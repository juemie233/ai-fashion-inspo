"""腿部姿态标签指标 / A-B 对比（只读）。

用于新 Prompt 或新词表上线后的回归观察：
- 全量 `并拢` / 新词表标签占比；
- legacy 标签关联量；
- 矛盾组合出现率；
- 不同 prompt_version 的腿部姿态标签产出率（A/B）。

用法::

    python backend/scripts/leg_posture_metrics.py
    python backend/scripts/leg_posture_metrics.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.config import settings  # noqa: E402

LEGACY_TAG = "并拢"
TOGETHER_TAG = "双腿并拢"
CONFLICT_TAGS = ("二郎腿", "双腿交叉", "脚踝交叉", "单腿抬起", "双腿分开", "踮脚", "屈膝")
# 当前审计的目标准入线（可随样本继续调）
TARGETS = {
    "legacy_prevalence_max": 0.05,
    "conflict_rate_max": 0.01,
    "meaningful_precision_min": 0.80,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="腿部姿态标签指标（只读）")
    parser.add_argument("--json", type=Path, help="额外输出 JSON 文件")
    return parser.parse_args()


def query(con: sqlite3.Connection, sql: str, params=()) -> list[tuple]:
    return con.execute(sql, params).fetchall()


def build_metrics() -> dict:
    con = sqlite3.connect(f"file:{Path(settings.db_file_path).as_posix()}?mode=ro", uri=True)
    try:
        total = query(con, "SELECT COUNT(*) FROM inspirations WHERE deleted_at IS NULL")[0][0]
        tag_rows = query(
            con,
            """
            SELECT t.name, COUNT(DISTINCT it.inspiration_id) AS cnt, t.is_legacy
              FROM tags t JOIN inspiration_tags it ON it.tag_id=t.id
              JOIN inspirations i ON i.id=it.inspiration_id AND i.deleted_at IS NULL
             WHERE t.category='leg_posture'
             GROUP BY t.id ORDER BY cnt DESC
            """,
        )
        tags = [
            {"name": name, "images": count, "is_legacy": bool(legacy)}
            for name, count, legacy in tag_rows
        ]
        by_name = {row["name"]: row for row in tags}

        def images_with_any(names: tuple[str, ...]) -> int:
            if not names:
                return 0
            marks = ",".join("?" for _ in names)
            sql = f"""
                SELECT COUNT(DISTINCT it.inspiration_id)
                  FROM tags t JOIN inspiration_tags it ON it.tag_id=t.id
                  JOIN inspirations i ON i.id=it.inspiration_id AND i.deleted_at IS NULL
                 WHERE t.category='leg_posture' AND t.name IN ({marks})
            """
            return query(con, sql, names)[0][0]

        legacy_images = images_with_any((LEGACY_TAG,))
        together_images = images_with_any((TOGETHER_TAG,))
        conflict_images = query(
            con,
            """
            SELECT COUNT(DISTINCT a.inspiration_id)
              FROM tags t JOIN inspiration_tags a ON a.tag_id=t.id
              JOIN tags o ON o.name IN ('二郎腿','双腿交叉','脚踝交叉','单腿抬起','双腿分开','踮脚','屈膝')
              JOIN inspiration_tags b ON b.tag_id=o.id AND b.inspiration_id=a.inspiration_id
              JOIN inspirations i ON i.id=a.inspiration_id AND i.deleted_at IS NULL
             WHERE t.category='leg_posture' AND t.name='双腿并拢'
            """,
        )[0][0]
        full_standing_together = query(
            con,
            """
            SELECT COUNT(DISTINCT l1.inspiration_id)
              FROM tags t1 JOIN inspiration_tags l1 ON l1.tag_id=t1.id
              JOIN tags t2 ON t2.name='全身' AND t2.category='attribute'
              JOIN inspiration_tags l2 ON l2.tag_id=t2.id AND l2.inspiration_id=l1.inspiration_id
              JOIN tags t3 ON t3.name='站姿' AND t3.category='attribute'
              JOIN inspiration_tags l3 ON l3.tag_id=t3.id AND l3.inspiration_id=l1.inspiration_id
              JOIN inspirations i ON i.id=l1.inspiration_id AND i.deleted_at IS NULL
             WHERE t1.category='leg_posture' AND t1.name='双腿并拢'
            """,
        )[0][0]

        prompt_rows = query(
            con,
            """
            SELECT l.prompt_version,
                   COUNT(DISTINCT l.id) AS logs,
                   COUNT(DISTINCT l.inspiration_id) AS images,
                   SUM(CASE WHEN t.category='leg_posture' THEN 1 ELSE 0 END) AS leg_tag_hits
              FROM ai_analysis_log l
              LEFT JOIN ai_extracted_tags e ON e.log_id=l.id
              LEFT JOIN tags t ON t.id=e.tag_id
             WHERE l.log_type='analysis' AND l.prompt_version IS NOT NULL
             GROUP BY l.prompt_version
             ORDER BY logs DESC
            """,
        )
        prompt_versions = [
            {
                "prompt_version": p,
                "logs": logs,
                "images": imgs,
                "leg_tag_hits": int(hits or 0),
                "leg_tag_rate": round((hits or 0) / logs, 4) if logs else 0,
            }
            for p, logs, imgs, hits in prompt_rows
        ]
        return {
            "total_images": total,
            "legacy_tag": {
                "name": LEGACY_TAG,
                "images": legacy_images,
                "prevalence": round(legacy_images / total, 4) if total else 0,
                "row": by_name.get(LEGACY_TAG),
            },
            "new_together_tag": {
                "name": TOGETHER_TAG,
                "images": together_images,
                "prevalence": round(together_images / total, 4) if total else 0,
                "row": by_name.get(TOGETHER_TAG),
            },
            "together_conflict_images": conflict_images,
            "conflict_rate": round(conflict_images / together_images, 4) if together_images else 0,
            "together_full_standing_images": full_standing_together,
            "targets": TARGETS,
            "leg_posture_tags": tags,
            "prompt_versions": prompt_versions,
        }
    finally:
        con.close()


def main() -> int:
    args = parse_args()
    metrics = build_metrics()
    text = json.dumps(metrics, ensure_ascii=False, indent=2)
    print(text)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
