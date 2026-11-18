"""博主素材评分补全任务：给博主名下「未评分」素材批量补一个统一星级。

需求背景（2026-11-18）：人物页发现大量穿搭博主的素材尚未评分，人工逐张点太慢；
本任务按博主批量补分。核心口径是**只填空缺、绝不覆盖已评分**——用户已经打过的分
是人工判断，不能被批量操作抹掉（也因此不需要「覆盖」开关）。

规则：
- 目标博主：所有关联**未删除**素材、且至少有一条未评分素材的博主；
  **优先**处理素材总数 > :data:`MIN_RATING_SAMPLE`（5）的博主（按素材数升序，
  让刚够统计门槛的博主先有均分），其余素材较少的博主排在其后（同样按素材数升序），
  同数按 id 升序；
- 只更新 ``rating IS NULL OR rating = 0`` 且未删除的素材；
- 每个博主单独 commit 一次：进度可见、取消时已补的保留（取消检查在博主之间）；
- ``blogger_ids`` 显式指定时按给定范围补（不再套 >5 门槛——用户已明确选择）。

任务载荷 / 结果：
- 创建时 ``result`` 写入 ``rating`` / ``total`` / ``blogger_ids``（有序）/
  ``blogger_targets``（每个博主的素材数与未评分数）；
- 执行完成追加 ``updated_materials`` / ``updated_bloggers`` / ``skipped`` /
  ``failed`` / ``cancelled`` / ``bloggers``（逐博主明细，含状态与更新条数）。

边界：
- 创建后、执行前可能有素材被手动评分/删除：执行时按当时数据更新，实际更新数可能
  小于 ``total``，此时以真实更新数记 ``task.done``，任务结束仍置 progress=100；
- 单个博主更新失败只记 ``failed`` 并继续后面的博主，不拖垮整批；
- 该任务只写 ``inspirations.rating``，不动质量状态/标签/向量，失败重跑幂等
  （已补的素材 rating>0，不会再被选中）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inspiration import NOT_DELETED, Inspiration
from app.models.person import Blogger, InspirationBlogger
from app.models.task import TaskQueue
from app.services.person.base import MIN_RATING_SAMPLE
from app.services.task_runners.common import PermanentTaskError, is_cancelled, utcnow

logger = logging.getLogger(__name__)

"""任务类型标识：与 worker 的 TASK_HANDLERS、前端任务类型中文名保持同一字符串。"""
TASK_TYPE = "blogger_rating_backfill"


def _unrated_condition():
    """「未评分」判定：rating 为空或 0（0 是历史默认值，两种都视为没评过）。"""
    return or_(Inspiration.rating.is_(None), Inspiration.rating == 0)


@dataclass(frozen=True)
class RatingBackfillTarget:
    """一个待补分博主及其素材统计（排序/展示用）。"""

    blogger_id: int
    name: str
    total_materials: int
    unrated_materials: int


async def list_rating_backfill_targets(
    db: AsyncSession, blogger_ids: list[int] | None = None
) -> list[RatingBackfillTarget]:
    """查询本次可补分的博主。

    目标口径：所有关联未删除素材、且至少有一条未评分素材的博主。排序分两层：
    先排素材总数 > :data:`MIN_RATING_SAMPLE` 的博主（按素材数升序，先让刚够
    统计门槛的人有均分），再排其余博主（同样按素材数升序）；同数按博主 id 升序。
    显式传 ``blogger_ids`` 时只在该范围内查，排序规则不变。

    Args:
        db: 数据库会话。
        blogger_ids: 限定博主范围（None / 空列表 = 全部）。

    Returns:
        有序目标列表；无可补博主时为空列表。
    """
    unrated_count = func.sum(
        case((_unrated_condition(), 1), else_=0)
    ).label("unrated_materials")
    stmt = (
        select(
            InspirationBlogger.blogger_id,
            Blogger.name,
            func.count(Inspiration.id).label("total_materials"),
            unrated_count,
        )
        .join(Inspiration, Inspiration.id == InspirationBlogger.inspiration_id)
        .join(Blogger, Blogger.id == InspirationBlogger.blogger_id)
        .where(NOT_DELETED)
        .group_by(InspirationBlogger.blogger_id, Blogger.name)
    )
    if blogger_ids:
        stmt = stmt.where(InspirationBlogger.blogger_id.in_(blogger_ids))

    targets: list[RatingBackfillTarget] = []
    for blogger_id, name, total_materials, unrated_materials in (
        await db.execute(stmt)
    ).all():
        total = int(total_materials or 0)
        unrated = int(unrated_materials or 0)
        if unrated <= 0:
            continue  # 没有可补的素材，不必进任务
        targets.append(
            RatingBackfillTarget(
                blogger_id=int(blogger_id),
                name=str(name or f"#{blogger_id}"),
                total_materials=total,
                unrated_materials=unrated,
            )
        )
    # 第一层：素材数 > 5 的优先（这些博主的均分才能进入排行/筛选）；
    # 第二层：素材数升序（刚够门槛的先补，避免一上来卡在大博主上）；同数按 id。
    targets.sort(
        key=lambda item: (
            0 if item.total_materials > MIN_RATING_SAMPLE else 1,
            item.total_materials,
            item.blogger_id,
        )
    )
    return targets


async def create_rating_backfill_task(
    db: AsyncSession,
    rating: int,
    blogger_ids: list[int] | None = None,
) -> tuple[TaskQueue | None, int]:
    """创建博主素材评分补全任务（返回 (任务, 本次未评分素材总数)）。

    Args:
        db: 数据库会话。
        rating: 统一星级（1~5）。
        blogger_ids: 限定博主范围（None / 空列表 = 全部）。

    Returns:
        ``(任务, total)``；没有可补素材时返回 ``(None, 0)``，由路由层转 400。
    """
    if not 1 <= rating <= 5:
        raise ValueError(f"rating 必须在 1~5 之间（当前：{rating}）")

    targets = await list_rating_backfill_targets(db, blogger_ids)
    total = sum(item.unrated_materials for item in targets)
    if not targets or total <= 0:
        return None, 0

    task = TaskQueue(
        type=TASK_TYPE,
        status="pending",
        priority=0,
        progress=0,
        total=total,
        done=0,
        result={
            "rating": rating,
            "total": total,
            "blogger_ids": [item.blogger_id for item in targets],
            "blogger_targets": [
                {
                    "blogger_id": item.blogger_id,
                    "name": item.name,
                    "total_materials": item.total_materials,
                    "unrated_materials": item.unrated_materials,
                }
                for item in targets
            ],
        },
        max_retries=1,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    logger.info(
        "已创建博主素材评分补全任务: #%s，%s 位博主 / %s 条未评分素材，rating=%s",
        task.id,
        len(targets),
        total,
        rating,
    )
    return task, total


async def _apply_rating_to_blogger(
    db: AsyncSession, blogger_id: int, rating: int
) -> int:
    """把一个博主的未评分素材统一写成 ``rating``，返回实际更新条数。

    已评分素材（rating > 0）与垃圾桶素材不会被选中——「只填空缺、绝不覆盖」。
    """
    result = await db.execute(
        update(Inspiration)
        .where(
            Inspiration.id.in_(
                select(InspirationBlogger.inspiration_id).where(
                    InspirationBlogger.blogger_id == blogger_id
                )
            ),
            NOT_DELETED,
            _unrated_condition(),
        )
        .values(rating=rating)
    )
    return int(result.rowcount or 0)


def _empty_result(payload: dict, message: str) -> dict:
    """构造「无可处理对象」时的终态 result（字段与正常完成保持同构）。"""
    return {
        **payload,
        "updated_materials": 0,
        "updated_bloggers": 0,
        "skipped": 0,
        "failed": 0,
        "cancelled": False,
        "bloggers": [],
        "message": message,
    }


async def execute_rating_backfill(db: AsyncSession, task: TaskQueue) -> None:
    """执行评分补全：逐博主更新未评分素材，可取消，按博主分批 commit。

    Args:
        db: 数据库会话（worker 持有）。
        task: 已认领的任务记录。
    """
    payload = dict(task.result or {})
    raw_rating = payload.get("rating")
    if not isinstance(raw_rating, int) or not 1 <= raw_rating <= 5:
        raise PermanentTaskError(f"任务载荷 rating 非法：{raw_rating!r}（应为 1~5）")
    rating = raw_rating

    targets: list[dict] = list(payload.get("blogger_targets") or [])
    total = int(payload.get("total") or 0)
    task.total = total
    if not targets or total <= 0:
        task.progress = 100
        task.done = 0
        task.result = _empty_result(payload, "没有可补评分的素材")
        task.updated_at = utcnow()
        await db.commit()
        return

    task_id = task.id
    details: list[dict] = []
    updated_materials = 0
    updated_bloggers = 0
    skipped = 0
    failed = 0
    cancelled = False

    for target in targets:
        if await is_cancelled(db, task):
            cancelled = True
            break
        try:
            blogger_id = int(target.get("blogger_id") or 0)
        except (TypeError, ValueError) as exc:
            logger.warning("跳过非法博主 id %r：%s", target.get("blogger_id"), exc)
            failed += 1
            details.append(
                {
                    "blogger_id": target.get("blogger_id"),
                    "name": str(target.get("name") or ""),
                    "status": "failed",
                    "updated": 0,
                    "reason": f"博主 id 非法：{exc}",
                }
            )
            continue

        entry = {
            "blogger_id": blogger_id,
            "name": str(target.get("name") or f"#{blogger_id}"),
        }
        try:
            count = await _apply_rating_to_blogger(db, blogger_id, rating)
        except Exception as exc:  # noqa: BLE001 —— 单个博主失败不拖垮整批
            logger.warning("博主评分补全失败 #%s：%s", blogger_id, exc)
            await db.rollback()
            # rollback 会 expire 任务实例；重新取回，后续进度字段才能正常写回
            fresh = await db.get(TaskQueue, task_id)
            if fresh is None:
                raise PermanentTaskError("任务记录在评分补全过程中消失") from exc
            task = fresh
            failed += 1
            details.append(
                {**entry, "status": "failed", "updated": 0, "reason": str(exc)[:200]}
            )
            await db.commit()
            continue

        if count > 0:
            updated_materials += count
            updated_bloggers += 1
            details.append({**entry, "status": "updated", "updated": count})
        else:
            # 创建任务后素材可能被手动评分/删除，或删光了关联：这里如实记跳过
            skipped += 1
            details.append(
                {
                    **entry,
                    "status": "skipped",
                    "updated": 0,
                    "reason": "执行时已无可补的未评分素材（可能已被手动评分）",
                }
            )

        task.done = min(updated_materials, total)
        task.progress = min(100, round(updated_materials / total * 100))
        task.updated_at = utcnow()
        await db.commit()  # 按博主分批：进度可见、取消时已补的保留

    task.result = {
        **payload,
        "rating": rating,
        "total": total,
        "blogger_ids": [item.get("blogger_id") for item in targets],
        "updated_materials": updated_materials,
        "updated_bloggers": updated_bloggers,
        "skipped": skipped,
        "failed": failed,
        "cancelled": cancelled,
        "bloggers": details,
    }
    task.updated_at = utcnow()
    if cancelled:
        # worker 见 status != running 不会覆盖为 success，并按 cancelled 广播终态
        task.status = "cancelled"
        await db.commit()
        logger.info(
            "博主素材评分补全已取消: #%s 已更新 %s 条 / %s 条",
            task.id,
            updated_materials,
            total,
        )
        return

    task.done = updated_materials
    task.progress = 100
    await db.commit()
    logger.info(
        "博主素材评分补全完成: #%s 更新 %s 条素材 / %s 位博主（跳过 %s 位、失败 %s 位）",
        task.id,
        updated_materials,
        updated_bloggers,
        skipped,
        failed,
    )
