"""灵感素材垃圾桶：软删除、恢复、批量移入、清空与物理删除。

事务边界约定（与其余删除路径一致）：
「先 DB 提交，后文件/向量操作」——软删除标记/物理删除记录先落库提交，
成功后再移动或删除磁盘文件，避免「文件已动但事务回滚」产生悬空记录。
"""

import logging
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.inspiration import Inspiration, utcnow
from app.models.person import InspirationBlogger, InspirationModel
from app.models.tag import InspirationTag
from app.services.audit_service import record_audit_log
from app.services.file_service import (
    delete_files,
    delete_files_counting,
    move_to_trash,
    restore_from_trash,
)
from app.services.inspiration_query import load_inspiration_full
from app.services.inspiration_state import (
    _mark_restored,
    _mark_trashed,
    _resolve_trash_reason,
)
from app.services.scraper_seen_service import seal_urls

logger = logging.getLogger(__name__)


async def delete_rejected_inspirations(db: AsyncSession) -> dict:
    """将全部质量审核被拒绝（rejected）的素材批量移入垃圾桶（软删除，可恢复）。

    与素材库垃圾桶语义一致：标记 deleted_at / trash_reason（rejected → 「质量差」，
    供负样本学习使用）、文件移入 storage/trash/、向量保留；不再物理删除，
    避免 AI 误判导致素材不可恢复地丢失，也避免写入采集墓碑后无法重新采集。

    分批处理（每批 100 条）：避免大库下一次性全量加载与文件移动阻塞过久。
    """
    result = await db.execute(
        select(Inspiration).where(
            Inspiration.quality_status == "rejected",
            Inspiration.deleted_at.is_(None),
        )
    )
    rejected = result.scalars().all()

    if not rejected:
        return {"trashed": 0, "message": "没有已拒绝的素材"}

    total_trashed = 0
    for start in range(0, len(rejected), 100):
        batch = rejected[start:start + 100]
        # 标记软删除（reason 自动推断：rejected → 质量差；来源标记为自动移动）。
        # 三字段经 _mark_trashed 单点写入，保证状态转移合法
        trashed_items: list[Inspiration] = []
        for insp in batch:
            if insp.deleted_at is not None:
                continue
            _mark_trashed(insp, _resolve_trash_reason(None, insp), "auto")
            trashed_items.append(insp)
        if not trashed_items:
            continue

        # 先提交软删除标记与来源 URL 墓碑（同一事务），提交成功后再移动文件，
        # 避免「文件已移走但事务回滚/失败」产生指向不存在文件的记录
        await seal_urls(db, [insp.source_url for insp in trashed_items if insp.source_url])
        await db.commit()

        # 移动文件到垃圾桶目录；失败仅记日志不阻断软删除（恢复时按 DB 路径自愈）
        paths_changed = False
        for insp in trashed_items:
            try:
                new_file = move_to_trash(insp.file_path, insp.id)
                if new_file:
                    insp.file_path = new_file
                    paths_changed = True
            except OSError as e:
                logger.warning(f"移动主文件到垃圾桶失败 {insp.id}: {e}")
            try:
                new_thumb = move_to_trash(insp.thumbnail_path, insp.id, suffix="_thumb")
                if new_thumb:
                    insp.thumbnail_path = new_thumb
                    paths_changed = True
            except OSError as e:
                logger.warning(f"移动缩略图到垃圾桶失败 {insp.id}: {e}")

        if paths_changed:
            await db.commit()
        total_trashed += len(trashed_items)

    # 记录审计：批量移入垃圾桶属破坏性批量操作，留痕便于追溯（仅实际移入时）
    if total_trashed:
        await record_audit_log(
            action="delete_rejected",
            count=total_trashed,
            detail="批量将质量审核被拒绝的素材移入垃圾桶（软删除，可恢复）",
        )

    return {
        "trashed": total_trashed,
        "message": f"已将 {total_trashed} 个已拒绝素材移入垃圾桶",
    }


async def batch_trash_inspirations(
    db: AsyncSession,
    inspiration_ids: list[str],
    reason: str | None = None,
    source: str = "manual",
) -> dict:
    """批量将素材移入垃圾桶（逐个复用单条软删除逻辑，容忍部分失败）。

    单条素材的文件移动与软删除已在 trash_inspiration 内完成并各自提交；
    此处逐条调用，不存在/已在垃圾桶中的 ID 计入 skipped，不影响其余素材。

    参数:
        source: 移入来源（manual 手动 / auto 质量审核自动移动），逐条透传给 trash_inspiration
    """
    trashed = 0
    skipped = 0
    for inspiration_id in inspiration_ids:
        try:
            # audit=False：批量路径由下方汇总写一条 batch_trash 审计，避免逐条留痕噪音
            await trash_inspiration(db, inspiration_id, reason, source=source, audit=False)
            trashed += 1
        except Exception as e:
            # 与注释「容忍部分失败」语义一致：单条失败（404/409 或异常）计入
            # skipped 继续处理其余素材，避免单条异常中断整个批次
            skipped += 1
            if not isinstance(e, HTTPException):
                logger.warning(
                    f"批量移入垃圾桶单条失败（计入跳过）{inspiration_id}: {e}"
                )

    # 记录审计：批量移入垃圾桶（软删除）也纳入审计，便于追溯批量整理动作
    if trashed > 0:
        await record_audit_log(
            action="batch_trash",
            count=trashed,
            detail=f"跳过 {skipped} 个（不存在或已在垃圾桶）" if skipped else None,
        )

    return {"trashed": trashed, "skipped": skipped}


async def delete_inspiration(db: AsyncSession, inspiration_id: str) -> None:
    """删除灵感素材及其对应的磁盘文件，不存在则抛出 404。

    仅允许删除垃圾桶中的素材（物理删除，不可恢复）：
    - active 素材必须先走 trash_inspiration 移入垃圾桶（软删除可恢复），
      防止误调用绕过软删除生命周期直接永久删除；
    - 物理删除属不可恢复的破坏性操作，写入审计留痕（与 trash/restore/purge 一致）。
    """
    from app.services import vector_store

    result = await db.execute(
        select(Inspiration).where(Inspiration.id == inspiration_id)
    )
    inspiration = result.scalar_one_or_none()
    if not inspiration:
        raise HTTPException(status_code=404, detail="灵感素材未找到")
    if inspiration.deleted_at is None:
        raise HTTPException(
            status_code=409,
            detail="素材不在垃圾桶中，请先移入垃圾桶（软删除）后再彻底删除",
        )

    # 写入墓碑表（防止重复采集）
    await seal_urls(db, [inspiration.source_url])

    # 先删除数据库记录并提交（与 purge_trash / batch_delete 等路径一致：DB
    # 落库成功后再删磁盘文件/向量），避免「文件已删但事务提交失败」产生
    # 指向不存在文件的悬空记录
    await db.delete(inspiration)
    await db.commit()

    # 记录审计：物理删除素材属不可恢复的破坏性操作，必须留痕（写入在主事务提交后）
    await record_audit_log(
        action="delete",
        count=1,
        detail=f"彻底删除素材 {inspiration_id}（垃圾桶中物理删除）",
    )

    # 提交成功后同步删除向量库中的文本/图像向量（LanceDB 未安装时静默跳过）
    if vector_store.is_lancedb_available():
        await vector_store.delete_inspiration_vectors(inspiration.id)

    # 物理删除文件（删除失败仅记日志，不抛异常）
    delete_files(inspiration.file_path, inspiration.thumbnail_path)

    # 同步清理关键帧目录（视频素材；非视频目录不存在，幂等）
    from app.services.video_service import cleanup_keyframes

    await cleanup_keyframes(inspiration.id)


async def trash_inspiration(
    db: AsyncSession,
    inspiration_id: str,
    reason: str | None,
    source: str = "manual",
    audit: bool = True,
) -> Inspiration:
    """将素材移入垃圾桶（软删除）：文件移入 trash/，标记 deleted_at 与 trash_reason。

    向量保留不删除（阶段 2 负样本学习依赖垃圾桶素材的 CLIP 图像向量），
    恢复时无需重建、清空时才删除向量。软删除即写入来源 URL 墓碑，采集器
    后续遇到该 URL 会直接跳过，不再重复采集。

    参数:
        source: 移入来源（manual 手动移入 / auto 质量审核自动移动），垃圾桶据此展示来源
        audit: 是否写入单条审计（批量入口逐条调用时应传 False，由批量汇总一条审计）
    """
    inspiration = await load_inspiration_full(db, inspiration_id)
    if not inspiration:
        raise HTTPException(status_code=404, detail="灵感素材未找到")
    if inspiration.deleted_at is not None:
        raise HTTPException(status_code=409, detail="素材已在垃圾桶中")

    resolved = _resolve_trash_reason(reason, inspiration)

    # 先提交软删除标记（DB 落库），提交成功后再移动文件，避免「文件已移走但事务
    # 回滚/失败」导致 DB 仍指向原路径的悬空记录（与 delete_inspiration 的顺序一致）。
    # 三字段通过 _mark_trashed 单点写入（含状态转移合法性断言）
    _mark_trashed(inspiration, resolved, source)
    # 进垃圾桶视为「垃圾素材」，立即写入来源 URL 墓碑，防止采集器重复采集
    await seal_urls(db, [inspiration.source_url])
    await db.commit()

    # 移动文件失败时记录日志但不回滚软删除：文件仍在原目录，DB 路径未变仍指向
    # 实际位置；恢复时 restore_from_trash 找不到 trash/ 下的文件会自动保持原路径，
    # 形成自愈。文件缺失时 move_to_trash 返回 None，同样保留原路径。
    paths_changed = False
    try:
        new_file = move_to_trash(inspiration.file_path, inspiration.id)
        if new_file:
            inspiration.file_path = new_file
            paths_changed = True
    except OSError as e:
        logger.warning(f"移动主文件到垃圾桶失败 {inspiration.id}: {e}")
    try:
        new_thumb = move_to_trash(inspiration.thumbnail_path, inspiration.id, suffix="_thumb")
        if new_thumb:
            inspiration.thumbnail_path = new_thumb
            paths_changed = True
    except OSError as e:
        logger.warning(f"移动缩略图到垃圾桶失败 {inspiration.id}: {e}")

    if paths_changed:
        await db.commit()
    await db.refresh(inspiration)

    # 记录审计：单条移入垃圾桶留痕，链路可回放（批量入口逐条调用时经 audit=False 跳过）
    if audit:
        await record_audit_log(
            action="trash",
            count=1,
            detail=f"原因：{resolved}；来源：{'自动移动' if source == 'auto' else '手动移入'}",
        )
    return inspiration


async def restore_inspiration(db: AsyncSession, inspiration_id: str) -> Inspiration:
    """从垃圾桶恢复素材：文件移回媒体目录，清除 deleted_at 与 trash_reason。"""
    inspiration = await load_inspiration_full(db, inspiration_id)
    if not inspiration:
        raise HTTPException(status_code=404, detail="灵感素材未找到")
    if inspiration.deleted_at is None:
        raise HTTPException(status_code=409, detail="素材不在垃圾桶中")

    # 恢复会重新占用 source_platform_id 的唯一性（部分唯一索引仅约束未删除素材）。
    # 若垃圾桶期间同平台 ID 已被新素材重新入库，恢复将触发唯一索引 IntegrityError，
    # 这里前置查重并返回 409，避免落成 500。
    if inspiration.source_platform_id:
        dup = await db.execute(
            select(Inspiration.id)
            .where(
                Inspiration.source_platform_id == inspiration.source_platform_id,
                Inspiration.deleted_at.is_(None),
                Inspiration.id != inspiration.id,
            )
            .limit(1)
        )
        if dup.scalar_one_or_none():
            raise HTTPException(
                status_code=409,
                detail="该平台 ID 已被新素材占用，无法恢复（请先删除新素材后再试）",
            )

    # 先提交恢复标记（DB 落库），提交成功后再移动文件；移动失败仅记日志，
    # 文件留在 trash/ 由后续清空任务兜底清理，恢复本身不受阻断。
    # 三字段通过 _mark_restored 单点清除（含状态转移合法性断言）
    prev_reason = inspiration.trash_reason
    prev_source = inspiration.trash_source
    _mark_restored(inspiration)
    # 恢复后解除来源 URL 墓碑（与移入时的 seal_urls 对称）：
    # 否则素材恢复后其 URL 仍被采集器/导入链路永久拦截（状态转移缺口）。
    # 墓碑解除不影响内容去重——同 URL 重新采集会命中 content_hash 查重跳过。
    if inspiration.source_url:
        from app.models.scraper import ScraperSeenURL

        await db.execute(
            delete(ScraperSeenURL).where(
                ScraperSeenURL.source_url == inspiration.source_url
            )
        )
    try:
        await db.commit()
    except IntegrityError:
        # 并发竞态：恢复期间同平台 ID 被新素材抢先入库，撞部分唯一索引。
        # 恢复标记随事务回滚（素材仍在垃圾桶），转 409 提示用户重试
        raise HTTPException(
            status_code=409,
            detail="恢复失败：该平台 ID 已被其它素材占用（并发冲突），请刷新后重试",
        )

    paths_changed = False
    try:
        new_file = restore_from_trash(inspiration.file_path)
        if new_file:
            inspiration.file_path = new_file
            paths_changed = True
    except OSError as e:
        logger.warning(f"恢复主文件失败 {inspiration.id}: {e}")
    try:
        new_thumb = restore_from_trash(inspiration.thumbnail_path)
        if new_thumb:
            inspiration.thumbnail_path = new_thumb
            paths_changed = True
    except OSError as e:
        logger.warning(f"恢复缩略图失败 {inspiration.id}: {e}")

    if paths_changed:
        await db.commit()
    await db.refresh(inspiration)

    # 记录审计：恢复素材留痕（含原删除原因/来源），链路可回放
    await record_audit_log(
        action="restore",
        count=1,
        detail=f"原原因：{prev_reason or '未知'}；原来源：{'自动移动' if prev_source == 'auto' else '手动移入'}",
    )
    return inspiration


async def list_trash(
    db: AsyncSession,
    page: int = 1,
    size: int = 50,
    reason: str | None = None,
) -> tuple[list[Inspiration], int]:
    """分页查询垃圾桶中的素材（按删除时间倒序），支持按删除原因筛选。"""
    query = (
        select(Inspiration)
        .options(
            selectinload(Inspiration.tags).selectinload(InspirationTag.tag),
            selectinload(Inspiration.bloggers).selectinload(InspirationBlogger.blogger),
            selectinload(Inspiration.models).selectinload(InspirationModel.model),
        )
        .where(Inspiration.deleted_at.isnot(None))
    )
    if reason:
        query = query.where(Inspiration.trash_reason == reason)

    total = (
        await db.execute(select(func.count()).select_from(query.subquery()))
    ).scalar() or 0

    query = query.order_by(Inspiration.deleted_at.desc()).offset((page - 1) * size).limit(size)
    result = await db.execute(query)
    return result.unique().scalars().all(), total


async def purge_trash(db: AsyncSession, only_expired: bool = False) -> dict:
    """彻底清空垃圾桶（物理删除文件与数据库记录，释放磁盘空间）。

    参数:
        only_expired: 为 True 时仅清理已超过保留期（trash_retention_days）的素材，
            用于到期自动清理；为 False 时清空全部垃圾桶素材。

    返回:
        {"deleted": 删除数量, "freed_bytes": 释放字节数}
    """
    from app.services import vector_store

    conds = [Inspiration.deleted_at.isnot(None)]
    if only_expired:
        # 保留期 <= 0 表示禁用自动回收：不清理任何素材，直接返回
        if settings.trash_retention_days <= 0:
            return {"deleted": 0, "freed_bytes": 0, "message": "自动回收已禁用"}
        cutoff = utcnow() - timedelta(days=settings.trash_retention_days)
        conds.append(Inspiration.deleted_at < cutoff)

    result = await db.execute(select(Inspiration).where(*conds))
    items = result.scalars().all()
    if not items:
        return {"deleted": 0, "freed_bytes": 0, "message": "垃圾桶已空"}

    deleted_ids = [insp.id for insp in items]
    urls_to_seal = [insp.source_url for insp in items if insp.source_url]

    # 先删数据库记录并写墓碑（同一事务），提交成功后再物理删除文件
    for insp in items:
        await db.delete(insp)
    await seal_urls(db, urls_to_seal)
    await db.commit()

    freed_bytes = 0
    for insp in items:
        freed_bytes += delete_files_counting(insp.file_path, insp.thumbnail_path)

    # 删除向量库中的文本/图像向量（垃圾桶素材向量在清空时一并清理）
    await vector_store.delete_inspiration_vectors_batch(deleted_ids)

    # 同步清理关键帧目录（视频素材；非视频目录不存在，幂等）
    from app.services.video_service import cleanup_keyframes_batch

    await cleanup_keyframes_batch(deleted_ids)

    # 记录审计：清空垃圾桶（含定时自动清理）属于不可恢复的破坏性操作
    await record_audit_log(
        action="empty_trash",
        count=len(items),
        freed_bytes=freed_bytes,
        detail="仅清理过期素材" if only_expired else "清空全部垃圾桶",
    )

    return {"deleted": len(items), "freed_bytes": freed_bytes}
