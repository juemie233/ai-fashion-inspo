"""执行 SQLite ANALYZE，刷新查询优化器统计信息（2026-11-06 数据库审查 P2-1）。

只写 sqlite_stat1，不改业务数据；建议在新增索引/大批量导入后运行一次。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.config import settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="对当前 SQLite 库执行 ANALYZE")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    con = sqlite3.connect(str(settings.db_file_path), timeout=30)
    try:
        con.execute("ANALYZE")
        con.commit()
        stats = con.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='sqlite_stat1'"
        ).fetchone()[0]
    finally:
        con.close()
    if not args.quiet:
        print(f"ANALYZE 完成；sqlite_stat1 存在={bool(stats)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
