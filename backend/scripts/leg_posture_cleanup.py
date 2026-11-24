"""旧腿部姿态标签定向清理（默认 dry-run，不物理删除标签定义）。

只处理已标记 legacy 的泛化 `并拢` 关联：
1. 半身/七分取景且没有全身属性 → 删除该 AI 关联；
2. 与交叉腿/跷腿/分开/抬腿/弯曲等关系词矛盾 → 删除该 AI 关联；
3. 全身站姿的默认并拢先不自动删，只统计，等待人工复核。

只删除 inspiration_tags 中的 ai_generated 关联；标签本身保留（is_legacy=1）以便追溯。
默认 dry-run；加 --apply 才写库。
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

LEGACY_NAME = "并拢"

# 与 apply_cleanup 的 DELETE 使用同一套条件：preview 的 cleanup_candidates 与
# 实际删除必须同口径（此前 half + contradiction 相加会把同时命中两类的图计两次）
_CLEANUP_CONDITION = """
        tag_id=? AND source='ai_generated' AND (
            (EXISTS (
                SELECT 1 FROM inspiration_tags x JOIN tags t ON t.id=x.tag_id
                 WHERE x.inspiration_id=inspiration_tags.inspiration_id
                   AND t.category='attribute' AND t.name IN ('半身','七分')
            ) AND NOT EXISTS (
                SELECT 1 FROM inspiration_tags y JOIN tags t ON t.id=y.tag_id
                 WHERE y.inspiration_id=inspiration_tags.inspiration_id AND t.name='全身'
            ))
            OR EXISTS (
                SELECT 1 FROM inspiration_tags x JOIN tags t ON t.id=x.tag_id
                 WHERE x.inspiration_id=inspiration_tags.inspiration_id
                   AND t.category='leg_posture'
                   AND t.name IN ('交叉腿','腿交叉','腿部交叉','跷腿','翘腿','分开','分腿','抬腿','弯曲')
            )
        )
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="旧腿部并拢标签定向清理（默认 dry-run）")
    parser.add_argument("--apply", action="store_true", help="实际删除关联（默认只预览）")
    parser.add_argument(
        "--all-legacy",
        action="store_true",
        help="清空该 legacy 标签的全部 AI 关联（仍保留标签定义）",
    )
    parser.add_argument("--json", type=Path, help="输出预览/结果 JSON")
    return parser.parse_args()


def _connection(apply: bool) -> sqlite3.Connection:
    mode = "rw" if apply else "ro"
    # --apply 会用同一连接执行定向 DELETE；默认 dry-run 只读，写连接需绕过只读守卫。
    # 写连接只用于 --apply；行内同时保留项目自定义的 scripts-check 豁免标记。
    return sqlite3.connect(  # noqa: E501  # noqa: scripts-check
        f"file:{Path(settings.db_file_path).as_posix()}?mode={mode}", uri=True
    )


def _count(con: sqlite3.Connection, sql: str, params=()) -> int:
    return con.execute(sql, params).fetchone()[0]


def build_preview(con: sqlite3.Connection, all_legacy: bool = False) -> dict:
    tag_id = con.execute("SELECT id FROM tags WHERE name=? AND category='leg_posture'", (LEGACY_NAME,)).fetchone()
    if not tag_id:
        return {"tag": LEGACY_NAME, "tag_id": None, "half_visible": 0, "contradiction": 0, "full_default_keep": 0}
    tid = tag_id[0]
    half = _count(
        con,
        """
        SELECT COUNT(DISTINCT it.inspiration_id)
          FROM inspiration_tags it
         WHERE it.tag_id=? AND it.source='ai_generated' AND EXISTS (
             SELECT 1 FROM inspiration_tags x JOIN tags t ON t.id=x.tag_id
              WHERE x.inspiration_id=it.inspiration_id AND t.category='attribute'
                AND t.name IN ('半身','七分')
         ) AND NOT EXISTS (
             SELECT 1 FROM inspiration_tags y JOIN tags t ON t.id=y.tag_id
              WHERE y.inspiration_id=it.inspiration_id AND t.name='全身'
         )
        """,
        (tid,),
    )
    contradiction = _count(
        con,
        """
        SELECT COUNT(DISTINCT it.inspiration_id)
          FROM inspiration_tags it
         WHERE it.tag_id=? AND it.source='ai_generated' AND EXISTS (
             SELECT 1 FROM inspiration_tags x JOIN tags t ON t.id=x.tag_id
              WHERE x.inspiration_id=it.inspiration_id AND t.category='leg_posture'
                AND t.name IN ('交叉腿','腿交叉','腿部交叉','跷腿','翘腿','分开','分腿','抬腿','弯曲')
         )
        """,
        (tid,),
    )
    full_default = _count(
        con,
        """
        SELECT COUNT(DISTINCT it.inspiration_id)
          FROM inspiration_tags it
         WHERE it.tag_id=? AND it.source='ai_generated' AND EXISTS (
             SELECT 1 FROM inspiration_tags x JOIN tags t ON t.id=x.tag_id
              WHERE x.inspiration_id=it.inspiration_id AND t.name='全身' AND t.category='attribute'
         ) AND EXISTS (
             SELECT 1 FROM inspiration_tags y JOIN tags t ON t.id=y.tag_id
              WHERE y.inspiration_id=it.inspiration_id AND t.name='站姿' AND t.category='attribute'
         )
        """,
        (tid,),
    )
    total = _count(
        con,
        "SELECT COUNT(DISTINCT inspiration_id) FROM inspiration_tags WHERE tag_id=? AND source='ai_generated'",
        (tid,),
    )
    cleanup_candidates = _count(
        con,
        f"SELECT COUNT(DISTINCT inspiration_id) FROM inspiration_tags"
        f" WHERE {_CLEANUP_CONDITION}",
        (tid,),
    )
    return {
        "tag": LEGACY_NAME,
        "tag_id": tid,
        "ai_linked_images": total,
        "half_visible": half,
        "contradiction": contradiction,
        # 与 DELETE 同口径的去重计数（两类可能重叠）
        "cleanup_candidates": total if all_legacy else cleanup_candidates,
        "full_default_keep": full_default,
        "mode": "all_legacy" if all_legacy else "targeted",
    }


def apply_cleanup(
    con: sqlite3.Connection, tag_id: int, all_legacy: bool = False
) -> tuple[int, list[str]]:
    """执行定向删除，返回 (删除行数, 受影响素材 ID)。

    受影响素材必须在删除前取出：标签变更会影响素材的文本向量，删完要登记回填，
    否则语义检索仍会按旧标签召回它们（与其它标签增删路径保持一致）。
    """
    condition = (
        "tag_id=? AND source='ai_generated'"
        if all_legacy
        else _CLEANUP_CONDITION
    )
    affected = [
        row[0]
        for row in con.execute(
            f"SELECT DISTINCT inspiration_id FROM inspiration_tags WHERE {condition}",
            (tag_id,),
        )
    ]
    # 同一素材可能同时命中两类，用一条 DELETE 一次完成；只删 ai_generated，保留手动态。
    cur = con.execute(
        f"DELETE FROM inspiration_tags WHERE {condition}",
        (tag_id,),
    )
    return cur.rowcount or 0, affected


def enqueue_text_vector_backfills(inspiration_ids: list[str]) -> int:
    """登记文本向量回填（攒批队列），返回登记数；失败只告警不中断清理。"""
    ids = sorted(set(inspiration_ids))
    if not ids:
        return 0
    import asyncio

    from app.database import async_session
    from app.services.task_runners.vector_backfill import enqueue_vector_backfills

    async def _run() -> None:
        async with async_session() as db:
            await enqueue_vector_backfills(db, ids)
            await db.commit()

    try:
        asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 —— 清理已完成，回填失败只提示后续重试
        print(f"[warn] 登记文本向量回填失败：{exc}", file=sys.stderr)
        return 0
    return len(ids)


def main() -> int:
    args = parse_args()
    con = _connection(args.apply)
    try:
        preview = build_preview(con, args.all_legacy)
        result = {"dry_run": not args.apply, "preview": preview, "deleted_rows": 0}
        if args.apply and preview.get("tag_id"):
            con.execute("BEGIN IMMEDIATE")
            try:
                deleted, affected = apply_cleanup(
                    con, preview["tag_id"], args.all_legacy
                )
                con.commit()
                result["deleted_rows"] = deleted
                result["vector_backfill_enqueued"] = enqueue_text_vector_backfills(
                    affected
                )
            except Exception:
                con.rollback()
                raise
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.json:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
