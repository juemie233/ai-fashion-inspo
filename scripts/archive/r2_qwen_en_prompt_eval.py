"""P0 实验：Qwen-Image 2.1 + 社区英文约束提示词（先不挂 Pose Studio LoRA）。

来源（已调研）：wangweihong/comfyui-pose-gallery 的 Qwen 2.1 工作流——
图 1 = 图库姿势线稿、图 2 = 人物参考，用一段「image1 只作姿势参考，保留 image2 身份/服装/发型/背景，
不要复制线稿/素体外观」的英文提示词；社区另挂 VNCCS Pose Studio QI2.1 v2 LoRA（152 MB）。

本脚本先验证 **零下载** 部分：只改提示词与图序（图1=姿势、图2=人物），跑我们的 Qwen 2.1 栈。
两个变体：
- `lineart`：图 1 = 图库原始线稿（社区同款输入）；
- `stage1` ：图 1 = 我们 R2 阶段 1（SDXL+ControlNet）的姿势图（更接近真实照片，但带素体伪影）。

用法：
    python scripts/oneoff/r2_qwen_en_prompt_eval.py --dry-run
    python scripts/oneoff/r2_qwen_en_prompt_eval.py --execute --limit 2
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
from app.services import pose_gallery
from app.services.comfyui.client import ComfyUIClient
from app.services.comfyui.workflow import (
    build_edit_workflow,
    load_template,
)

UC_UNET = "qwen-image-2.1-UC-int8_convrot.safetensors"
POSE_LORA = r"qwen2.1\VNCCS_QI2_PoseStudioV2.safetensors"
BATCH_DIR = settings.storage_root / "poc" / "r2_batch10_20261030"

# 社区实测用的约束提示词（逐字保留，便于对照）
PROMPT_EN = (
    "Replace the pose of the person in image2 with the pose shown in image1. "
    "Use image1 only as a reference for body pose, limb placement, body orientation and camera viewpoint. "
    "Preserve the identity, facial features, hairstyle, clothing, accessories, colors, visual style, "
    "background and lighting of image2. Reproduce the reference pose as closely as possible with natural "
    "anatomy and coherent proportions. Render the person in the visual style of image2; do not copy the "
    "line drawing, mannequin appearance or annotations from image1."
)


def _pick_image(entry: dict) -> dict | None:
    for output in (entry.get("outputs") or {}).values():
        images = output.get("images") or []
        if images:
            return images[0]
    return None


def _subject_path(subject_id: str) -> Path:
    db = Path(str(settings.database_url).split(":///", 1)[1])
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        row = con.execute(
            "select file_path from inspirations where id=? and deleted_at is null",
            (subject_id,),
        ).fetchone()
    finally:
        con.close()
    if not row:
        raise SystemExit(f"素材不存在：{subject_id}")
    return settings.storage_root / str(row[0])


def _to_png(src: Path, dst: Path) -> None:
    """转成 PNG 上传（保持原始比例：Qwen 的 latent 尺寸取第一张参考图，压成方图会破坏姿势）。"""
    from PIL import Image

    with Image.open(src) as img:
        img.convert("RGB").save(dst)


async def run(args: argparse.Namespace) -> int:
    rows = json.loads((BATCH_DIR / "summary.json").read_text(encoding="utf-8"))
    if args.limit:
        rows = rows[: args.limit]
    out_dir = Path(args.out_dir) if args.out_dir else (
        settings.storage_root / "poc" / f"r2_qwen_en_{time.strftime('%Y%m%d')}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    template, digest = load_template("qwen_edit")
    current = template["1"]["inputs"].get("unet_name")
    template["1"]["inputs"]["unet_name"] = UC_UNET if args.unet == "uc" else current
    if args.lora:
        template["15"] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "model": ["1", 0],
                "lora_name": POSE_LORA,
                "strength_model": args.lora_strength,
            },
        }
        template["5"]["inputs"]["model"] = ["15", 0]
    print(
        f"模板 qwen_edit={digest}；UNET: {current if args.unet == 'std' else UC_UNET}；"
        f"LoRA: {POSE_LORA if args.lora else '关闭'}"
    )
    print(f"样本 {len(rows)}；变体 {args.variants}；输出 {out_dir}；sampler={args.sampler}")
    if args.dry_run:
        print("[dry-run] 不提交 ComfyUI")
        return 0

    client = ComfyUIClient(settings.comfyui_base_url, timeout=settings.comfyui_timeout)
    records: list[dict] = []
    wanted = [v.strip() for v in args.variants.split(",") if v.strip()]
    try:
        for row in rows:
            idx = int(row["idx"])
            sample_dir = out_dir / f"s{idx:02d}"
            sample_dir.mkdir(parents=True, exist_ok=True)
            subject = _subject_path(row["subject_id"])
            subject_png = sample_dir / "subject_input.png"
            _to_png(subject, subject_png)
            subject_name = await client.upload_image(
                subject_png.read_bytes(), f"en_s{idx:02d}_subject.png"
            )
            pose_path = await pose_gallery.ensure_pose_image(row["pose_id"])
            pose_png = sample_dir / "pose_input.png"
            _to_png(pose_path, pose_png)
            pose_name = await client.upload_image(
                pose_png.read_bytes(), f"en_s{idx:02d}_pose.png"
            )
            stage1_path = BATCH_DIR / f"s{idx:02d}" / "stage1.png"
            stage1_name = None
            if stage1_path.exists():
                stage1_png = sample_dir / "stage1_input.png"
                _to_png(stage1_path, stage1_png)
                stage1_name = await client.upload_image(
                    stage1_png.read_bytes(), f"en_s{idx:02d}_stage1.png"
                )

            refs = {
                "lineart": [pose_name, subject_name],
                "stage1": [stage1_name, subject_name] if stage1_name else None,
            }
            for variant in wanted:
                if refs.get(variant) is None:
                    print(f"[跳过] s{idx:02d} {variant} 缺输入")
                    continue
                wf = build_edit_workflow(
                    template,
                    reference_names=refs[variant],
                    PROMPT=PROMPT_EN,
                    NEGATIVE="",
                    PREFIX=f"en_s{idx:02d}_{variant}",
                    RESOLUTION=1024,
                    STEPS=args.steps,
                    CFG=1.0,
                    SAMPLER=args.sampler,
                    SCHEDULER="simple",
                    SHIFT=3.0,
                    SEED=args.seed + idx,
                )
                started = time.monotonic()
                pid = await client.submit(wf)
                entry = await client.wait_for_prompt(pid, timeout=args.timeout)
                image = _pick_image(entry)
                if image is None:
                    print(f"[失败] s{idx:02d} {variant}")
                    continue
                raw = await client.fetch_image(
                    image.get("filename") or "", image.get("subfolder") or ""
                )
                target = sample_dir / f"{variant}.png"
                target.write_bytes(raw)
                elapsed = round(time.monotonic() - started, 1)
                records.append(
                    {
                        "idx": idx,
                        "subject_id": row["subject_id"],
                        "pose_id": row["pose_id"],
                        "variant": variant,
                        "image": str(target.relative_to(out_dir)),
                        "elapsed_s": elapsed,
                        "prompt": PROMPT_EN,
                        "unet": UC_UNET if args.unet == "uc" else "std",
                    }
                )
                print(f"[OK] s{idx:02d} {variant} -> {target.name}（{elapsed}s）")
    finally:
        await client.aclose()

    summary = out_dir / "summary.json"
    summary.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完成 {len(records)} 张；summary: {summary}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="P0：Qwen2.1 + 社区英文约束 prompt（零下载）")
    parser.add_argument("--variants", default="lineart,stage1")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--unet", choices=["uc", "std"], default="uc")
    parser.add_argument("--steps", type=int, default=25)
    parser.add_argument("--sampler", default="euler")
    parser.add_argument("--seed", type=int, default=20261029)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--lora", action="store_true", help="挂 VNCCS Pose Studio QI2.1 v2 LoRA")
    parser.add_argument("--lora-strength", type=float, default=1.0)
    parser.add_argument("--out-dir", default="")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", default=True)
    group.add_argument("--execute", dest="dry_run", action="store_false")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
