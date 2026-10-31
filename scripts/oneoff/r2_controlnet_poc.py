"""R2 PoC：SDXL + ControlNet(lineart/union) 换姿势 —— 最小可用性验证。

目的（见 docs/R2（SDXL+ControlNet lineart）PoC成本与代价评估.md）：
用下载好的 SDXL 基座 + xinsir ControlNet-Union（lineart 类型）验证「姿势线稿当控制信号」
能不能把姿势带准，以及三种接入方式哪个可用：

- txt2img + CN：只靠线稿控制姿势（身份必然丢，用来验证「姿势能不能准」）；
- img2img(0.7) + CN：以主体照片为底图 + 线稿控制（尝试姿势与身份折中）；
- img2img(0.85) + CN：更强的姿势控制、更弱的身份保留；
- 控制图预处理：姿势线稿 → 纯黑白阈值图（关节圆圈也会被保留，属于要观察的问题）。

用法（默认 dry-run，只打印工作流，不连 ComfyUI）：
    python scripts/oneoff/r2_controlnet_poc.py --dry-run
    python scripts/oneoff/r2_controlnet_poc.py --execute

产物：``<storage_root>/poc/r2_controlnet_<日期>/``（PNG + summary.json，gitignore 覆盖，不入库）。
**注意**：SDXL 的 CLIP 对中文提示词很弱，本脚本用英文姿势描述（PoC 够用；产品化需要把
1439 条中文姿势名翻译成英文，成本需另算）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import settings
from app.services import pose_gallery
from app.services.comfyui.client import ComfyUIClient
from PIL import Image

SDXL_CKPT = "sd_xl_base_1.0.safetensors"
CONTROLNET = "controlnet-union-sdxl-1.0-promax.safetensors"
UNION_LINEART = "canny/lineart/anime_lineart/mlsd"

# 中文姿势名 → 英文提示词（SDXL CLIP 只认英文；产品化需要批量翻译，先把 PoC 跑通）
POSE_EN: dict[str, str] = {
    "full_sexy_P001_side": (
        "S-shaped standing pose, hip pushed to the side, one hand behind the head, "
        "slender waist, side view"
    ),
    "full_sexy_P006_diag": (
        "standing and leaning forward with both hands on the wall, hips pushed back, "
        "s-curve body, diagonal view"
    ),
    "full_single_P020_high": (
        "standing straight, one hand touching the back of the head, looking up, high angle view"
    ),
    "full_single_P001_front": (
        "sitting on the floor leaning back, one knee raised, hands behind for support, front view"
    ),
    "full_single_P015_front": (
        "standing in contrapposto pose, one hand on the hip, weight on one leg, front view"
    ),
    "full_sexy_P016_front": (
        "sitting sideways on the floor, one hand supporting the body, body gently arched, front view"
    ),
    "full_sexy_P046_front": (
        "lying on one side, waist line emphasized, one leg bent, front view"
    ),
    "full_single_P031_front": (
        "kneeling on both knees, hands resting on the thighs, front view"
    ),
    "full_sexy_P066_front": (
        "bridge pose, hips lifted high, knees apart, front view"
    ),
    "upper80v2_P002_front": (
        "hands forming a heart shape above the head, front view"
    ),
}

NEGATIVE = (
    "lowres, blurry, bad anatomy, bad hands, extra limbs, deformed, disfigured, "
    "mannequin, joint circles, line art, sketch, cartoon, anime, 3d render, "
    "doll, text, watermark, signature"
)

CONTROL_STRENGTH = 1.0
LATENT_W, LATENT_H = 896, 1152  # 3:4，贴近姿势线稿 900×1200；SDXL 友好（均为 64 的倍数）
BASE_PROMPT = (
    "photo of a beautiful young asian woman, full body, slim body, long hair, "
    "fashion clothing, standing, realistic photography, highly detailed, sharp focus, "
    "natural lighting, 35mm photo"
)


def build_workflow(
    *,
    prompt: str,
    seed: int,
    steps: int,
    cfg: float,
    control_image: str,
    subject_image: str | None,
    denoise: float,
    prefix: str,
    control_type: str = UNION_LINEART,
    control_strength: float = CONTROL_STRENGTH,
    control_start: float = 0.0,
    control_end: float = 1.0,
) -> dict[str, Any]:
    """构造 SDXL + ControlNet 工作流（txt2img 或 img2img）。"""
    latent: list[Any] = ["13", 0]  # EmptyLatentImage
    wf: dict[str, Any] = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": SDXL_CKPT}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": NEGATIVE, "clip": ["1", 1]}},
        "4": {"class_type": "LoadImage", "inputs": {"image": control_image}},
        "5": {
            "class_type": "ControlNetLoader",
            "inputs": {"control_net_name": CONTROLNET},
        },
        "6": {
            "class_type": "SetUnionControlNetType",
            "inputs": {"control_net": ["5", 0], "type": control_type},
        },
        "7": {
            "class_type": "ControlNetApplyAdvanced",
            "inputs": {
                "positive": ["2", 0],
                "negative": ["3", 0],
                "control_net": ["6", 0],
                "image": ["4", 0],
                "strength": control_strength,
                "start_percent": control_start,
                "end_percent": control_end,
            },
        },
        "13": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": LATENT_W, "height": LATENT_H, "batch_size": 1},
        },
        "9": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": ["7", 0],
                "negative": ["7", 1],
                "latent_image": latent,
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "dpmpp_2m",
                "scheduler": "karras",
                "denoise": denoise,
            },
        },
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["1", 2]}},
        "11": {"class_type": "SaveImage", "inputs": {"images": ["10", 0], "filename_prefix": prefix}},
    }
    if subject_image:
        wf["12"] = {"class_type": "LoadImage", "inputs": {"image": subject_image}}
        wf["14"] = {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["12", 0], "vae": ["1", 2]},
        }
        wf["9"]["inputs"]["latent_image"] = ["14", 0]
    return wf


def prepare_control(pose_path: Path, out_path: Path, threshold: int = 200) -> None:
    """姿势线稿 → 纯黑白控制图（灰阶阴影统一压成黑线，避免边缘判定不稳定）。"""
    with Image.open(pose_path) as img:
        gray = img.convert("L")
        mask = gray.point(lambda p: 0 if p < threshold else 255)
        mask.save(out_path)


def prepare_subject(subject_path: Path, out_path: Path) -> None:
    """主体图裁成 SDXL 友好尺寸（3:4），避免 VAE 尺寸不整除报错。"""
    with Image.open(subject_path) as img:
        img.convert("RGB").resize((LATENT_W, LATENT_H), Image.Resampling.LANCZOS).save(out_path)


def _pick_image(entry: dict) -> dict | None:
    for output in (entry.get("outputs") or {}).values():
        images = output.get("images") or []
        if images:
            return images[0]
    return None


VARIANTS = (
    ("txt2img", 1.0, None),
    ("img2img_070", 0.7, None),
    ("img2img_085", 0.85, None),
)


async def run(args: argparse.Namespace) -> int:
    poses = [p.strip() for p in args.poses.split(",") if p.strip()]
    unknown = [p for p in poses if p not in POSE_EN]
    if unknown:
        raise SystemExit(f"这些姿势没有英文提示词映射：{unknown}")
    out_dir = Path(args.out_dir) if args.out_dir else (
        settings.storage_root / "poc" / f"r2_controlnet_{time.strftime('%Y%m%d')}"
    )
    control_dir = out_dir / "control"
    out_dir.mkdir(parents=True, exist_ok=True)
    control_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"SDXL: {SDXL_CKPT} | ControlNet: {CONTROLNET} | "
        f"union type: {args.control_type}（strength={args.strength}, "
        f"{args.control_start}~{args.control_end}）"
    )
    print(f"输出目录：{out_dir}")

    pose_paths = {pid: await pose_gallery.ensure_pose_image(pid) for pid in poses}
    control_names: dict[str, str] = {}
    subject_path: Path | None = None
    subject_name: str | None = None
    if args.subject_id:
        import sqlite3

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
            raise SystemExit(f"主体素材不存在：{args.subject_id}")
        subject_path = settings.storage_root / str(row[0])
        resized = out_dir / "subject_input.png"
        prepare_subject(subject_path, resized)
        subject_path = resized
        subject_name = "r2_poc_subject.png"

    for pid, path in pose_paths.items():
        target = control_dir / f"control_{pid}.png"
        if args.control_dir:
            src = Path(args.control_dir) / f"{pid}.png"
            if not src.exists():
                raise SystemExit(f"预生成控制图不存在：{src}")
            target.write_bytes(src.read_bytes())
        else:
            prepare_control(path, target)
        control_names[pid] = f"r2_poc_control_{pid}.png"
        print(f"控制图：{target}（{target.stat().st_size} 字节）")

    if args.dry_run:
        wf = build_workflow(
            prompt=f"{BASE_PROMPT}, {POSE_EN[poses[0]]}",
            seed=args.seed,
            steps=args.steps,
            cfg=args.cfg,
            control_image=control_names[poses[0]],
            subject_image=subject_name,
            denoise=0.7,
            prefix="r2_poc_dry",
        )
        print("\n[dry-run] txt2img 工作流节点：")
        for nid, node in wf.items():
            print(f"  {nid}: {node['class_type']}")
        print("加 --execute 才会真正提交 ComfyUI。")
        return 0

    client = ComfyUIClient(settings.comfyui_base_url, timeout=settings.comfyui_timeout)
    records: list[dict[str, Any]] = []
    try:
        variants = []
        if subject_name is None or args.allow_txt2img:
            variants.append(VARIANTS[0])
        if subject_name is not None:
            variants.extend(VARIANTS[1:])

        for pid in poses:
            # 控制图用阈值化版本上传（原始姿势图不直接进 ComfyUI）
            ctrl_bytes = await asyncio.to_thread((control_dir / f"control_{pid}.png").read_bytes)
            uploaded_ctrl = await client.upload_image(ctrl_bytes, control_names[pid])
            uploaded_subject = None
            if subject_path and subject_name:
                sub_bytes = await asyncio.to_thread(subject_path.read_bytes)
                uploaded_subject = await client.upload_image(sub_bytes, subject_name)

            for variant, denoise, _ in variants:
                prompt = f"{BASE_PROMPT}, {POSE_EN[pid]}"
                if args.prompt_suffix:
                    prompt = f"{prompt}, {args.prompt_suffix}"
                wf = build_workflow(
                    prompt=prompt,
                    seed=args.seed,
                    steps=args.steps,
                    cfg=args.cfg,
                    control_image=uploaded_ctrl,
                    subject_image=uploaded_subject,
                    denoise=denoise,
                    prefix=f"r2_poc_{pid}_{variant}",
                    control_type={
                        "lineart": UNION_LINEART,
                        "hed": "hed/pidi/scribble/ted",
                        "depth": "depth",
                    }[args.control_type],
                    control_strength=args.strength,
                    control_start=args.control_start,
                    control_end=args.control_end,
                )
                started = time.monotonic()
                prompt_id = await client.submit(wf)
                entry = await client.wait_for_prompt(prompt_id, timeout=args.timeout)
                image = _pick_image(entry)
                if image is None:
                    print(f"[失败] {pid} × {variant}：ComfyUI 未返回图片")
                    continue
                raw = await client.fetch_image(
                    image.get("filename") or "", image.get("subfolder") or ""
                )
                target = out_dir / f"{pid}__{variant}.png"
                target.write_bytes(raw)
                elapsed = round(time.monotonic() - started, 1)
                records.append(
                    {
                        "pose_id": pid,
                        "variant": variant,
                        "denoise": denoise,
                        "prompt": prompt,
                        "seed": args.seed,
                        "image": target.name,
                        "elapsed_s": elapsed,
                    }
                )
                print(f"[OK] {pid} × {variant} -> {target.name}（{elapsed}s）")
    finally:
        await client.aclose()

    summary = out_dir / "summary.json"
    summary.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完成 {len(records)} 张；summary: {summary}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="R2 PoC：SDXL + ControlNet-Union(lineart) 换姿势")
    parser.add_argument(
        "--poses", default="full_sexy_P001_side,full_sexy_P006_diag", help="姿势 id，逗号分隔"
    )
    parser.add_argument("--subject-id", default="", help="img2img 用的主体素材 id（留空则只跑 txt2img）")
    parser.add_argument("--steps", type=int, default=30)
    parser.add_argument("--cfg", type=float, default=7.0)
    parser.add_argument("--seed", type=int, default=1001)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--out-dir", default="")
    parser.add_argument(
        "--control-type",
        default="lineart",
        choices=["lineart", "hed", "depth"],
        help="union 控制类型：lineart / hed / depth",
    )
    parser.add_argument(
        "--control-dir",
        default="",
        help="预生成控制图目录（文件名 <pose_id>.png，如 depth 图）；不给则用线稿阈值图",
    )
    parser.add_argument("--control-start", type=float, default=0.0)
    parser.add_argument("--control-end", type=float, default=1.0)
    parser.add_argument("--strength", type=float, default=1.0, help="ControlNet 强度")
    parser.add_argument(
        "--prompt-suffix", default="", help="追加到正向提示词末尾（可试抑制关节圆护具）"
    )
    parser.add_argument(
        "--allow-txt2img", action="store_true", help="额外跑 txt2img（身份会丢，仅用于验证姿势）"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", default=True)
    group.add_argument("--execute", dest="dry_run", action="store_false")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
