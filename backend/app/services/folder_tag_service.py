"""抖音收藏夹 → 穿搭大标签映射（2026-11-24）。

口径（用户已确认）：
- 映射按**收藏夹名**存（首次「获取我的收藏」前就能预配置）；
- 未配置的收藏夹首次同步按同名建标签并打上，同时 ``needs_confirm=True`` 进待确认；
- 作品移出收藏夹后标签**保留**（快照语义，不自动摘除）；
- 同名标签已属于其它类别时不静默复用，返回 409 让前端给三选一；
- 打标失败只记 warning，不影响 f2 同步入库。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.collection import DOUYIN_AUTO_SOURCE, Collection, CollectionItem
from app.models.folder_tag import (
    MODE_PENDING,
    MODE_SKIP,
    MODE_TAG,
    MODES,
    DouyinFolderTagMap,
)
from app.models.inspiration import Inspiration
from app.models.tag import InspirationTag, Tag
from app.services.inspiration_tags import batch_add_tags
from app.services.tag_crud import get_or_create_tag
from app.utils.tag_normalizer import normalize_tag_name_async

logger = logging.getLogger(__name__)

OUTFIT_CATEGORY = "outfit"
# 关联来源：收藏夹自动打标。重新分析只清 ai_generated，不会误删这类关联。
LINK_SOURCE_COLLECTION = "collection"

# 冲突提示里的类别中文名（与 shared/types/inspiration.ts 的 TAG_CATEGORY_LABELS 对齐）
_CATEGORY_LABELS: dict[str, str] = {
    "style": "风格",
    "item_type": "单品",
    "color": "颜色",
    "body_part": "穿着方式",
    "fit": "版型",
    "attribute": "属性",
    "atmosphere": "氛围",
    "expression": "模特表情",
    "leg_posture": "腿部姿态",
    "design_detail": "款式细节",
    "material": "面料",
    "outfit": "穿搭大标签",
    "free": "自定义",
}


def _category_label(category: str | None) -> str:
    return _CATEGORY_LABELS.get(category or "", category or "未知")


# ── 查询 ──


async def get_mapping(db: AsyncSession, folder_name: str) -> DouyinFolderTagMap | None:
    """按收藏夹名取映射行。"""
    return (
        await db.execute(
            select(DouyinFolderTagMap).where(
                DouyinFolderTagMap.folder_name == folder_name.strip()
            )
        )
    ).scalar_one_or_none()


async def _local_folder_collections(db: AsyncSession) -> dict[str, Collection]:
    """本地已建立的抖音二级收藏夹：收藏夹名 → 合集。"""
    rows = (
        await db.execute(
            select(Collection).where(
                Collection.auto_source == DOUYIN_AUTO_SOURCE,
                Collection.parent_id.is_not(None),
            )
        )
    ).scalars().all()
    return {row.name: row for row in rows}


def _known_folder_names() -> list[str]:
    """f2 侧已扫描到的收藏夹名（读 ``_collect_folders.json`` 快照，不联网）。"""
    from app.services import f2
    from app.services.task_runners.f2_personal import load_collect_folder_map

    try:
        folder_map = load_collect_folder_map(f2.DEFAULT_F2_COLLECT_ROOT)
    except Exception as exc:  # noqa: BLE001 —— 清单缺失/损坏时只是少列几行
        logger.debug("读取抖音收藏夹清单失败，忽略：%s", exc)
        return []
    return sorted(folder_map)


async def _row_dict(db: AsyncSession, row: DouyinFolderTagMap) -> dict:
    """映射行序列化（含标签名/类别与本地合集信息）。"""
    tag = await db.get(Tag, row.tag_id) if row.tag_id else None
    local = (
        await db.execute(
            select(Collection).where(
                Collection.name == row.folder_name,
                Collection.auto_source == DOUYIN_AUTO_SOURCE,
                Collection.parent_id.is_not(None),
            )
        )
    ).scalar_one_or_none()
    item_count = None
    if local is not None:
        item_count = (
            await db.execute(
                select(func.count())
                .select_from(CollectionItem)
                .join(Inspiration, Inspiration.id == CollectionItem.inspiration_id)
                .where(
                    CollectionItem.collection_id == local.id,
                    Inspiration.deleted_at.is_(None),
                )
            )
        ).scalar() or 0
    return {
        "folder_name": row.folder_name,
        "mode": row.mode,
        "needs_confirm": row.needs_confirm,
        "tag_id": row.tag_id,
        "tag_name": tag.name if tag else None,
        "tag_category": tag.category if tag else None,
        "local_collection_id": local.id if local else None,
        "local_item_count": item_count,
    }


async def list_mappings(db: AsyncSession) -> list[dict]:
    """映射总览：已有映射 + 本地抖音夹 + f2 已扫描到的夹名（含未导入的）。"""
    rows = (await db.execute(select(DouyinFolderTagMap))).scalars().all()
    by_name = {row.folder_name: row for row in rows}
    local = await _local_folder_collections(db)
    known = await asyncio.to_thread(_known_folder_names)

    names: list[str] = sorted(set(by_name) | set(local) | set(known))
    out: list[dict] = []
    for name in names:
        row = by_name.get(name)
        if row is not None:
            out.append(await _row_dict(db, row))
            continue
        # 还没建映射：显示为待处理（本地合集成员数直接读）
        item_count = None
        collection = local.get(name)
        if collection is not None:
            item_count = (
                await db.execute(
                    select(func.count())
                    .select_from(CollectionItem)
                    .join(Inspiration, Inspiration.id == CollectionItem.inspiration_id)
                    .where(
                        CollectionItem.collection_id == collection.id,
                        Inspiration.deleted_at.is_(None),
                    )
                )
            ).scalar() or 0
        out.append(
            {
                "folder_name": name,
                "mode": MODE_PENDING,
                "needs_confirm": False,
                "tag_id": None,
                "tag_name": None,
                "tag_category": None,
                "local_collection_id": collection.id if collection else None,
                "local_item_count": item_count,
            }
        )
    return out


# ── 标签解析 ──


async def _ensure_outfit_tag(
    db: AsyncSession, name: str, *, reuse_existing_category: bool
) -> tuple[Tag | None, dict | None]:
    """按名称取/建穿搭大标签。

    Returns:
        ``(tag, conflict)``：冲突时 tag 为 None，conflict 为 ``{name, category, label}``。
    """
    effective = (await normalize_tag_name_async(db, name.strip())).strip()
    if not effective:
        raise HTTPException(status_code=400, detail="标签名称不能为空")
    existing = (
        await db.execute(select(Tag).where(Tag.name == effective))
    ).scalar_one_or_none()
    if existing is None:
        tag = await get_or_create_tag(db, effective, OUTFIT_CATEGORY, "manual")
        return tag, None
    if existing.category == OUTFIT_CATEGORY:
        return existing, None
    if not reuse_existing_category:
        return None, {
            "name": effective,
            "category": existing.category,
            "label": _category_label(existing.category),
        }
    # 显式复用：把该标签改为穿搭大标签（会影响所有引用它的素材，前端已二次确认）。
    # 必须走 tag_service.update_tag：它会写操作历史（可回滚）并做冲突检测，
    # 直接改 tag.category 会让标签治理的「操作历史」查不到这次改动。
    from app.services import tag_service

    tag = await tag_service.update_tag(db, existing.id, category=OUTFIT_CATEGORY)
    return tag, None


def _conflict_detail(conflict: dict) -> str:
    return (
        f"名称「{conflict['name']}」已被「{conflict['label']}」类标签占用；"
        "请选择：复用并改为穿搭大标签 / 另起一个名称 / 跳过此收藏夹"
    )


# ── 写入 ──


async def upsert_mapping(
    db: AsyncSession,
    folder_name: str,
    *,
    mode: str,
    tag_id: int | None = None,
    new_tag_name: str | None = None,
    reuse_existing_category: bool = False,
) -> dict:
    """新建/更新映射。``mode='tag'`` 时必须能解析出穿搭大标签。"""
    folder_name = folder_name.strip()
    if not folder_name:
        raise HTTPException(status_code=400, detail="收藏夹名称不能为空")
    if mode not in MODES:
        raise HTTPException(status_code=400, detail=f"不支持的映射模式：{mode}")

    row = await get_mapping(db, folder_name)
    if row is None:
        row = DouyinFolderTagMap(folder_name=folder_name, mode=MODE_PENDING)
        db.add(row)

    if mode == MODE_SKIP:
        row.mode = MODE_SKIP
        row.tag_id = None
        row.needs_confirm = False
    elif mode == MODE_PENDING:
        row.mode = MODE_PENDING
        row.tag_id = None
        row.needs_confirm = False
    else:
        if tag_id is not None:
            tag = await db.get(Tag, tag_id)
            if tag is None:
                raise HTTPException(status_code=404, detail="标签未找到")
            if tag.category != OUTFIT_CATEGORY:
                if not reuse_existing_category:
                    raise HTTPException(
                        status_code=409,
                        detail=_conflict_detail(
                            {
                                "name": tag.name,
                                "category": tag.category,
                                "label": _category_label(tag.category),
                            }
                        ),
                    )
                from app.services import tag_service

                tag = await tag_service.update_tag(
                    db, tag.id, category=OUTFIT_CATEGORY
                )
        else:
            tag, conflict = await _ensure_outfit_tag(
                db,
                new_tag_name or folder_name,
                reuse_existing_category=reuse_existing_category,
            )
            if conflict is not None:
                raise HTTPException(status_code=409, detail=_conflict_detail(conflict))
            assert tag is not None
        row.mode = MODE_TAG
        row.tag_id = tag.id
        row.needs_confirm = False

    await db.commit()
    await db.refresh(row)
    return await _row_dict(db, row)


async def delete_mapping(db: AsyncSession, folder_name: str) -> dict:
    """删除映射（该夹回到待处理；已打的标签关联保留）。"""
    row = await get_mapping(db, folder_name)
    if row is None:
        raise HTTPException(status_code=404, detail="映射不存在")
    await db.delete(row)
    await db.commit()
    return {"folder_name": folder_name, "deleted": True}


async def folder_member_ids(db: AsyncSession, folder_name: str) -> list[str]:
    """本地该收藏夹的当前成员 ID（未删除素材）。"""
    local = (
        await db.execute(
            select(Collection).where(
                Collection.name == folder_name.strip(),
                Collection.auto_source == DOUYIN_AUTO_SOURCE,
                Collection.parent_id.is_not(None),
            )
        )
    ).scalar_one_or_none()
    if local is None:
        return []
    return list(
        (
            await db.execute(
                select(CollectionItem.inspiration_id)
                .join(Inspiration, Inspiration.id == CollectionItem.inspiration_id)
                .where(
                    CollectionItem.collection_id == local.id,
                    Inspiration.deleted_at.is_(None),
                )
            )
        ).scalars().all()
    )


async def apply_folder_tags(
    db: AsyncSession, folder_name: str, inspiration_ids: list[str]
) -> dict:
    """给素材打上该收藏夹映射的大标签（幂等，只追加不覆盖）。"""
    row = await get_mapping(db, folder_name)
    if row is None or row.mode != MODE_TAG:
        return {"applied": 0, "reason": "该收藏夹未配置大标签"}
    # 标签被删时 FK 已把 tag_id 置空；必须先判 tag 再判 tag_id，否则会卡在
    # 「已映射但未设置」且永远走不到降级分支
    tag = await db.get(Tag, row.tag_id) if row.tag_id else None
    if tag is None:
        row.mode = MODE_PENDING
        row.tag_id = None
        row.needs_confirm = True
        await db.commit()
        return {"applied": 0, "reason": "映射的标签已被删除，请重新选择"}
    result = await batch_add_tags(
        db,
        [str(i) for i in inspiration_ids if i],
        [tag.name],
        category=OUTFIT_CATEGORY,
        source="manual",
        link_source=LINK_SOURCE_COLLECTION,
    )
    return {"applied": int(result.get("added") or 0), "skipped": int(result.get("skipped") or 0)}


async def apply_on_sync(
    db: AsyncSession, folder_name: str, inspiration_ids: list[str]
) -> dict:
    """f2 同步时的入口：按映射打标；无映射时按同名建标签并标记待确认。

    - ``skip`` → 不动；
    - 无映射 → 同名建标签 + 打标 + ``needs_confirm=True``；同名类别冲突则不建关联，
      行置 ``pending`` 并在返回值里带冲突原因（由调用方写 notice）；
    - 失败由调用方兜底，不阻断入库。
    """
    if not inspiration_ids:
        return {"applied": 0}
    folder_name = folder_name.strip()
    row = await get_mapping(db, folder_name)
    if row is not None and row.mode == MODE_SKIP:
        return {"applied": 0, "skipped": True}
    if row is None:
        tag, conflict = await _ensure_outfit_tag(
            db, folder_name, reuse_existing_category=False
        )
        row = DouyinFolderTagMap(
            folder_name=folder_name,
            mode=MODE_TAG if tag is not None else MODE_PENDING,
            tag_id=tag.id if tag is not None else None,
            needs_confirm=True,
        )
        db.add(row)
        await db.commit()
        if tag is None:
            return {
                "applied": 0,
                "needs_confirm": True,
                "conflict": conflict["name"] if conflict else folder_name,
            }
    result = await apply_folder_tags(db, folder_name, inspiration_ids)
    return {**result, "needs_confirm": bool(row.needs_confirm)}


async def apply_all(db: AsyncSession, folder_name: str | None = None) -> dict:
    """按当前成员重打：指定一个收藏夹或全部已映射的收藏夹。"""
    if folder_name:
        names = [folder_name.strip()]
    else:
        rows = (
            await db.execute(
                select(DouyinFolderTagMap).where(DouyinFolderTagMap.mode == MODE_TAG)
            )
        ).scalars().all()
        names = [row.folder_name for row in rows]
    total_applied = 0
    details: list[dict] = []
    for name in names:
        ids = await folder_member_ids(db, name)
        result = await apply_folder_tags(db, name, ids)
        total_applied += int(result.get("applied") or 0)
        details.append({"folder_name": name, **result})
    return {"applied": total_applied, "folders": details}


async def tag_impact(db: AsyncSession, tag_id: int) -> dict:
    """标签影响面（改名/改类别前给前端确认）。"""
    tag = await db.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail="标签未找到")
    usage = (
        await db.execute(
            select(func.count())
            .select_from(InspirationTag)
            .where(InspirationTag.tag_id == tag_id)
        )
    ).scalar() or 0
    folder_rows = (
        await db.execute(
            select(DouyinFolderTagMap.folder_name).where(
                DouyinFolderTagMap.tag_id == tag_id
            )
        )
    ).scalars().all()
    return {
        "tag_id": tag.id,
        "name": tag.name,
        "category": tag.category,
        "usage_count": int(usage),
        "mapped_folders": list(folder_rows),
    }
