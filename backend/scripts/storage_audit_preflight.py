"""备份前存储完整性预检（只读，不阻塞备份）。

调用管理端同一套 ``admin_stats_service.integrity_check``：
- 配置了 Rust sidecar 时走 storage-audit；
- 未配置/失败自动回退 Python；
- 只报告缺失/孤立文件与垃圾桶不变量，不删除任何文件。

用法：
    python backend/scripts/storage_audit_preflight.py --json /path/report.json
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# 允许从项目根直接执行：把 backend/ 加入 sys.path，才能 import app.*
_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.database import async_session  # noqa: E402
from app.services import admin_stats_service  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="备份前只读完整性预检")
    parser.add_argument("--json", type=Path, help="报告输出路径；不填则打印到 stdout")
    return parser.parse_args()


async def build_report() -> dict:
    async with async_session() as db:
        result = await admin_stats_service.integrity_check(db)
    return {
        "scanner": result.get("scanner", "python"),
        "missing_count": result["missing_count"],
        "orphan_count": result["orphan_count"],
        "orphan_total_size_bytes": result["orphan_total_size_bytes"],
        "missing_sample": result["missing_files"][:20],
        "orphan_sample": result["orphan_files"][:20],
        "trash_invariant_count": len(result["trash_invariants"]),
    }


def main() -> int:
    args = parse_args()
    report = asyncio.run(build_report())
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    print(
        f"预检完成：scanner={report['scanner']} "
        f"missing={report['missing_count']} "
        f"orphan={report['orphan_count']} "
        f"orphan_bytes={report['orphan_total_size_bytes']} "
        f"trash_violations={report['trash_invariant_count']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
