"""清理素材人脸检测表中的重复记录（同一素材 + 相同 embedding 的多条）。

背景：修复前「检测并匹配 / 批量扫描」对已确认（锁定）人脸会重复插入
同 embedding 记录（face_index 递增），素材详情出现多条相同人脸。
本脚本删除每组 (inspiration_id, embedding) 中多余记录，每组保留一条：
优先保留已确认（confirmed）记录，其次 id 最小（最早创建）。

用法：
    python scripts/dedupe_face_detections.py            # dry-run：仅统计
    python scripts/dedupe_face_detections.py --delete   # 实际删除
"""

import argparse
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "backend" / "fashion_inspo.db"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delete", action="store_true", help="实际删除（缺省仅 dry-run 统计）")
    args = parser.parse_args()

    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()

    # 取全部非空 embedding 记录（占位记录 embedding='' 不参与分组）
    rows = cur.execute(
        """
        SELECT id, inspiration_id, embedding, match_status
        FROM inspiration_face_detections
        WHERE embedding != ''
        ORDER BY inspiration_id, embedding
        """
    ).fetchall()

    # 分组：保留一条（confirmed 优先，其次 id 小），其余标记删除
    groups: dict[tuple[str, bytes], list[tuple[int, str | None]]] = {}
    for det_id, insp_id, embedding, status in rows:
        groups.setdefault((insp_id, embedding), []).append((det_id, status))

    to_delete: list[int] = []
    dup_groups = 0
    for (_key, members) in groups.items():
        if len(members) <= 1:
            continue
        dup_groups += 1
        members.sort(key=lambda m: (m[1] != "confirmed", m[0]))  # confirmed 优先，再 id 小
        keep = members[0][0]
        for det_id, _status in members[1:]:
            to_delete.append(det_id)

    print(f"重复组数: {dup_groups}，将删除记录数: {len(to_delete)}")
    if to_delete:
        print("删除示例（每组保留外的记录 id）:", to_delete[:10], "…")
    else:
        print("无重复记录，无需清理。")
        conn.close()
        return 0

    if not args.delete:
        print("dry-run：未执行删除。加 --delete 实际执行。")
        conn.close()
        return 0

    cur.executemany("DELETE FROM inspiration_face_detections WHERE id = ?", [(i,) for i in to_delete])
    conn.commit()
    print(f"已删除 {len(to_delete)} 条重复记录。")

    # 校验：删除后应无重复组
    left = cur.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT 1 FROM inspiration_face_detections
            WHERE embedding != ''
            GROUP BY inspiration_id, embedding HAVING COUNT(*) > 1
        )
        """
    ).fetchone()[0]
    print(f"校验：剩余重复组数 = {left}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
