"""豆包 Seedream 云端图生图冒烟脚本（P0 手工联调用；会真实计费）。

用途：不开网页直接验证「1 张素材 + 1 个姿态 → 1 张图」，记录耗时、request_id、
usage 与估算费用，产物落到 ``storage/poc/seedream_smoke/``。

前置：
1. `backend/.env` 配置 `ARK_ENABLED=true`、`ARK_API_KEY=...`；
2. 已安装依赖（httpx/Pillow 随主项目）。

用法（仓库根目录）：
    python scripts/oneoff/seedream_smoke.py --material-id <素材UUID> --pose-id full_sexy_P037_diag
    python scripts/oneoff/seedream_smoke.py --material-id <id> --pose-id <pose> --dry-run

退出码：0=成功或 dry-run；1=失败/未配置 key。日志不记录 API Key/base64。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
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
from app.services.seedream_client import (
    SeedreamClient,
    SeedreamError,
    build_prompt,
)

OUT_DIR = Path(settings.storage_root) / "poc" / "seedream_smoke"

SYSTEM_PROMPT = (
    "保持图1中人物的长相、发型、肤色和服装完全不变，让人物按图2的姿势重新拍摄。"
    "图2只作为姿势参考：只参考身体朝向、重心、四肢位置、手部动作和镜头视角；"
    "不要复制图2的线稿风格、灰色背景、关节球和人体模型外观。"
    "写实摄影，全身构图，自然光，皮肤与服装细节清晰，画面干净。"
)


def _read_material(material_id: str) -> tuple[Path, str]:
    """只读查素材路径与文件名（不写库）。"""
    db = Path(settings.db_file_path)
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        row = con.execute(
            "select file_path, media_type from inspirations where id=? and deleted_at is null",
            (material_id,),
        ).fetchone()
    finally:
        con.close()
    if not row:
        raise SystemExit(f"素材不存在或已删除：{material_id}")
    if row[1] != "image":
        raise SystemExit("素材必须是图片（一期云端不做视频首帧）")
    path = Path(settings.storage_root) / row[0]
    if not path.exists():
        raise SystemExit(f"素材文件缺失：{row[0]}")
    return path, row[0]


def _read_pose_prompt(pose_id: str) -> str:
    db = Path(settings.db_file_path)
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        row = con.execute("select prompt from pose_prompts where pose_id=?", (pose_id,)).fetchone()
    finally:
        con.close()
    return str(row[0]).strip() if row else ""


def _data_uri(data: bytes) -> str:
    """给 dry-run 展示用：只算体积，不打印内容。"""
    return f"data:image;base64,<{len(base64.b64encode(data))} chars>"


async def main() -> int:
    parser = argparse.ArgumentParser(description="豆包 Seedream 云端图生图冒烟（会真实计费）")
    parser.add_argument("--material-id", required=True, help="素材 UUID（图片）")
    parser.add_argument("--pose-id", required=True, help="姿势 id")
    parser.add_argument("--size", default="1536x2048", help="1K/1.5K/2K 或 宽x高")
    parser.add_argument("--prompt-extra", default="", help="附加到姿态细节后的提示词")
    parser.add_argument("--out", default="", help="输出路径（默认 storage/poc/seedream_smoke）")
    parser.add_argument("--dry-run", action="store_true", help="只打印将发送的内容与预估费用，不调用")
    args = parser.parse_args()

    material_path, material_rel = _read_material(args.material_id)
    pose = await pose_gallery.get_pose(args.pose_id)
    pose_prompt = _read_pose_prompt(args.pose_id) or f"{pose.get('name')}（{pose.get('angle')}）"
    prompt = build_prompt(f"{SYSTEM_PROMPT}\n姿势细节：{pose_prompt}{args.prompt_extra}", None)
    pose_path = await pose_gallery.ensure_pose_image(args.pose_id)
    sys.path.insert(0, str(ROOT))
    from app.services.task_runners.image_gen import _prepare_ark_image

    material = _prepare_ark_image(material_path.read_bytes(), output_format="jpeg")
    pose_img = _prepare_ark_image(pose_path.read_bytes(), output_format="png")
    cost = 0.12
    print(
        json.dumps(
            {
                "material": material_rel,
                "pose_id": args.pose_id,
                "size": args.size,
                "material_bytes": len(material),
                "pose_bytes": len(pose_img),
                "prompt": prompt,
                "estimated_cost_cny": cost,
                "ark_enabled": settings.ark_enabled,
                "key_set": bool(settings.ark_api_key),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if args.dry_run:
        print("[dry-run] 未调用云端；数据 URI 体积示意：", _data_uri(material))
        return 0
    if not settings.ark_enabled or not settings.ark_api_key:
        raise SystemExit("云端未启用或 ARK_API_KEY 为空：先在 backend/.env 配置并重启后端")

    out_dir = Path(args.out) if args.out else OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    client = SeedreamClient(settings.ark_api_key)
    started = time.monotonic()
    try:
        result = await client.generate(
            prompt=prompt,
            images=[material, pose_img],
            size=args.size,
            output_format="png",
            watermark=False,
        )
    except SeedreamError as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 1
    finally:
        await client.aclose()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_file = out_dir / f"{stamp}_{args.pose_id}.png"
    out_file.write_bytes(result.image)
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(out_file),
                "elapsed_s": result.elapsed_s,
                "wall_s": round(time.monotonic() - started, 2),
                "request_id": result.request_id,
                "usage": result.usage,
                "size": result.size,
                "cost_cny": cost,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
