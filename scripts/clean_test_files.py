"""扫描并清理测试污染文件：上传测试曾误写入真实 backend/storage/ 的纯色小图。

判定条件（三重确认，避免误删真实素材）：
1. PIL 判定为纯色图（颜色数 == 1）且尺寸 <= 128x128（测试图片 64x64 纯色）
2. 真实数据库（backend/fashion_inspo.db）无该文件路径的记录
3. 修改时间为本次测试会话期间（扫描时通过 --since 传入 ISO 时间）

用法:
    python scripts/clean_test_files.py            # 扫描（只报告不删除）
    python scripts/clean_test_files.py --delete   # 扫描并删除
"""

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image

PROJECT_DIR = Path(__file__).resolve().parent.parent  # fashion-inspo 项目根
BACKEND_DIR = PROJECT_DIR / "backend"
STORAGE_DIR = BACKEND_DIR / "storage"
DB_PATH = BACKEND_DIR / "fashion_inspo.db"

# 测试上传使用的纯色（make_image 默认色 (200,100,50)）
TEST_COLORS = {(200, 100, 50)}


def is_test_file(path: Path) -> bool:
    """判定疑似测试文件：小尺寸 + 颜色极单一（JPEG 压缩噪声容忍 8 色内）+ 接近测试色。"""
    try:
        with Image.open(path) as img:
            img.load()
            if img.width > 128 or img.height > 128:
                return False
            small = img.convert("RGB").resize((16, 16))
            colors = small.getcolors(maxcolors=100000)
            if colors is None or len(colors) > 8:
                return False
            # 平均色接近测试色（200,100,50）
            px = list(small.getdata())
            avg = tuple(sum(c[i] for c in px) // len(px) for i in range(3))
            return all(abs(a - b) <= 40 for a, b in zip(avg, (200, 100, 50)))
    except Exception:
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delete", action="store_true", help="确认删除匹配文件")
    parser.add_argument(
        "--since",
        default="2026-08-16T00:00:00",
        help="只处理修改时间晚于此 ISO 时间的文件（默认本次测试日）",
    )
    args = parser.parse_args()
    since_ts = datetime.fromisoformat(args.since).timestamp()

    # 收集数据库中的真实路径
    known_paths = set()
    if DB_PATH.exists():
        conn = sqlite3.connect(str(DB_PATH))
        for table in ("inspirations",):
            try:
                for (p,) in conn.execute(
                    f"SELECT file_path FROM {table} WHERE file_path IS NOT NULL"
                ):
                    known_paths.add(p.replace("\\", "/"))
                for (p,) in conn.execute(
                    f"SELECT thumbnail_path FROM {table} WHERE thumbnail_path IS NOT NULL"
                ):
                    known_paths.add(p.replace("\\", "/"))
            except sqlite3.Error:
                pass
        conn.close()
    print(f"数据库已知路径: {len(known_paths)} 条")

    candidates = []
    for sub in ("images", "thumbnails"):
        base = STORAGE_DIR / sub
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
                continue
            rel = p.relative_to(STORAGE_DIR).as_posix()
            if rel in known_paths:
                continue  # 数据库有记录，跳过
            if p.stat().st_mtime < since_ts:
                continue  # 早于测试时段，跳过
            if is_test_file(p):
                candidates.append(p)

    if not candidates:
        print("未发现测试污染文件")
        return

    print(f"发现 {len(candidates)} 个疑似测试污染文件:")
    for p in candidates:
        print(f"  {p.relative_to(BACKEND_DIR)}  ({p.stat().st_size} B)")

    if args.delete:
        for p in candidates:
            try:
                p.unlink()
                print(f"  已删除: {p}")
            except OSError as e:
                print(f"  删除失败: {p} — {e}", file=sys.stderr)
        print(f"完成，共删除 {len(candidates)} 个文件")
    else:
        print("（未删除；加 --delete 确认清理）")


if __name__ == "__main__":
    main()
