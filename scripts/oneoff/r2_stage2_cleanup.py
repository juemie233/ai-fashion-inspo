"""R2 第二阶段：用 Qwen-Image-Edit 把「SDXL+ControlNet 出的姿势图」清洗成可用成图。

为什么需要：R2-A（SDXL + lineart ControlNet）姿势很准，但把素体线稿的关节圆圈
渲染成了护具/机甲元素，且身份丢失。第二阶段以 [主体照片, 第一阶段图] 为参考，
让 Qwen-Image-Edit 保姿势、换回主体身份、去掉护具、改成日常服装。

用法：
    python scripts/oneoff/r2_stage2_cleanup.py --dry-run
    python scripts/oneoff/r2_stage2_cleanup.py --execute
产物：`<storage_root>/poc/r2_stage2_<日期>/`（storage，gitignore，不入库）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import settings
from app.services.comfyui.client import ComfyUIClient
from app.services.comfyui.workflow import (
    build_edit_workflow,
    load_template,
)

SUBJECT_ID = "9be0a4e5-f4cb-44b5-a120-781adfc85dbf"

PROMPT_SUBJECT_FIRST = (
    "第 1 张是人物照片，第 2 张是同一个人物的姿势参考图（姿势正确，但服装上带有圆形护具/"
    "机甲元素）。请保持第 2 张图的姿势与构图，把人物换成第 1 张图的人（长相、发型、肤色一致），"
    "并去掉身上的圆形护具、关节垫片与机甲质感，换成日常黑色修身连衣裙与黑色丝袜，"
    "真实照片质感、全身照。"
)
PROMPT_POSE_FIRST = (
    "第 1 张是姿势图（姿势正确，但服装上有圆形护具/机甲元素），第 2 张是人物照片。"
    "请保持第 1 张图的姿势与构图不变，把人物换成第 2 张图的人（长相、发型、肤色一致），"
    "并去掉身上的圆形护具、关节垫片与机甲质感，换成日常黑色修身连衣裙与黑色丝袜，"
    "真实照片质感、清晰自然、全身照。"
)
PROMPT_POSE_FIRST_FACE = (
    "第 1 张是姿势图（姿势正确，但服装上有圆形护具/机甲元素），第 2 张是人物全身照，"
    "第 3 张是同一人物的脸部特写。请保持第 1 张图的姿势与构图不变，把人物换成第 2/3 张里的人："
    "**脸型、五官、发型严格按第 3 张**，身材与穿着按第 2 张；去掉圆形护具、关节垫片与机甲质感，"
    "换成日常黑色修身连衣裙与黑色丝袜；真实照片质感、清晰自然、全身照。"
)
NEGATIVE = "圆形护具, 护膝, 肩甲, 机甲, 线稿, 素体, 夸张比例, 多肢, 畸形手"

GEN = {
    "RESOLUTION": 1024,
    "STEPS": 25,
    "CFG": 1.0,
    "SAMPLER": "res_multistep",
    "SCHEDULER": "simple",
    "SHIFT": 3.0,
    "SEED": 1001,
}


def _face_crop(subject_path: Path, out_path: Path) -> bool:
    """用 OpenCV Haar 检测主体人脸并裁出带发型的特写（给 Qwen 第三张参考用）。

    Qwen 编辑在「全身照参考」里人脸像素太少，identity 保不住；单独给一张脸部特写
    是零下载、最便宜的身份增强手段（先验证是否有效，再决定是否上 IP-Adapter）。
    """
    import cv2

    img = cv2.imread(str(subject_path))
    if img is None:
        return False
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )
    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60))
    if len(faces) == 0:
        return False
    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
    # 扩到 1.9 倍，带进头发/脖子；再裁 1:1 并缩到 512
    cx, cy = x + w // 2, y + h // 2
    size = int(max(w, h) * 1.9)
    left = max(0, cx - size // 2)
    top = max(0, cy - size // 2)
    crop = img[top : top + size, left : left + size]
    if crop.size == 0:
        return False

    side = min(crop.shape[:2])
    crop = crop[:side, :side]
    crop = cv2.resize(crop, (512, 512), interpolation=cv2.INTER_LANCZOS4)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), crop)
    return True


def _pick_image(entry: dict) -> dict | None:
    for output in (entry.get("outputs") or {}).values():
        images = output.get("images") or []
        if images:
            return images[0]
    return None


async def run(args: argparse.Namespace) -> int:
    db = Path(str(settings.database_url).split(":///", 1)[1])
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        row = con.execute(
            "select file_path from inspirations where id=? and deleted_at is null",
            (args.subject_id,),
        ).fetchone()
    finally:
        con.close()
    if not row:
        raise SystemExit("主体素材不存在")
    subject_path = settings.storage_root / str(row[0])
    stage1_dir = Path(args.stage1_dir)
    names = sorted(Path(stage1_dir).glob("*.png"))
    if args.stage1_glob:
        import fnmatch

        names = [p for p in names if fnmatch.fnmatch(p.name, args.stage1_glob)]
    if not names:
        raise SystemExit(f"{stage1_dir} 下没有匹配的 PNG")

    out_dir = Path(args.out_dir) if args.out_dir else (
        settings.storage_root / "poc" / f"r2_stage2_{time.strftime('%Y%m%d')}"
    )
    print("阶段 1 输入：", [p.name for p in names], "| 参考顺序:", args.ref_order)
    print("输出目录：", out_dir)
    if args.dry_run:
        print("[dry-run] 不上传/不生成；加 --execute 才会提交 ComfyUI")
        return 0

    template, digest = load_template("qwen_edit")
    print("qwen_edit 模板 sha:", digest)
    client = ComfyUIClient(settings.comfyui_base_url, timeout=settings.comfyui_timeout)
    out_dir.mkdir(parents=True, exist_ok=True)
    records = []
    try:
        subject_data = await asyncio.to_thread(subject_path.read_bytes)
        suffix = subject_path.suffix or ".png"
        subject_name = await client.upload_image(subject_data, f"r2s2_subject{suffix}")
        face_name = None
        if args.face_ref:
            face_path = out_dir / "face_ref.png"
            if _face_crop(subject_path, face_path):
                face_data = await asyncio.to_thread(face_path.read_bytes)
                face_name = await client.upload_image(face_data, "r2s2_face.png")
                print("人脸参考已生成：", face_path)
            else:
                print("未检测到人脸，降级为两图参考")
        for path in names:
            data = await asyncio.to_thread(path.read_bytes)
            stage_name = await client.upload_image(data, f"r2s2_{path.name}")
            if args.face_ref and face_name:
                reference_names = [stage_name, subject_name, face_name]
                prompt = PROMPT_POSE_FIRST_FACE
            elif args.ref_order == "pose-first":
                reference_names = [stage_name, subject_name]
                prompt = PROMPT_POSE_FIRST
            else:
                reference_names = [subject_name, stage_name]
                prompt = PROMPT_SUBJECT_FIRST
            wf = build_edit_workflow(
                template,
                reference_names=reference_names,
                PROMPT=prompt,
                NEGATIVE=NEGATIVE,
                PREFIX=f"r2s2_{path.stem}",
                **GEN,
            )
            started = time.monotonic()
            prompt_id = await client.submit(wf)
            entry = await client.wait_for_prompt(prompt_id, timeout=args.timeout)
            image = _pick_image(entry)
            if image is None:
                print("[失败]", path.name)
                continue
            raw = await client.fetch_image(
                image.get("filename") or "", image.get("subfolder") or ""
            )
            target = out_dir / f"{path.stem}__stage2.png"
            target.write_bytes(raw)
            elapsed = round(time.monotonic() - started, 1)
            records.append(
                {
                    "stage1": path.name,
                    "stage2": target.name,
                    "prompt": prompt,
                    "elapsed_s": elapsed,
                }
            )
            print(f"[OK] {path.name} -> {target.name}（{elapsed}s）")
    finally:
        await client.aclose()
    summary = out_dir / "summary.json"
    summary.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print("完成", len(records), "张；summary:", summary)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="R2 第二阶段：Qwen-Edit 清洗姿势图")
    parser.add_argument(
        "--stage1-dir",
        default=str(settings.storage_root / "poc" / "r2_controlnet_20261030"),
        help="第一阶段产出的目录",
    )
    parser.add_argument("--stage1-glob", default="*__txt2img.png", help="只处理匹配的文件")
    parser.add_argument("--subject-id", default=SUBJECT_ID)
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument(
        "--face-ref", action="store_true", help="额外上传主体人脸特写作为第 3 张参考（增强身份）"
    )
    parser.add_argument(
        "--ref-order",
        default="subject-first",
        choices=["subject-first", "pose-first"],
        help="参考图顺序：Qwen 第 1 张像编辑底图，pose-first 才能保住第一阶段姿势",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", default=True)
    group.add_argument("--execute", dest="dry_run", action="store_false")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
