"""扫描并清理测试污染文件：上传测试曾误写入真实 backend/storage/ 的纯色小图。

判定规则（四重确认，任一条件不满足即跳过，避免误删真实素材）：
1. 白名单：真实数据库（backend/fashion_inspo.db）有记录的路径一律跳过
   （inspirations.file_path / inspirations.thumbnail_path / persons.avatar_path）
2. 新鲜度保护：mtime 在最近 --recent-minutes 分钟内的文件一律跳过
   （防误删正在上传/刚生成的文件）
3. 时间窗口：只处理 mtime 不早于 --since 的文件；--since 默认「当前时间
   往前推 1 天」（不再写死未来日期，避免随时间失效）
4. 内容判定：PIL 判定为纯色小图（尺寸 <= 128x128、颜色极单一、平均色接近测试色）

用法:
    python scripts/clean_test_files.py            # 扫描（只报告不删除）
    python scripts/clean_test_files.py --delete   # 扫描并删除
"""

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
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
        default=None,
        help="只处理修改时间不早于此 ISO 时间的文件（默认当前时间往前推 1 天）",
    )
    parser.add_argument(
        "--recent-minutes",
        type=int,
        default=10,
        help="mtime 在最近 N 分钟内的文件一律跳过（默认 10，防误删在传/刚生成文件）",
    )
    args = parser.parse_args()
    if args.since:
        since_ts = datetime.fromisoformat(args.since).timestamp()
    else:
        since_ts = (datetime.now() - timedelta(days=1)).timestamp()
    recent_cutoff_ts = datetime.now().timestamp() - args.recent_minutes * 60

    # 收集数据库中的真实路径
    known_paths = set()
    if DB_PATH.exists():
        conn = sqlite3.connect(str(DB_PATH))
        # 各表媒体路径字段 → 白名单（新增路径字段时在此登记即可纳入保护）
        path_columns = {
            "inspirations": ("file_path", "thumbnail_path"),
            "persons": ("avatar_path",),
        }
        for table, columns in path_columns.items():
            for column in columns:
                try:
                    for (p,) in conn.execute(
                        f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL"
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
            mtime = p.stat().st_mtime
            if mtime > recent_cutoff_ts:
                continue  # 最近 N 分钟内修改，可能正在上传/生成，跳过
            if mtime < since_ts:
                continue  # 早于时间窗口，跳过
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
