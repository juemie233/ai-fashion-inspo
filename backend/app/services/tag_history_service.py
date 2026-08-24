"""标签操作历史服务：快照记录、历史查询与单条操作回滚。

记录策略：
- 标签域的写操作（create / rename / category_change / update / move / merge /
  alias_add / alias_remove / batch_edit / delete）在各自操作点调用
  record_history 写入 before/after 快照；
- 快照为 JSON 文本，记录受影响标签的可编辑状态（含别名与关联数），
  供历史查看与回滚冲突检测；
- merge 等需要精确恢复关联的操作，把关键明细（重指向的关联/别名 ID）写入 meta。

回滚策略：
- 单条操作级回滚：用 before 快照恢复字段/别名，delete 回滚重建标签行
  （保留原 id），merge 回滚重建源标签并恢复关联；
- 回滚前做冲突检测：仅比较本次操作实际变更的字段；create 回滚额外校验
  关联数（防止级联删除新增关联导致数据丢失）；冲突时抛 TagHistoryRollbackError
  （路由层转 409）。
"""

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tag import InspirationTag, Tag, TagAlias
from app.models.tag_history import TagHistory

# 冲突检测/回滚需要比较的「可编辑」字段（不含派生计数）
_EDITABLE_FIELDS = (
    "name",
    "category",
    "parent_id",
    "pinned",
    "sort_order",
    "description",
    "source",
)

# 操作类型 → 中文描述（回滚成功提示用）
_OP_LABELS = {
    "create": "创建标签",
    "rename": "重命名标签",
    "category_change": "修改类别",
    "move": "移动层级",
    "merge": "合并标签",
    "alias_add": "添加别名",
    "alias_remove": "删除别名",
    "batch_edit": "批量编辑",
    "delete": "删除标签",
    "update": "更新标签",
}


class TagHistoryNotFoundError(Exception):
    """历史记录不存在（路由层转为 404）。"""


class TagHistoryRollbackError(Exception):
    """回滚冲突或失败（路由层转为 409）。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def new_batch_id(prefix: str = "b") -> str:
    """生成批次 ID（同一批操作的 history 分组）。"""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{ts}-{uuid.uuid4().hex[:6]}"


# ============ 快照 ============


async def _aliases_of(db: AsyncSession, tag_ids: list[int]) -> dict[int, list[str]]:
    """一次性查询多个标签的别名列表。"""
    result = await db.execute(
        select(TagAlias.tag_id, TagAlias.alias)
        .where(TagAlias.tag_id.in_(tag_ids))
        .order_by(TagAlias.alias)
    )
    out: dict[int, list[str]] = {tid: [] for tid in tag_ids}
    for tid, alias in result.all():
        out.setdefault(tid, []).append(alias)
    return out


async def _link_counts_of(db: AsyncSession, tag_ids: list[int]) -> dict[int, int]:
    """一次性查询多个标签的素材关联数。"""
    result = await db.execute(
        select(InspirationTag.tag_id, func.count(InspirationTag.inspiration_id))
        .where(InspirationTag.tag_id.in_(tag_ids))
        .group_by(InspirationTag.tag_id)
    )
    return {tid: cnt for tid, cnt in result.all()}


async def snapshot_tags(db: AsyncSession, tag_ids: list[int]) -> dict[int, dict]:
    """采集多个标签的快照（可编辑字段 + 别名 + 关联数），返回 {tag_id: snapshot}。"""
    ids = list(dict.fromkeys(tag_ids))
    if not ids:
        return {}
    result = await db.execute(select(Tag).where(Tag.id.in_(ids)))
    tags = {t.id: t for t in result.scalars().all()}
    aliases = await _aliases_of(db, ids)
    link_counts = await _link_counts_of(db, ids)
    snap: dict[int, dict] = {}
    for tid, tag in tags.items():
        snap[tid] = {
            "id": tid,
            "name": tag.name,
            "category": tag.category,
            "parent_id": tag.parent_id,
            "pinned": tag.pinned,
            "sort_order": tag.sort_order,
            "description": tag.description,
            "source": tag.source,
            "aliases": aliases.get(tid, []),
            "link_count": link_counts.get(tid, 0),
        }
    return snap


async def snapshot_tag(db: AsyncSession, tag_id: int) -> dict | None:
    """采集单个标签快照；标签不存在返回 None。"""
    return (await snapshot_tags(db, [tag_id])).get(tag_id)


# ============ 记录 ============


async def record_history(
    db: AsyncSession,
    *,
    operation: str,
    before: dict[int, dict],
    after: dict[int, dict],
    batch_id: str | None = None,
    meta: dict | None = None,
) -> TagHistory:
    """写入一条标签操作历史（仅 flush，随调用方事务一并提交）。

    参数:
        operation: 操作类型（create/rename/category_change/update/move/merge/
            alias_add/alias_remove/batch_edit/delete）
        before: 操作前快照 {tag_id: snapshot}
        after: 操作后快照 {tag_id: snapshot}；被删除的标签用
            {"deleted": True, "name": ...} 表示
        batch_id: 同批次操作的 ID（批量编辑 / 聚类 apply / 批量移动）
        meta: 附加信息（merge 的关联明细 / 正则规则等）
    """
    affected = sorted(set(before) | set(after))
    row = TagHistory(
        batch_id=batch_id,
        operation=operation,
        tag_ids=json.dumps(affected, ensure_ascii=False),
        before_snapshot=json.dumps(before, ensure_ascii=False, sort_keys=True),
        after_snapshot=json.dumps(after, ensure_ascii=False, sort_keys=True),
        meta=json.dumps(meta, ensure_ascii=False, sort_keys=True) if meta else None,
    )
    db.add(row)
    await db.flush()
    return row


# ============ 查询 ============


async def list_history(
    db: AsyncSession,
    page: int = 1,
    size: int = 20,
    operation: str | None = None,
    tag_id: int | None = None,
    batch_id: str | None = None,
) -> dict:
    """分页查询操作历史（按时间倒序），支持按操作类型 / 标签 / 批次过滤。"""
    query = select(TagHistory)
    count_query = select(func.count(TagHistory.id))
    if operation:
        query = query.where(TagHistory.operation == operation)
        count_query = count_query.where(TagHistory.operation == operation)
    if batch_id:
        query = query.where(TagHistory.batch_id == batch_id)
        count_query = count_query.where(TagHistory.batch_id == batch_id)
    if tag_id is not None:
        # tag_ids 存 JSON 数组文本，用 SQLite json_each 精确匹配成员
        exists_clause = text(
            "EXISTS (SELECT 1 FROM json_each(tag_history.tag_ids) je "
            "WHERE je.value = :tid)"
        ).bindparams(tid=tag_id)
        query = query.where(exists_clause)
        count_query = count_query.where(exists_clause)

    total = (await db.execute(count_query)).scalar() or 0
    result = await db.execute(
        query.order_by(TagHistory.created_at.desc(), TagHistory.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    rows = result.scalars().all()
    # 收集本页全部受影响标签 ID → 当前名字映射（供列表展示；标签可能已被
    # 删除/重命名，缺失的 ID 用 "#id" 兜底，tag_ids 本身保持原样供回滚使用）
    page_ids = [tid for r in rows for tid in json.loads(r.tag_ids)]
    name_map: dict[int, str] = {}
    if page_ids:
        tag_rows = await db.execute(select(Tag.id, Tag.name).where(Tag.id.in_(set(page_ids))))
        name_map = {tid: name for tid, name in tag_rows.all()}
    items = []
    for r in rows:
        ids = json.loads(r.tag_ids)
        items.append(
            {
                "id": r.id,
                "batch_id": r.batch_id,
                "operation": r.operation,
                "tag_ids": ids,
                "tag_names": [name_map.get(tid) or f"#{tid}" for tid in ids],
                "before": json.loads(r.before_snapshot),
                "after": json.loads(r.after_snapshot),
                "meta": json.loads(r.meta) if r.meta else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
        )
    return {"items": items, "total": total, "page": page, "size": size}


# ============ 回滚 ============


def _diff_fields(before_snap: dict, after_snap: dict) -> list[str]:
    """返回操作前后发生变化的可编辑字段名。"""
    return [f for f in _EDITABLE_FIELDS if before_snap.get(f) != after_snap.get(f)]


async def _check_conflicts(
    db: AsyncSession, operation: str, before: dict, after: dict
) -> None:
    """回滚前冲突检测：仅比较本次操作实际变更的内容。

    冲突时抛 TagHistoryRollbackError，汇总全部冲突项。
    """
    conflicts: list[str] = []
    for tid_str, after_snap in after.items():
        tid = int(tid_str)
        cur = await snapshot_tag(db, tid)
        if after_snap.get("deleted"):
            # 该标签在操作后被删除：回滚要求它当前不存在
            if cur is not None:
                conflicts.append(f"标签「{after_snap.get('name', tid)}」在删除后又被重新创建")
            continue
        if cur is None:
            conflicts.append(f"标签「{after_snap.get('name', tid)}」已不存在")
            continue

        before_snap = before.get(tid_str, {})
        if operation == "create":
            # 创建回滚 = 删除标签：任何后续修改（含新增关联）都阻断
            changed = list(_EDITABLE_FIELDS) + ["aliases", "link_count"]
        elif operation in ("alias_add", "alias_remove"):
            changed = ["aliases"]
        elif operation == "merge":
            # 合并不修改目标字段，仅比较字段（关联增减不阻断回滚）
            changed = list(_EDITABLE_FIELDS)
        else:
            # rename / category_change / update / move / batch_edit：
            # 只比较本次操作实际变更的字段
            changed = _diff_fields(before_snap, after_snap) or list(_EDITABLE_FIELDS)

        diffs: list[str] = []
        for f in changed:
            if f == "aliases":
                if sorted(cur.get("aliases") or []) != sorted(after_snap.get("aliases") or []):
                    diffs.append("别名")
            elif f == "link_count":
                if (cur.get("link_count") or 0) != (after_snap.get("link_count") or 0):
                    diffs.append("关联数")
            elif cur.get(f) != after_snap.get(f):
                diffs.append(f)
        if diffs:
            conflicts.append(f"标签「{cur['name']}」自操作后已被修改（{', '.join(diffs)}）")
    if conflicts:
        raise TagHistoryRollbackError("；".join(conflicts))


async def _restore_aliases(db: AsyncSession, tag_id: int, before_aliases: list[str]) -> None:
    """把标签的别名列表恢复为 before_aliases（增删差异行）。"""
    cur = (
        await db.execute(select(TagAlias).where(TagAlias.tag_id == tag_id))
    ).scalars().all()
    cur_set = {a.alias for a in cur}
    target_set = set(before_aliases)
    for a in cur:
        if a.alias not in target_set:
            await db.delete(a)
    for alias in target_set - cur_set:
        # 别名全局唯一：若已被其它标签占用则跳过（正常回滚场景不会发生）
        occupied = await db.execute(select(TagAlias.id).where(TagAlias.alias == alias))
        if occupied.scalar_one_or_none() is None:
            db.add(TagAlias(tag_id=tag_id, alias=alias))


async def _restore_tag_fields(db: AsyncSession, tid: int, before_snap: dict) -> None:
    """用 before 快照恢复标签的可编辑字段。

    恢复 name 前检查全局唯一：操作后其它标签可能占用了旧名，
    直接赋值会撞唯一约束导致 500（应友好拒绝回滚）。
    """
    tag = await db.get(Tag, tid)
    if not tag:
        return
    if before_snap["name"] != tag.name:
        occupied = await db.execute(
            select(Tag.id).where(Tag.name == before_snap["name"], Tag.id != tid)
        )
        if occupied.scalar_one_or_none() is not None:
            raise TagHistoryRollbackError(
                f"无法回滚：标签名「{before_snap['name']}」已被其它标签占用"
            )
    tag.name = before_snap["name"]
    tag.category = before_snap["category"]
    tag.parent_id = before_snap["parent_id"]
    tag.pinned = before_snap["pinned"]
    tag.sort_order = before_snap["sort_order"]
    tag.description = before_snap["description"]
    tag.source = before_snap["source"]


async def _ensure_name_available(db: AsyncSession, name: str, exclude_id: int) -> None:
    """回滚重建标签前校验 name 未被其它标签占用（占用则拒绝回滚，防唯一约束 500）。"""
    occupied = await db.execute(
        select(Tag.id).where(Tag.name == name, Tag.id != exclude_id)
    )
    if occupied.scalar_one_or_none() is not None:
        raise TagHistoryRollbackError(
            f"无法回滚：标签名「{name}」已被其它标签占用"
        )


async def _restore_deleted_tags(db: AsyncSession, before: dict, after: dict) -> None:
    """delete 回滚：按 before 快照重建被删除的标签行（保留原 id）与别名。"""
    for tid_str, before_snap in before.items():
        tid = int(tid_str)
        if str(tid) not in after or not after[str(tid)].get("deleted"):
            continue
        if await db.get(Tag, tid):
            continue  # 冲突检测已保证不存在，这里防御性跳过
        await _ensure_name_available(db, before_snap["name"], tid)
        db.add(
            Tag(
                id=tid,
                name=before_snap["name"],
                category=before_snap["category"],
                source=before_snap.get("source", "manual"),
                pinned=before_snap.get("pinned", False),
                sort_order=before_snap.get("sort_order", 0),
                description=before_snap.get("description"),
                parent_id=before_snap.get("parent_id"),
            )
        )
        for alias in before_snap.get("aliases", []):
            db.add(TagAlias(tag_id=tid, alias=alias))


async def _rollback_merge(
    db: AsyncSession, before: dict, after: dict, meta: dict
) -> None:
    """merge 回滚：重建源标签行、恢复别名归属、把关联移回源标签。"""
    source_id = int(meta["source_tag_id"])
    target_id = int(meta["target_tag_id"])
    src_before = before.get(str(source_id), {})
    tgt_before = before.get(str(target_id), {})

    # 1. 重建源标签行
    if await db.get(Tag, source_id) is None:
        await _ensure_name_available(db, src_before["name"], source_id)
        db.add(
            Tag(
                id=source_id,
                name=src_before["name"],
                category=src_before["category"],
                source=src_before.get("source", "manual"),
                pinned=src_before.get("pinned", False),
                sort_order=src_before.get("sort_order", 0),
                description=src_before.get("description"),
                parent_id=src_before.get("parent_id"),
            )
        )

    # 2. 恢复源标签别名：合并时重指向目标的别名行改回源标签
    moved_alias_ids = meta.get("moved_alias_ids") or []
    if moved_alias_ids:
        await db.execute(
            update(TagAlias)
            .where(TagAlias.id.in_(moved_alias_ids))
            .values(tag_id=source_id)
        )

    # 3. 恢复关联：
    #    - 合并时重指向目标的关联行改回源标签
    #    - 合并时因目标已有同素材关联而删除的源关联行，重新插入
    merged_link_ids = meta.get("merged_link_ids") or []
    if merged_link_ids:
        await db.execute(
            update(InspirationTag)
            .where(
                InspirationTag.tag_id == target_id,
                InspirationTag.inspiration_id.in_(merged_link_ids),
            )
            .values(tag_id=source_id)
        )
    for insp_id in meta.get("duplicate_link_ids", []):
        db.add(InspirationTag(inspiration_id=insp_id, tag_id=source_id))

    # 4. 目标标签字段防御性恢复（merge 本身不改字段，正常无需变更）
    if tgt_before:
        await _restore_tag_fields(db, target_id, tgt_before)


async def _apply_rollback(
    db: AsyncSession,
    operation: str,
    before: dict,
    after: dict,
    meta: dict,
) -> None:
    """按操作类型执行回滚（未提交，由调用方统一 commit）。"""
    if operation == "create":
        # 创建回滚 = 删除该标签（冲突检测已保证其未被后续修改/关联）
        tid = int(next(iter(after)))
        tag = await db.get(Tag, tid)
        if tag:
            await db.delete(tag)
        return

    if operation == "alias_add":
        # 移除新增的别名（after - before 的差集）
        tid = int(next(iter(after)))
        added = set(after[str(tid)]["aliases"]) - set(before.get(str(tid), {}).get("aliases", []))
        for alias in added:
            row = (
                await db.execute(
                    select(TagAlias).where(TagAlias.tag_id == tid, TagAlias.alias == alias)
                )
            ).scalar_one_or_none()
            if row:
                await db.delete(row)
        return

    if operation == "alias_remove":
        # 重新添加被删除的别名
        tid = int(next(iter(after)))
        alias = (meta or {}).get("alias")
        if alias:
            exists = await db.execute(
                select(TagAlias.id).where(TagAlias.tag_id == tid, TagAlias.alias == alias)
            )
            if exists.scalar_one_or_none() is None:
                db.add(TagAlias(tag_id=tid, alias=alias))
        return

    if operation == "merge":
        await _rollback_merge(db, before, after, meta)
        return

    if operation == "delete":
        await _restore_deleted_tags(db, before, after)
        return

    # rename / category_change / update / move / batch_edit：恢复字段与别名
    for tid_str, before_snap in before.items():
        tid = int(tid_str)
        await _restore_tag_fields(db, tid, before_snap)
        await _restore_aliases(db, tid, before_snap.get("aliases", []))


async def rollback_history(db: AsyncSession, history_id: int) -> dict:
    """回滚一条操作历史（单条操作级；冲突时抛 TagHistoryRollbackError）。

    返回: {"rolled_back": True, "message": ...}
    """
    row = await db.get(TagHistory, history_id)
    if not row:
        raise TagHistoryNotFoundError("历史记录未找到")

    before = json.loads(row.before_snapshot)
    after = json.loads(row.after_snapshot)
    meta = json.loads(row.meta) if row.meta else {}

    await _check_conflicts(db, row.operation, before, after)
    await _apply_rollback(db, row.operation, before, after, meta)
    await db.commit()
    return {
        "rolled_back": True,
        "message": f"已回滚「{_OP_LABELS.get(row.operation, row.operation)}」",
    }
