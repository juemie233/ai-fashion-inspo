"""穿搭博主人物组（方案 B）：绑定/解绑/切主 + 组查询。

同一现实人物在抖音/小红书各有一个账号（两条 blogger 记录），通过
``person_groups`` 声明为同一人；账号记录全部保留，按平台采集/浏览不受影响。

- 绑定（link）：把两个账号并入同一组（新建组或并入已有组）
- 解绑（unlink）：把账号移出组（变独立账号；组内剩 1 个账号时该组自动清理）
- 切主（set_primary）：手动指定组内主账号；不指定时主账号自动取素材数最多者
- 所有写操作均写审计留痕（audit_logs），供追溯
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.person import Blogger, PersonGroup
from app.services.audit_service import record_audit_log
from app.services.person.base import PersonConflictError, PersonNotFoundError


async def _get_group(db: AsyncSession, group_id: int) -> PersonGroup:
    """按 ID 取组，不存在抛 PersonNotFoundError。"""
    group = await db.get(PersonGroup, group_id)
    if not group:
        raise PersonNotFoundError("人物组不存在")
    return group


async def _get_blogger(db: AsyncSession, blogger_id: int) -> Blogger:
    """按 ID 取博主，不存在抛 PersonNotFoundError。"""
    blogger = await db.get(Blogger, blogger_id)
    if not blogger:
        raise PersonNotFoundError("博主未找到")
    return blogger


async def _auto_primary_blogger_id(db: AsyncSession, group: PersonGroup) -> int:
    """组内主账号自动确定：素材数最多者；同数取 id 较小者（先建）。

    仅当未手动指定 primary_blogger_id 时调用。
    """
    from app.models.person import InspirationBlogger

    stmt = (
        select(Blogger.id, func.count(InspirationBlogger.inspiration_id).label("cnt"))
        .outerjoin(
            InspirationBlogger, InspirationBlogger.blogger_id == Blogger.id
        )
        .where(Blogger.person_group_id == group.id)
        .group_by(Blogger.id)
        .order_by(func.count(InspirationBlogger.inspiration_id).desc(), Blogger.id.asc())
    )
    row = (await db.execute(stmt)).first()
    if row is None:
        raise PersonConflictError("人物组内没有博主，无法确定主账号")
    return int(row[0])


async def _effective_primary(db: AsyncSession, group: PersonGroup) -> int:
    """返回组当前生效的主账号 ID（手动指定优先，否则自动取素材数最多）。"""
    if group.primary_blogger_id is not None:
        return group.primary_blogger_id
    return await _auto_primary_blogger_id(db, group)


async def link_bloggers(
    db: AsyncSession,
    blogger_id: int,
    target_blogger_id: int | None = None,
    group_id: int | None = None,
) -> dict:
    """把博主绑定到目标博主（新建组）或已有组，返回组信息。

    参数二选一：
        target_blogger_id: 目标博主 ID——两账号组成新组
        group_id: 已有组 ID——把该博主并入现有组
    被绑定的博主与组内账号必须互不相同（防自环）。

    返回: {"group_id", "group_name", "primary_blogger_id", "member_ids"}
    """
    if (target_blogger_id is None) == (group_id is None):
        raise PersonConflictError("必须且只能提供 target_blogger_id 或 group_id 之一")
    if target_blogger_id is not None and target_blogger_id == blogger_id:
        raise PersonConflictError("不能把博主绑定到自身")

    blogger = await _get_blogger(db, blogger_id)

    if group_id is not None:
        group = await _get_group(db, group_id)
        if blogger.person_group_id == group.id:
            raise PersonConflictError("该博主已在目标组内")
    else:
        assert target_blogger_id is not None
        target = await _get_blogger(db, target_blogger_id)
        if target.person_group_id is not None:
            # 目标博主已在某组：并入该组
            group = await _get_group(db, target.person_group_id)
            if group.id == blogger.person_group_id:
                raise PersonConflictError("两个博主已在同一组")
        else:
            # 目标博主独立：新建组（组名取素材数多者；尚未绑定完，先取目标名）
            group = PersonGroup(name=target.name)
            db.add(group)
            await db.flush()
            target.person_group_id = group.id

    blogger.person_group_id = group.id
    # 组名默认取当前主账号名（主账号可能因并入变化，在提交前刷新）
    primary_id = await _effective_primary(db, group)
    primary = await _get_blogger(db, primary_id)
    group.name = primary.name
    await db.commit()
    await db.refresh(group)

    member_ids = (
        await db.execute(
            select(Blogger.id).where(Blogger.person_group_id == group.id)
        )
    ).scalars().all()

    await record_audit_log(
        action="link_blogger_group",
        target_type="bloggers",
        count=len(member_ids),
        detail=f"博主 {blogger.name}({blogger.id}) 绑定到组 {group.id}（成员 {member_ids}）",
    )
    return {
        "group_id": group.id,
        "group_name": group.name,
        "primary_blogger_id": primary_id,
        "member_ids": member_ids,
    }


async def unlink_blogger(db: AsyncSession, blogger_id: int) -> dict:
    """把博主移出人物组（变独立账号）；组内仅剩 1 个账号时自动清理组。

    返回: {"blogger_id", "removed_group_id"|None}
    """
    blogger = await _get_blogger(db, blogger_id)
    if blogger.person_group_id is None:
        raise PersonConflictError("该博主不在任何人物组内")

    group = await _get_group(db, blogger.person_group_id)
    blogger.person_group_id = None
    # 组内剩余账号数
    remaining = (
        await db.execute(
            select(Blogger.id).where(Blogger.person_group_id == group.id)
        )
    ).scalars().all()
    removed_group_id: int | None = None
    if len(remaining) <= 1:
        # 组内只剩自己（或已空）：删组，剩余账号回退独立
        removed_group_id = group.id
        await db.delete(group)
    else:
        # 手动主账号若正是被解绑者：清空，恢复自动
        if group.primary_blogger_id == blogger_id:
            group.primary_blogger_id = None
        # 组名随主账号刷新
        primary_id = await _effective_primary(db, group)
        primary = await _get_blogger(db, primary_id)
        group.name = primary.name

    await db.commit()
    await record_audit_log(
        action="unlink_blogger_group",
        target_type="bloggers",
        count=1,
        detail=(
            f"博主 {blogger.name}({blogger.id}) 移出组 {group.id}"
            + ("（组已删除）" if removed_group_id else "")
        ),
    )
    return {"blogger_id": blogger_id, "removed_group_id": removed_group_id}


async def set_primary_blogger(db: AsyncSession, group_id: int, blogger_id: int) -> dict:
    """手动指定组内主账号（展示位）；传 None 恢复自动（素材数最多）。

    返回: {"group_id", "primary_blogger_id"}
    """
    group = await _get_group(db, group_id)
    blogger = await _get_blogger(db, blogger_id)
    if blogger.person_group_id != group.id:
        raise PersonConflictError("该博主不在目标组内")

    group.primary_blogger_id = blogger.id
    group.name = blogger.name
    await db.commit()
    await record_audit_log(
        action="set_primary_blogger",
        target_type="bloggers",
        count=1,
        detail=f"组 {group.id} 主账号设为 博主 {blogger.name}({blogger.id})",
    )
    return {"group_id": group.id, "primary_blogger_id": blogger.id}


async def get_group_info(db: AsyncSession, group_id: int) -> dict:
    """查询人物组完整信息（组内各账号 + 主账号）。"""
    group = await _get_group(db, group_id)
    primary_id = await _effective_primary(db, group)
    rows = (
        await db.execute(
            select(Blogger).where(Blogger.person_group_id == group.id).order_by(Blogger.id)
        )
    ).scalars().all()
    return {
        "group_id": group.id,
        "group_name": group.name,
        "primary_blogger_id": primary_id,
        "members": [
            {
                "id": b.id,
                "name": b.name,
                "platform": b.platform,
                "platform_user_id": b.platform_user_id,
                "xhs_id": b.xhs_id,
                "profile_url": b.profile_url,
                "avatar_path": b.avatar_path,
            }
            for b in rows
        ],
    }
