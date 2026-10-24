"""人物管理的批量操作与聚合查询：批量纳入追踪 / 批量删除 / 头像覆盖与清除 / 同名建议。

为什么单独一个模块：这些操作都是「按 id 集合处理，逐条给出结果」，与单条 CRUD
（``services.py`` / ``base.py``）是两种粒度；放一起也方便前端一次拿全结果。

三条口径：
1. **能删才删**：删除复用 :meth:`PersonServiceBase.delete` 的校验（有关联素材一律拒绝），
   批量只负责汇总「删掉的」与「被挡下的原因」，绝不绕过校验；
2. **批量纳入追踪只动自动登记的**：``source != auto_collect`` 的一律跳过并在明细里说明，
   避免把用户已登记的博主的其它字段顺带改坏；
3. **计数只查库、不读图片**：头像覆盖率是给界面显示徽标的，不能因为读 400 个头像文件
   把接口拖慢。
"""

from __future__ import annotations

import logging
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inspiration import NOT_DELETED, Inspiration
from app.models.person import Blogger, InspirationBlogger
from app.services.audit_service import record_audit_log
from app.services.person.avatar import delete_avatar_file
from app.services.person.base import PersonHasInspirationsError, PersonNotFoundError
from app.services.person.services import blogger_service

logger = logging.getLogger(__name__)

"""自动登记来源常量：与 ``scripts.f2_common.AUTO_BLOGGER_SOURCE`` 同值。

这里写字面量而不是 import 脚本模块：app 侧不应该为了一个常量去依赖 ``scripts``。
"""
AUTO_BLOGGER_SOURCE = "auto_collect"

"""同名建议最多返回多少组（前端弹窗一屏够看，避免几百组把弹窗撑爆）。"""
MAX_DUPLICATE_GROUPS = 50


def normalize_name(name: str) -> str:
    """昵称归一化（仅用于「是否同一个名字」的判定，不落库）。

    与博主补全的昵称匹配同口径：strip + 全角转半角 + 小写 + 去掉空白/emoji/符号，
    只保留中英文与数字。抖音/小红书的昵称常带 emoji 与装饰符号，直接比较会漏判。
    """
    from app.services.blogger_enrichment_service import _normalize_name

    return _normalize_name(name)


async def batch_promote(db: AsyncSession, blogger_ids: list[int]) -> dict:
    """把「自动登记」的博主批量纳入追踪（``source`` 改回 manual）。

    Args:
        db: 数据库会话。
        blogger_ids: 目标博主 id 列表。

    Returns:
        ``{"promoted": 成功数, "skipped": [{"id", "name", "reason"}]}``；
        「本来就不是自动登记」与「不存在」都进 skipped 并写明原因。
    """
    if not blogger_ids:
        return {"promoted": 0, "skipped": []}
    rows = (
        await db.execute(select(Blogger).where(Blogger.id.in_(blogger_ids)).order_by(Blogger.id))
    ).scalars()
    promoted = 0
    skipped: list[dict] = []
    found: set[int] = set()
    for blogger in rows:
        found.add(blogger.id)
        if (blogger.source or "") != AUTO_BLOGGER_SOURCE:
            skipped.append(
                {
                    "id": blogger.id,
                    "name": blogger.name,
                    "reason": "不是「自动登记」的博主，无需纳入",
                }
            )
            continue
        blogger.source = "manual"
        promoted += 1
    for blogger_id in blogger_ids:
        if blogger_id not in found:
            skipped.append({"id": blogger_id, "name": f"# {blogger_id}", "reason": "博主不存在"})
    await db.commit()
    if promoted:
        await record_audit_log(
            action="promote_bloggers",
            target_type="blogger",
            count=promoted,
            detail=f"批量纳入追踪 {promoted} 位自动登记博主",
        )
    return {"promoted": promoted, "skipped": skipped}


async def batch_delete(db: AsyncSession, blogger_ids: list[int]) -> dict:
    """批量删除博主（**逐条走单条删除的校验**：有素材关联的一律拒绝）。

    Args:
        db: 数据库会话。
        blogger_ids: 目标博主 id 列表。

    Returns:
        ``{"deleted": 成功数, "blocked": [{"id", "name", "reason"}]}``。
    """
    deleted = 0
    blocked: list[dict] = []
    for blogger_id in blogger_ids:
        blogger = await db.get(Blogger, blogger_id)
        name = blogger.name if blogger is not None else f"# {blogger_id}"
        try:
            await blogger_service.delete(db, blogger_id)
            deleted += 1
        except PersonHasInspirationsError as exc:
            await db.rollback()
            blocked.append({"id": blogger_id, "name": name, "reason": str(exc)})
        except PersonNotFoundError:
            await db.rollback()
            blocked.append({"id": blogger_id, "name": name, "reason": "博主不存在"})
        except Exception as exc:  # noqa: BLE001 —— 单条失败不阻塞整批
            await db.rollback()
            logger.warning(f"批量删除博主 #{blogger_id} 失败：{exc}")
            blocked.append({"id": blogger_id, "name": name, "reason": f"删除失败：{exc}"})
    return {"deleted": deleted, "blocked": blocked}


async def avatar_coverage(db: AsyncSession) -> dict:
    """头像覆盖率：总人数 / 已有头像 / 现在能补（有素材且没头像）。

    Returns:
        ``{"total", "with_avatar", "fillable"}``。
    """
    total = (await db.execute(select(func.count()).select_from(Blogger))).scalar() or 0
    with_avatar = (
        await db.execute(
            select(func.count())
            .select_from(Blogger)
            .where(Blogger.avatar_path.is_not(None), Blogger.avatar_path != "")
        )
    ).scalar() or 0
    with_materials = (
        select(InspirationBlogger.blogger_id)
        .join(Inspiration, Inspiration.id == InspirationBlogger.inspiration_id)
        .where(NOT_DELETED)
        .distinct()
    )
    fillable = (
        await db.execute(
            select(func.count())
            .select_from(Blogger)
            .where(
                Blogger.id.in_(with_materials),
                func.coalesce(Blogger.avatar_path, "") == "",
            )
        )
    ).scalar() or 0
    return {"total": total, "with_avatar": with_avatar, "fillable": fillable}


async def clear_avatars(db: AsyncSession, blogger_ids: list[int] | None = None) -> dict:
    """批量清除头像（``avatar_path`` 置空 + 删文件）。

    只有「确实有头像」的才计入 cleared；头像文件在提交后才删，避免事务回滚后
    出现「库里有路径、文件已没了」的悬空记录。

    Args:
        db: 数据库会话。
        blogger_ids: 目标 id（None = 全部有头像的博主）。

    Returns:
        ``{"cleared": 清除数}``。
    """
    stmt = select(Blogger).where(Blogger.avatar_path.is_not(None), Blogger.avatar_path != "")
    if blogger_ids:
        stmt = stmt.where(Blogger.id.in_(blogger_ids))
    rows = list((await db.execute(stmt)).scalars().all())
    if not rows:
        return {"cleared": 0}
    ids = [b.id for b in rows]
    for blogger in rows:
        blogger.avatar_path = None
    await db.commit()
    for blogger_id in ids:
        delete_avatar_file(blogger_id)
    await record_audit_log(
        action="clear_avatars",
        target_type="blogger",
        count=len(ids),
        detail=f"批量清除博主头像 {len(ids)} 位",
    )
    return {"cleared": len(ids)}


async def duplicate_name_groups(db: AsyncSession) -> list[dict]:
    """同名博主建议：归一化昵称相同的多条记录分组返回（供合并/删除）。

    只给建议、不改数据：同名的两条未必是同一人（也可能就是重复登记），由用户判断。
    已经在同一个人物组里的账号不再提示（那正是「同一个人」的正确表达方式）。

    Args:
        db: 数据库会话。

    Returns:
        ``[{"key": 归一化昵称, "items": [{"id","name","platform","source",
        "inspiration_count","person_group_id","profile_url"}]}]``；按组内人数降序，
        最多 :data:`MAX_DUPLICATE_GROUPS` 组。
    """
    count_subq = (
        select(
            InspirationBlogger.blogger_id.label("bid"),
            func.count().label("n"),
        )
        .join(Inspiration, Inspiration.id == InspirationBlogger.inspiration_id)
        .where(NOT_DELETED)
        .group_by(InspirationBlogger.blogger_id)
        .subquery()
    )
    rows = (
        await db.execute(
            select(
                Blogger.id,
                Blogger.name,
                Blogger.platform,
                Blogger.source,
                Blogger.person_group_id,
                Blogger.profile_url,
                Blogger.created_at,
                func.coalesce(count_subq.c.n, 0).label("cnt"),
            ).outerjoin(count_subq, count_subq.c.bid == Blogger.id)
        )
    ).all()

    grouped: dict[str, list[dict]] = defaultdict(list)
    for (
        blogger_id,
        name,
        platform,
        source,
        group_id,
        profile_url,
        created_at,
        cnt,
    ) in rows:
        key = normalize_name(name or "")
        if not key:
            continue
        grouped[key].append(
            {
                "id": blogger_id,
                "name": name,
                "platform": platform,
                "source": source,
                "person_group_id": group_id,
                "profile_url": profile_url,
                "created_at": created_at,
                "inspiration_count": int(cnt or 0),
            }
        )

    groups: list[dict] = []
    for key, items in grouped.items():
        if len(items) < 2:
            continue
        # 已在同一组里的不算「待合并」：那已经表达过是同一人了
        group_ids = {item["person_group_id"] for item in items}
        if len(group_ids) == 1 and None not in group_ids:
            continue
        items.sort(key=lambda item: (-item["inspiration_count"], item["id"]))
        groups.append({"key": key, "items": items})
    groups.sort(key=lambda group: (-len(group["items"]), group["key"]))
    return groups[:MAX_DUPLICATE_GROUPS]


__all__ = [
    "AUTO_BLOGGER_SOURCE",
    "avatar_coverage",
    "batch_delete",
    "batch_promote",
    "clear_avatars",
    "duplicate_name_groups",
    "normalize_name",
]
