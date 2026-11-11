"""AI 日志保留策略报告（2026-11-06 数据库审查 P2-8）。

只读统计 `ai_analysis_log` 与 `ai_extracted_tags` 的规模、年龄分布与重复分析组，
用于制定「每个素材保留最新成功分析 + 人工纠错引用」的保留策略；**不删除任何数据**。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.config import settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="AI 日志保留策略只读报告")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    con = sqlite3.connect(f"{settings.db_file_path.as_uri()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        now = datetime.now(UTC).replace(tzinfo=None)
        rows = []
        total_logs = con.execute("SELECT COUNT(*) FROM ai_analysis_log").fetchone()[0]
        total_tags = con.execute("SELECT COUNT(*) FROM ai_extracted_tags").fetchone()[0]
        for days in (30, 90, 180, 365):
            cutoff = (now - timedelta(days=days)).isoformat(sep=" ")
            logs = con.execute(
                "SELECT COUNT(*) FROM ai_analysis_log WHERE created_at < ?", (cutoff,)
            ).fetchone()[0]
            rows.append({"older_than_days": days, "analysis_logs": logs})
        dup = con.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT inspiration_id, model_name, prompt_version, COUNT(*) AS n
                FROM ai_analysis_log
                WHERE log_type = 'analysis'
                GROUP BY inspiration_id, model_name, prompt_version
                HAVING n > 1
            )
            """
        ).fetchone()[0]
    finally:
        con.close()

    report = {
        "analysis_logs": total_logs,
        "extracted_tags": total_tags,
        "age_distribution": rows,
        "duplicate_analysis_groups": dup,
        "policy": (
            "建议按「每个素材保留最新成功分析 + 被纠错/审计引用的日志」设计，"
            "不要直接按时间删除：tag_corrections / quality_reviews 通过 log_id 引用分析日志。"
        ),
    }
    if args.json:
        import json

        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"ai_analysis_log: {total_logs} 行；ai_extracted_tags: {total_tags} 行")
        for row in rows:
            print(f"  >{row['older_than_days']} 天: {row['analysis_logs']} 条分析日志")
        print(f"重复分析组（素材+模型+prompt 版本）: {dup}")
        print(report["policy"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
