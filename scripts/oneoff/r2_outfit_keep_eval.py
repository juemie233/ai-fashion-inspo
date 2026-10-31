"""发型/服装保持实验：用 Qwen-Image 2.1 **未审查版** i2i 在「R2 目标姿势图」上重绘人物。

背景：R2 两段式的阶段2 提示词写死「换成黑色连衣裙」，导致发型/服装丢失（身份只剩脸）。
本实验不引入 Qwen3-VL 之类的描述模型，直接让 2.1 未审查版 i2i 按「第 2 张主体图」保持
发型/服装，只借第 1 张图的姿势与构图；并测试追加「服装细节裁切」第三张参考是否更稳。

两个变体（各 10 样本）：
- `keep2`：参考 [阶段2图, 主体图]，提示词明确「发型、服装严格按第 2 张，不要换装」；
- `crop3`：参考 [阶段2图, 主体图, 主体服装裁切]，提示词说明第 3 张是服装细节参考。

用法（默认 dry-run，只校验模型/参考图，不提交）：
    python scripts/oneoff/r2_outfit_keep_eval.py --dry-run
    python scripts/oneoff/r2_outfit_keep_eval.py --execute

产物：`<storage_root>/poc/r2_outfit_keep_<日期>/`（s01..s10 的 keep2/crop3 图 + summary.json）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from PIL import Image

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

UC_UNET = "qwen-image-2.1-UC-int8_convrot.safetensors"
BATCH_DIR = settings.storage_root / "poc" / "r2_batch10_20261030"
SUBJECT_ROOT = settings.storage_root

NEGATIVE = "护具, 机甲, 圆形护垫, 线稿, 素体, 畸形手, 多肢, 低质量, 模糊"

PROMPT_KEEP2 = (
    "第 1 张是目标姿势图（姿势正确，但人物是别人、服装带圆形护具/机甲元素），第 2 张是主体人物的照片。"
    "请保持第 1 张图的姿势与构图不变，把人物换成第 2 张图的人：**长相、发型、发色、服装、配饰严格按第 2 张**，"
    "不要换装、不要改发型；去掉第 1 张里的圆形护具、关节垫片与机甲质感；"
    "真实照片质感、清晰自然、全身照。"
)
PROMPT_CROP3 = (
    "第 1 张是目标姿势图（姿势正确，但人物是别人、服装带圆形护具/机甲元素），第 2 张是主体人物的全身照，"
    "第 3 张是主体人物的服装细节裁切。请保持第 1 张图的姿势与构图不变，把人物换成第 2 张图的人："
    "**长相、发型、发色严格按第 2 张；服装颜色/材质/款式严格按第 2、3 张**，不要换装；"
    "去掉第 1 张里的圆形护具、关节垫片与机甲质感；真实照片质感、清晰自然、全身照。"
)


def _prep_outfit_crop(subject_path: Path, out_path: Path) -> bool:
    """从主体图粗略裁出服装区域（中段竖条），给 Qwen 当第三张参考。"""
    with Image.open(subject_path) as img:
        w, h = img.size
        left = int(w * 0.22)
        right = int(w * 0.78)
        top = int(h * 0.20)
        bottom = int(h * 0.68)
        crop = img.convert("RGB").crop((left, top, right, bottom))
        if crop.width < 32 or crop.height < 32:
            return False
        out_path.parent.mkdir(parents=True, exist_ok=True)
        crop.save(out_path)
        return True


def _pick_image(entry: dict) -> dict | None:
    for output in (entry.get("outputs") or {}).values():
        images = output.get("images") or []
        if images:
            return images[0]
    return None


async def run(args: argparse.Namespace) -> int:
    rows = json.loads((BATCH_DIR / "summary.json").read_text(encoding="utf-8"))
    if args.limit:
        rows = rows[: args.limit]
    out_dir = Path(args.out_dir) if args.out_dir else (
        settings.storage_root / "poc" / f"r2_outfit_keep_{time.strftime('%Y%m%d')}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    template, digest = load_template("qwen_edit")
    current = template["1"]["inputs"].get("unet_name")
    template["1"]["inputs"]["unet_name"] = UC_UNET
    print(f"模板 qwen_edit={digest}；UNET: {current} -> {UC_UNET}")
    print(f"样本 {len(rows)} 个；输出：{out_dir}")
    if args.dry_run:
        print("[dry-run] 不提交 ComfyUI，加 --execute 才会生成。")
        return 0

    client = ComfyUIClient(settings.comfyui_base_url, timeout=settings.comfyui_timeout)
    records: list[dict] = []
    try:
        for row in rows:
            idx = int(row["idx"])
            sample = BATCH_DIR / f"s{idx:02d}"
            # 主体原图：summary 里只有 id，这里用批处理时保留的 subject.jpg（320px 缩略图）
            subject_path = sample / "subject.jpg"
            pose_path = sample / "stage2.png"
            if not subject_path.exists() or not pose_path.exists():
                print(f"[跳过] s{idx:02d} 缺输入")
                continue
            crop_path = out_dir / f"s{idx:02d}" / "outfit_crop.png"
            has_crop = _prep_outfit_crop(subject_path, crop_path)
            if not has_crop:
                print(f"[提示] s{idx:02d} 服装裁切失败，crop3 变体退化为 keep2")

            pose_name = await client.upload_image(
                pose_path.read_bytes(), f"ok_s{idx:02d}_pose.png"
            )
            subject_name = await client.upload_image(
                subject_path.read_bytes(), f"ok_s{idx:02d}_subject.jpg"
            )
            crop_name = None
            if has_crop:
                crop_name = await client.upload_image(
                    crop_path.read_bytes(), f"ok_s{idx:02d}_crop.png"
                )

            wanted = [v.strip() for v in args.variants.split(",") if v.strip()]
            variants = []
            if "keep2" in wanted:
                variants.append(("keep2", [pose_name, subject_name], PROMPT_KEEP2))
            if crop_name and "crop3" in wanted:
                variants.append(
                    ("crop3", [pose_name, subject_name, crop_name], PROMPT_CROP3)
                )

            sample_dir = out_dir / f"s{idx:02d}"
            for key, refs, prompt in variants:
                wf = build_edit_workflow(
                    template,
                    reference_names=refs,
                    PROMPT=prompt,
                    NEGATIVE=NEGATIVE,
                    PREFIX=f"ok_s{idx:02d}_{key}",
                    RESOLUTION=1024,
                    STEPS=args.steps,
                    CFG=1.0,
                    SAMPLER="res_multistep",
                    SCHEDULER="simple",
                    SHIFT=3.0,
                    SEED=args.seed + idx,
                )
                started = time.monotonic()
                pid = await client.submit(wf)
                entry = await client.wait_for_prompt(pid, timeout=args.timeout)
                image = _pick_image(entry)
                if image is None:
                    print(f"[失败] s{idx:02d} {key}")
                    continue
                raw = await client.fetch_image(
                    image.get("filename") or "", image.get("subfolder") or ""
                )
                target = sample_dir / f"{key}.png"
                target.write_bytes(raw)
                elapsed = round(time.monotonic() - started, 1)
                records.append(
                    {
                        "idx": idx,
                        "subject_id": row["subject_id"],
                        "pose_id": row["pose_id"],
                        "variant": key,
                        "image": str(target.relative_to(out_dir)),
                        "elapsed_s": elapsed,
                        "unet": UC_UNET,
                    }
                )
                print(f"[OK] s{idx:02d} {key} -> {target.name}（{elapsed}s）")
    finally:
        await client.aclose()

    summary = out_dir / "summary.json"
    summary.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完成 {len(records)} 张；summary: {summary}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="发型/服装保持实验（Qwen 2.1 未审查版 i2i）")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 个样本（调试用）")
    parser.add_argument(
        "--variants", default="keep2,crop3", help="变体：keep2 / crop3（逗号分隔）"
    )
    parser.add_argument("--steps", type=int, default=25)
    parser.add_argument("--seed", type=int, default=20261031)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--out-dir", default="")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", default=True)
    group.add_argument("--execute", dest="dry_run", action="store_false")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
