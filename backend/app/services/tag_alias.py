"""标签别名管理：别名 CRUD（同义词归一化到主标签）。

依赖 tag_crud 的异常类（TagNotFoundError / TagConflictError），
不反向依赖任何其它标签模块。
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tag import Tag, TagAlias
from app.services.tag_crud import TagConflictError, TagNotFoundError
from app.services.tag_history_service import record_history, snapshot_tag


async def list_aliases(db: AsyncSession) -> list[dict]:
    """获取所有标签别名（含所属标签名）。"""
    result = await db.execute(
        select(TagAlias.id, TagAlias.tag_id, TagAlias.alias, Tag.name)
        .join(Tag, Tag.id == TagAlias.tag_id)
        .order_by(Tag.name, TagAlias.alias)
    )
    return [
        {"id": r[0], "tag_id": r[1], "alias": r[2], "tag_name": r[3]}
        for r in result.all()
    ]


async def create_alias(db: AsyncSession, tag_id: int, alias: str) -> TagAlias:
    """为标签添加别名。

    标签不存在抛 TagNotFoundError；与主标签或已有别名冲突抛 TagConflictError。
    并发创建同名别名时，回滚后重查并返回已存在的别名（路由层原样返回）。
    """
    tag = await db.get(Tag, tag_id)
    if not tag:
        raise TagNotFoundError("标签未找到")

    # 操作前快照（供操作历史与回滚）
    before_snap = await snapshot_tag(db, tag_id) or {}

    # 别名不能与任何主标签同名（否则产生歧义）
    existing_tag = await db.execute(select(Tag.id).where(Tag.name == alias))
    if existing_tag.scalar_one_or_none():
        raise TagConflictError(f"别名 '{alias}' 与已有标签同名")

    existing_alias = await db.execute(select(TagAlias).where(TagAlias.alias == alias))
    if existing_alias.scalar_one_or_none():
        raise TagConflictError(f"别名 '{alias}' 已存在")

    obj = TagAlias(tag_id=tag_id, alias=alias)
    db.add(obj)
    try:
        # 用 SAVEPOINT 隔离插入：并发创建同名字别名时，后者触发 IntegrityError，
        # 回滚后重查并返回已存在的别名，避免 500。
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        db.expunge(obj)
        existing = await db.execute(select(TagAlias).where(TagAlias.alias == alias))
        existing_obj = existing.scalar_one_or_none()
        if existing_obj:
            return existing_obj  # 并发冲突：别名非本次新增，不写操作历史
        raise TagConflictError(f"别名 '{alias}' 已存在")
    await db.refresh(obj)

    # 记录操作历史（随调用方事务一并提交）
    after_snap = await snapshot_tag(db, tag_id) or {}
    await record_history(
        db,
        operation="alias_add",
        before={tag_id: before_snap},
        after={tag_id: after_snap},
        meta={"alias": alias},
    )
    return obj


async def delete_alias(db: AsyncSession, alias_id: int) -> bool:
    """删除标签别名，返回是否删除成功。"""
    obj = await db.get(TagAlias, alias_id)
    if not obj:
        return False
    tag_id = obj.tag_id
    alias = obj.alias
    # 操作前快照（供操作历史与回滚）
    before_snap = await snapshot_tag(db, tag_id) or {}
    await db.delete(obj)
    await db.commit()
    # 记录操作历史（delete_alias 已内部提交，此处独立提交 history 行）
    after_snap = await snapshot_tag(db, tag_id) or {}
    await record_history(
        db,
        operation="alias_remove",
        before={tag_id: before_snap},
        after={tag_id: after_snap},
        meta={"alias": alias},
    )
    await db.commit()
    return True
