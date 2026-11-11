"""任务完整 result 保留清理（2026-11-06 数据库审查 P1-2）。

默认 dry-run，只统计；`--apply` 才写库。清空的是终态任务超过保留天数的完整
result，`result_summary` 原样保留（列表/详情仍可看摘要）。

用法：
    cd backend
    python scripts/ops/prune_task_results.py                 # 预览
    python scripts/ops/prune_task_results.py --apply         # 执行
    python scripts/ops/prune_task_results.py --apply --vacuum # 执行并 VACUUM
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.config import settings  # noqa: E402


def _vacuum() -> None:
    from app.config import settings as _settings

    con = sqlite3.connect(str(_settings.db_file_path))  # noqa: scripts-check —— 本脚本需要 VACUUM（写操作）
    try:
        con.execute("VACUUM")
    finally:
        con.close()


async def _run(args: argparse.Namespace) -> int:
    from app.database import async_session
    from app.services.task_retention import prune_task_results

    async with async_session() as db:
        stats = await prune_task_results(db, days=args.days, dry_run=not args.apply)
    mode = "执行" if args.apply else "预览"
    print(
        f"[{mode}] 终态任务 result 超过 {args.days if args.days is not None else settings.task_result_retention_days} 天："
        f"{stats['scanned']} 条 / {stats['bytes'] / 1048576:.1f} MB"
    )
    if args.apply and args.vacuum:
        print("执行 VACUUM（需要独占写锁；服务运行中可能等待/失败）...")
        try:
            _vacuum()
            print("VACUUM 完成")
        except sqlite3.OperationalError as exc:
            print(f"VACUUM 失败（可稍后停机执行）：{exc}")
            return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="清理超期任务完整 result（保留摘要）")
    parser.add_argument("--days", type=int, default=None, help="保留天数（默认读配置，7 天）")
    parser.add_argument("--apply", action="store_true", help="真正写库（默认 dry-run）")
    parser.add_argument("--vacuum", action="store_true", help="清理后执行 VACUUM")
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
