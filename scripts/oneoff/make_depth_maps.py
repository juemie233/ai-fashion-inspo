"""把图库姿势线稿转成深度图（Depth Anything V2 Small ONNX），供 SDXL depth ControlNet 使用。

为什么需要：R2-A 把线稿直接接 lineart ControlNet，关节圆圈会被画成服装装饰/护具；
深度图只保留「轮廓 + 前后关系」，社区（MOSS/Krea2）用同一思路并靠降低深度强度/提前结束
来减少素体外观。本脚本离线批量生成深度图，不依赖 ComfyUI 自定义节点。

用法（需要单独 venv：onnxruntime + numpy + pillow）：
    C:/Users/Administrator/AppData/Local/Temp/davenv/Scripts/python.exe \
        scripts/oneoff/make_depth_maps.py --limit 10
产物：`<storage_root>/poc/r2_depth_maps/<pose_id>.png`（900×1200 灰度，近白远黑）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
STORAGE_ROOT = Path(r"G:/fashion-inspo-storage")  # 可用 --storage-root 覆盖
POSE_DIR = STORAGE_ROOT / "pose_gallery" / "images"
BATCH_DIR = STORAGE_ROOT / "poc" / "r2_batch10_20261030"
MODEL = Path(r"D:/AI/ComfyUI/ComfyUI/models/depthanything/depth_anything_v2_small.onnx")
INPUT_SIZE = 518  # Depth Anything V2 small 的固定输入

mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
std = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def infer(session, image: Image.Image) -> Image.Image:
    """单张图 → 深度图（PIL 灰度，尺寸与原图一致）。"""
    w, h = image.size
    rgb = image.convert("RGB").resize((INPUT_SIZE, INPUT_SIZE), Image.Resampling.BICUBIC)
    arr = np.asarray(rgb, dtype=np.float32) / 255.0
    arr = (arr - mean) / std
    tensor = arr.transpose(2, 0, 1)[None].astype(np.float32)
    out = session.run(None, {session.get_inputs()[0].name: tensor})[0]
    depth = np.squeeze(out).astype(np.float32)
    lo, hi = float(depth.min()), float(depth.max())
    if hi - lo < 1e-6:
        depth = np.zeros_like(depth)
    else:
        depth = (depth - lo) / (hi - lo)
    # 源图是线稿，人体比背景「近」；DepthAnything 输出大=近，直接映射成白=近
    gray = (depth * 255.0).astype(np.uint8)
    return Image.fromarray(gray).resize((w, h), Image.Resampling.BICUBIC)


async def run(args: argparse.Namespace) -> int:
    import onnxruntime as ort

    session = ort.InferenceSession(str(MODEL), providers=["CPUExecutionProvider"])
    storage = Path(args.storage_root)
    batch_dir = storage / "poc" / "r2_batch10_20261030"
    rows = json.loads((batch_dir / "summary.json").read_text(encoding="utf-8"))
    if args.limit:
        rows = rows[: args.limit]
    out_dir = Path(args.out_dir) if args.out_dir else (storage / "poc" / "r2_depth_maps")
    out_dir.mkdir(parents=True, exist_ok=True)
    for row in rows:
        pose_id = row["pose_id"]
        src = storage / "pose_gallery" / "images" / f"{pose_id}.png"
        if not src.exists():
            raise SystemExit(f"姿势图缓存不存在：{src}")
        with Image.open(src) as img:
            depth = infer(session, img)
        target = out_dir / f"{pose_id}.png"
        depth.save(target)
        print(f"[OK] {pose_id} -> {target.name}（{target.stat().st_size} 字节）")
    print("完成", len(rows), "张深度图；输出：", out_dir)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="图库姿势线稿 → 深度图（Depth Anything V2 Small）")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--storage-root", default=str(STORAGE_ROOT))
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
