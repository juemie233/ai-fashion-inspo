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
    "真实照片质感、全身照。"
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
        for path in names:
            data = await asyncio.to_thread(path.read_bytes)
            stage_name = await client.upload_image(data, f"r2s2_{path.name}")
            if args.ref_order == "pose-first":
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
