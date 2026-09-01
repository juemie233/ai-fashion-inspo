"""一次性诊断：向量回填任务状态 / pending 积压 / 向量库计数（只读）。"""

import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "backend" / "fashion_inspo.db"


def main() -> None:
    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()

    print("=== pending_vector_backfills 积压 ===")
    print("积压数:", cur.execute("SELECT COUNT(*) FROM pending_vector_backfills").fetchone()[0])
    print()

    print("=== 最近 12 个 vector_backfill 任务 ===")
    rows = cur.execute(
        """
        SELECT id, status, total, done, error,
               substr(result, 1, 200) AS result_head
        FROM task_queue WHERE type='vector_backfill'
        ORDER BY id DESC LIMIT 12
        """
    ).fetchall()
    for r in rows:
        print(r)
        print("---")
    print()

    print("=== 素材计数 ===")
    print("全部素材:", cur.execute("SELECT COUNT(*) FROM inspirations").fetchone()[0])
    print(
        "图片素材(未删):",
        cur.execute(
            "SELECT COUNT(*) FROM inspirations WHERE media_type='image' AND deleted_at IS NULL"
        ).fetchone()[0],
    )
    conn.close()


if __name__ == "__main__":
    main()
