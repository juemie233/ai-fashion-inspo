"""孤立媒体文件例行检查（2026-11-06 数据库审查 P2-10）。

磁盘上有、数据库无引用的文件（缩略图/垃圾桶/图片）会占用空间；本脚本只读扫描，
输出按目录分组的孤儿数量与体积，超过阈值返回 1，供定时任务/巡检告警。

用法：
    cd backend
    python scripts/ops/check_orphan_files.py
    python scripts/ops/check_orphan_files.py --max-orphans 100 --max-mb 50
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.config import settings  # noqa: E402


def _scan_files() -> dict[str, int]:
    """扫描 images/thumbnails/videos/trash（admin_stats_service 默认不含 trash）。"""
    root = settings.storage_root
    files: dict[str, int] = {}
    for dir_name in ("images", "thumbnails", "videos", "trash"):
        dir_path = root / dir_name
        if not dir_path.exists():
            continue
        for fpath in dir_path.rglob("*"):
            if fpath.is_file():
                try:
                    files[fpath.relative_to(root).as_posix()] = fpath.stat().st_size
                except (OSError, ValueError):
                    continue
    return files


def _db_paths() -> set[str]:
    con = sqlite3.connect(f"{settings.db_file_path.as_uri()}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT file_path, thumbnail_path FROM inspirations").fetchall()
    finally:
        con.close()
    return {p for row in rows for p in row if p}


def main() -> int:
    parser = argparse.ArgumentParser(description="检查未入库的孤立媒体文件")
    parser.add_argument("--max-orphans", type=int, default=0, help="允许的孤儿文件数上限")
    parser.add_argument("--max-mb", type=float, default=0, help="允许的孤儿总体积上限（MB）")
    args = parser.parse_args()

    root = settings.storage_root
    referenced = _db_paths()
    files = _scan_files()
    groups: dict[str, dict[str, int]] = defaultdict(lambda: {"count": 0, "bytes": 0})
    orphans = 0
    orphan_bytes = 0
    for rel in files:
        if rel in referenced:
            continue
        path = root / rel
        try:
            size = path.stat().st_size
        except OSError:
            continue
        top = str(Path(rel).parts[0]) if Path(rel).parts else "(root)"
        groups[top]["count"] += 1
        groups[top]["bytes"] += size
        orphans += 1
        orphan_bytes += size

    print(f"存储根: {root}")
    print(f"数据库引用: {len(referenced)} 个文件；磁盘文件: {len(files)} 个")
    print(f"孤儿: {orphans} 个 / {orphan_bytes / 1048576:.2f} MB")
    for name, stat in sorted(groups.items(), key=lambda item: item[1]["bytes"], reverse=True):
        print(f"  {name}: {stat['count']} 个 / {stat['bytes'] / 1048576:.2f} MB")

    if orphans > args.max_orphans or orphan_bytes > args.max_mb * 1048576:
        print(
            f"[ALERT] 超过阈值（max_orphans={args.max_orphans}, max_mb={args.max_mb}）；"
            "可先 POST /api/admin/cleanup-orphans?dry_run=true 预览，再执行清理。"
        )
        return 1
    print("[ok] 孤立文件在阈值内")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
