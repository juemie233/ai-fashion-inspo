"""手机图裁剪服务：一键裁剪手动上传素材中的手机全屏截图。

背景
    手动上传的图片中有一部分是手机全屏截图，画面里混入了状态栏、
    底部导航栏等与穿搭无关的区域。本服务用于就地裁剪这类素材的
    顶部/底部多余区域，并同步更新缩略图、内容哈希、主色调与向量。

    核心逻辑从 ``scripts/crop_screenshots.py`` 迁移而来（该脚本保留为
    命令行入口，扫描/裁剪实现与本服务共用同一份代码）。

约定与保护:
    - 仅处理 ``source_type=manual_upload``、未删除、竖屏比例（高/宽 ≥ 1.75）
      的图片素材；标签、收藏、来源等信息完全不动。
    - 原图先备份到 ``storage/_crop_backup/{时间戳}/``，误操作可手动恢复。
    - 裁剪结果若与库中其他素材内容重复，该条自动跳过并保留原图。
    - 已移入垃圾桶的素材不处理；文件缺失/无法解码的素材跳过并注明原因。
    - 向量重建以任务队列方式入队，由 worker 进程消费。
    - auto 模式黑边检测针对「深色/黑色背景」截图；浅色背景的截图请用
      固定比例模式。
"""

import asyncio
import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.inspiration import Inspiration, NOT_DELETED
from app.services.file_service import generate_thumbnail
from app.services.image_cropping import (
    MIN_MANUAL_CROP_HEIGHT_PX,
    crop_image_to_temp,
    crop_region_to_temp,
    detect_content_bounds,
    detect_photo_band,
    detect_screenshot_features,
    probe_size as _probe_size,
    screenshot_confidence,
)
# 内部算法接缝重新导出：内容边界/状态栏修正的纯函数单测仍从本模块导入，
# 实现已下沉到 image_cropping（纯算法 module，无需 DB/app 即可直测）。
from app.services.image_cropping import (  # noqa: F401
    _residual_top_estimate,
    _status_bar_correction,
)
from app.services.inspiration_query import load_inspiration_full
from app.services.task_runners.vector_backfill import enqueue_vector_backfills
from app.utils.file_hash import file_sha256
from app.utils.image_hash import perceptual_hash
from app.utils.image_utils import extract_dominant_colors
from app.utils.time import utcnow

logger = logging.getLogger(__name__)

# 竖屏截图判定：高/宽 ≥ 1.75（9:16≈1.78、19.5:9≈2.17）
MIN_RATIO = 1.75
# content 模式的竖屏下限放宽到 1.3：已被裁剪过的截图比例可能掉到 1.3~1.75
# （原 2.17 裁 40% 后 ≈1.3），顶部状态栏残留仍需二次裁剪；内容边界检测
# 自带「内容区占比」与「残留簇 + 后随内容更高」校验兜底，不会误裁普通照片。
CONTENT_MIN_RATIO = 1.3
# 默认裁剪比例（相对图片高度）
DEFAULT_CROP_TOP = 0.03  # 顶部 3%（状态栏区域）
DEFAULT_CROP_BOTTOM = 0.05  # 底部 5%（底部导航栏/手势条区域）

# 内容重复时的裁剪结果预览目录（每次 apply 前清空，仅存活于对比决策期间）
DUP_PREVIEW_DIR_NAME = "_crop_dups"

# 预览目录的清理/创建在进程内串行化：多个 apply_crops 并发执行时，
# 避免互相删除对方刚创建的对比预览批次（跨进程部署时仍有竞态，
# 但本服务为单进程 uvicorn，进程内锁即可覆盖实际并发场景）。
_dups_dir_lock = asyncio.Lock()


def _resolve_storage_path(rel_path: str | None) -> Path | None:
    """把库中相对路径解析为存储根内的绝对路径；为空或越出存储根时返回 None。

    防御性校验：素材记录若被篡改（如 ``../`` 越界路径），拒绝访问存储根之外的文件。

    参数:
        rel_path: 库中存储的相对路径（如 images/2026-08/xxx.jpg）

    返回:
        存储根内的绝对路径；路径为空或解析后越出存储根时返回 None
    """
    if not rel_path:
        return None
    full = (settings.storage_root / rel_path).resolve()
    if not full.is_relative_to(settings.storage_root.resolve()):
        logger.warning(f"素材文件路径越出存储根，已拒绝访问: {rel_path}")
        return None
    return full


async def scan_candidates(
    db: AsyncSession,
    mode: str = "auto",
    crop_top: float = DEFAULT_CROP_TOP,
    crop_bottom: float = DEFAULT_CROP_BOTTOM,
    limit: int = 200,
) -> dict:
    """扫描手动上传素材中的手机全屏截图候选（只读，不执行任何裁剪）。

    参数:
        db: 数据库会话
        mode: auto（黑边自动检测，逐张计算裁剪比例）/ ratio（统一按比例裁剪）/
            content（内容边界检测：灰带/状态栏/播放器条包夹的内容区边界。
            自动化口径——只列手机截图候选：须检出系统 UI 特征（状态栏/
            导航栏）或状态栏残留信号；无 UI 证据的普通竖屏照片静默排除，
            检测失败（无内容区边界）的同样排除）
        crop_top: 顶部裁剪比例（仅 ratio 模式生效）
        crop_bottom: 底部裁剪比例（仅 ratio 模式生效）
        limit: 单次最多返回的候选数（0 表示不限制）

    返回:
        {
            "total": 候选总数（limit 截断前）,
            "items": [{
                "id": str, "file_path": str, "width": int, "height": int,
                "ratio": float, "crop_top": float, "crop_bottom": float,
                "auto_ok": bool, "note": str | None,  # 自动检测失败原因
                "confidence": "high" | "medium" | "low",  # 截图特征置信度
                "boundary_kind": str | None,  # content 模式：gray_band/status_bar/plain
                "created_at": str | None,  # 上传时间（ISO）
            }, ...],
        }
    """
    if mode not in ("auto", "ratio", "content"):
        raise ValueError(f"不支持的裁剪模式: {mode}（允许 auto / ratio / content）")
    if mode == "ratio" and crop_top + crop_bottom >= 1:
        raise ValueError(f"裁剪比例合计必须 < 1：顶部 {crop_top} + 底部 {crop_bottom}")
    # content 模式放宽竖屏下限（被裁剪过的截图比例可低至 1.3）
    min_ratio = MIN_RATIO if mode != "content" else CONTENT_MIN_RATIO

    result = await db.execute(
        select(Inspiration).where(
            Inspiration.source_type == "manual_upload",
            NOT_DELETED,
            Inspiration.file_path.isnot(None),
            Inspiration.media_type == "image",
        )
    )
    candidates: list[dict] = []
    total = 0
    for insp in result.scalars():
        full = _resolve_storage_path(insp.file_path)
        if full is None or not full.exists():
            continue
        try:
            # PIL 文件头读取是阻塞 I/O，放线程池执行（与下方 detect_* 一致）
            width, height = await asyncio.to_thread(_probe_size, full)
        except Exception:
            continue  # 无法解码的图片不做候选
        if height / width < min_ratio:
            continue

        # 截图特征检测（状态栏/底部栏 → 置信度分级）：content 模式用它做
        # 「只列手机截图」的自动化过滤，其余模式仅用于候选置信度展示
        try:
            features = await asyncio.to_thread(detect_screenshot_features, full)
            confidence = screenshot_confidence(features)
        except Exception:
            confidence = "low"

        # content 模式：内容边界检测（灰带/状态栏/播放器条包夹的内容区边界）
        bounds_result = None
        if mode == "content":
            try:
                bounds_result = await asyncio.to_thread(detect_content_bounds, full)
            except Exception:
                # 检测失败（未检出内容区边界/布局不规则）：无法验证截图结构，
                # 自动化口径下静默排除（此前会带默认比例混入候选，属误列）
                continue
            if (
                bounds_result["already_cropped"]
                and bounds_result["residual_top_frac"] <= 0
            ):
                # 已裁剪干净且无任何残留建议 → 跳过不列入候选（避免扫描列表
                # 被大量已处理素材淹没）；检出「疑似状态栏残留」的仍要列入
                # ——这是待人工确认的有效裁剪候选，与 apply_crops 的
                # 「残留建议优先于 already_cropped 跳过」语义保持一致
                continue
            if confidence == "low" and bounds_result["residual_top_frac"] <= 0:
                # ── 自动化口径：内容边界检测只服务手机截图（抖音全屏截图）──
                # 无任何系统 UI 特征且无状态栏残留信号的竖屏图大概率是普通
                # 照片（顶部纯色天空/暗部被误判为边界）——静默排除，不再以
                # 「自动检测失败/疑似非截图请人工确认」占据扫描列表
                continue

        total += 1
        if limit > 0 and len(candidates) >= limit:
            continue
        item: dict = {
            "id": insp.id,
            "file_path": str(insp.file_path),
            "width": width,
            "height": height,
            "ratio": round(height / width, 3),
            "crop_top": crop_top,
            "crop_bottom": crop_bottom,
            "auto_ok": True,
            "note": None,
            "confidence": confidence,
            "boundary_kind": None,
            "created_at": insp.created_at.isoformat(sep=" ") if insp.created_at else None,
        }
        if mode == "auto":
            try:
                top_px, bottom_px = await asyncio.to_thread(detect_photo_band, full)
                item["crop_top"] = round(top_px / height, 6)
                item["crop_bottom"] = round((height - 1 - bottom_px) / height, 6)
            except ValueError as e:
                item["auto_ok"] = False
                item["note"] = f"自动检测失败：{e}"
        elif mode == "content":
            # 使用上方缓存的检测结果
            item["crop_top"] = bounds_result["top_frac"]
            item["crop_bottom"] = bounds_result["bottom_frac"]
            item["boundary_kind"] = bounds_result["kind"]
            if bounds_result["residual_top_frac"] > 0 and bounds_result["top_frac"] == 0:
                # 疑似顶部状态栏残留（透明图标叠加照片——抖音全屏浏览态的
                # 典型特征）：不自动判定可裁剪（防误裁普通照片），标注建议
                # 比例供人工目检后勾选；勾选后 apply 按此建议裁剪
                item["auto_ok"] = False
                item["crop_top"] = bounds_result["residual_top_frac"]
                item["note"] = (
                    f"疑似顶部状态栏残留（建议裁剪 {bounds_result['residual_top_frac']:.1%}），"
                    "确认后勾选裁剪"
                )
        candidates.append(item)

    # 按上传时间倒序（最新批次在前，便于定位特定时间段导入的图）
    candidates.sort(key=lambda c: c.get("created_at") or "", reverse=True)
    return {"total": total, "items": candidates}


def _skip_entry(insp: Inspiration, reason: str) -> dict:
    """构造跳过明细条目，附带素材文件信息供前端缩略图展示与素材库定位跳转。

    参数:
        insp: 素材记录（文件信息取自该记录）
        reason: 跳过原因

    返回:
        {"id": str, "reason": str, "file_path": str | None,
         "thumbnail_path": str | None, "created_at": str | None}
    """
    return {
        "id": insp.id,
        "reason": reason,
        "file_path": insp.file_path,
        "thumbnail_path": insp.thumbnail_path,
        "created_at": insp.created_at.isoformat(sep=" ") if insp.created_at else None,
    }


async def apply_crops(
    db: AsyncSession,
    ids: list[str],
    mode: str = "auto",
    crop_top: float = DEFAULT_CROP_TOP,
    crop_bottom: float = DEFAULT_CROP_BOTTOM,
) -> dict:
    """按用户确认的素材 ID 列表执行裁剪（备份原图 → 裁剪替换 → 重建缩略图/哈希/主色调）。

    参数:
        db: 数据库会话
        ids: 用户勾选确认要裁剪的素材 ID 列表
        mode: auto（黑边自动检测）/ ratio（统一按比例裁剪）/ content（内容边界检测）
        crop_top: 顶部裁剪比例（仅 ratio 模式生效）
        crop_bottom: 底部裁剪比例（仅 ratio 模式生效）

    返回:
        {
            "processed": 成功裁剪数,
            "skipped": [{
                "id": str, "reason": str,
                "file_path": str | None, "thumbnail_path": str | None,
                "created_at": str | None,  # 记录不存在时仅 id + reason
            }, ...],
            "duplicates": [{
                "id": str,  # 本次要裁剪的素材
                "dup_id": str,  # 库中内容重复的素材
                "dup_file_path": str | None, "dup_thumbnail_path": str | None,
                "dup_created_at": str | None,
                "preview_path": str | None,  # 裁剪结果预览（临时文件，供左右对比）
                "reason": str,
            }, ...],
            "backup_dir": str | None,
            "vector_task_id": int | None,
        }
    """
    if mode not in ("auto", "ratio", "content"):
        raise ValueError(f"不支持的裁剪模式: {mode}（允许 auto / ratio / content）")
    if mode == "ratio" and crop_top + crop_bottom >= 1:
        raise ValueError(f"裁剪比例合计必须 < 1：顶部 {crop_top} + 底部 {crop_bottom}")
    # content 模式放宽竖屏下限（被裁剪过的截图比例可低至 1.3）
    min_ratio = MIN_RATIO if mode != "content" else CONTENT_MIN_RATIO
    # 去重：同一素材重复勾选时只处理一次
    ids = list(dict.fromkeys(ids))

    result = await db.execute(
        select(Inspiration).where(Inspiration.id.in_(ids))
    )
    insp_map = {insp.id: insp for insp in result.scalars()}

    # 逐张确定裁剪比例并执行
    plans: list[tuple[Inspiration, Path, float, float]] = []
    skipped: list[dict] = []
    for insp_id in ids:
        insp = insp_map.get(insp_id)
        if insp is None:
            skipped.append({"id": insp_id, "reason": "记录不存在"})
            continue
        if insp.deleted_at is not None:
            skipped.append(_skip_entry(insp, "已入垃圾桶"))
            continue
        if insp.source_type != "manual_upload":
            # 业务边界：仅手动上传素材参与裁剪，其他来源（采集/插件）不处理
            skipped.append(_skip_entry(insp, "仅支持处理手动上传素材"))
            continue
        if insp.media_type != "image":
            skipped.append(_skip_entry(insp, "非图片素材"))
            continue
        full = _resolve_storage_path(insp.file_path)
        if full is None:
            skipped.append(_skip_entry(insp, "文件路径为空或越出存储根"))
            continue
        if not full.exists():
            skipped.append(_skip_entry(insp, "文件不存在"))
            continue
        try:
            width, height = _probe_size(full)
        except Exception:
            skipped.append(_skip_entry(insp, "文件无法解码"))
            continue
        if height / width < min_ratio:
            skipped.append(_skip_entry(insp, f"非竖屏截图（高/宽 < {min_ratio}）"))
            continue
        if mode == "auto":
            try:
                top_px, bottom_px = await asyncio.to_thread(detect_photo_band, full)
                t_frac = round(top_px / height, 6)
                b_frac = round((height - 1 - bottom_px) / height, 6)
            except ValueError as e:
                skipped.append(_skip_entry(insp, f"自动检测失败：{e}"))
                continue
        elif mode == "content":
            try:
                bounds = await asyncio.to_thread(detect_content_bounds, full)
                t_frac = bounds["top_frac"]
                b_frac = bounds["bottom_frac"]
                if bounds["residual_top_frac"] > 0 and t_frac == 0:
                    # 用户已勾选 = 确认疑似残留：按建议比例裁剪顶部
                    t_frac = bounds["residual_top_frac"]
                if bounds["already_cropped"] and t_frac == 0:
                    skipped.append(_skip_entry(insp, "已裁剪过或内容占满全图，无需裁剪"))
                    continue
            except ValueError as e:
                skipped.append(_skip_entry(insp, f"内容边界检测失败：{e}"))
                continue
        else:
            t_frac, b_frac = crop_top, crop_bottom
        plans.append((insp, full, t_frac, b_frac))

    if not plans:
        return {
            "processed": 0,
            "skipped": skipped,
            "duplicates": [],
            "backup_dir": None,
            "vector_task_id": None,
        }

    # 重复对比预览按「批次子目录」存放：用户逐组决策时，前端会仅携带单张
    # 素材重新调用 apply（如「保留裁剪结果」），若在此处清空整个目录会把
    # 其他组的预览一并删掉。因此只清理除「最近一个批次」外的残留批次，
    # 最近批次（含本组其他待决策预览）必须保留到本组决策结束。
    # 清理与批次创建在进程内加锁串行化，避免并发 apply 互相删除对方预览。
    async with _dups_dir_lock:
        dups_dir = settings.storage_root / DUP_PREVIEW_DIR_NAME
        if dups_dir.exists():
            old_batches = sorted(
                (d for d in dups_dir.iterdir() if d.is_dir()),
                key=lambda d: d.stat().st_mtime,
                reverse=True,
            )
            for batch_dir in old_batches[1:]:
                shutil.rmtree(batch_dir, ignore_errors=True)
        # 本批次标识：重新 apply 时创建新批次，避免与旧预览混放
        dup_batch = dups_dir / datetime.now().strftime("%Y%m%d_%H%M%S_%f")

    # 逐张执行裁剪（PIL 操作为阻塞 I/O，放线程池）
    backup_dir = (
        settings.storage_root / "_crop_backup" / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    )
    successes: list[tuple[Inspiration, str | None, str | None, str | None]] = []
    duplicates: list[dict] = []
    # 本批次已成功裁剪素材的新内容哈希 → 素材：同批次后续素材重复命中时，
    # 数据库尚未提交（哈希还没写回），必须在本批次内互查才能检出重复
    seen_hashes: dict[str, Inspiration] = {}
    for insp, full, t_frac, b_frac in plans:
        tmp: Path | None = None
        backup_path: Path | None = None
        replaced = False
        new_thumb: str | None = None
        try:
            tmp = await asyncio.to_thread(crop_image_to_temp, full, t_frac, b_frac)

            # 新内容哈希 + 去重检查：命中重复时保留裁剪结果预览，交用户对比决策，
            # 不再自动丢弃（原图与库中素材均保留，由用户决定删除哪一张）
            new_hash = await asyncio.to_thread(file_sha256, tmp)
            dup_id = None
            dup_insp: Inspiration | None = None
            if new_hash:
                dup_id = (
                    await db.execute(
                        select(Inspiration.id).where(
                            Inspiration.content_hash == new_hash,
                            Inspiration.id != insp.id,
                            NOT_DELETED,
                        )
                    )
                ).scalars().first()
                # 同批次已成功裁剪（尚未写库）的素材同样参与去重
                if dup_id is None and new_hash in seen_hashes:
                    dup_insp = seen_hashes[new_hash]
                    dup_id = dup_insp.id
            if dup_id:
                if dup_insp is None:
                    dup_insp = (
                        await db.execute(
                            select(Inspiration).where(Inspiration.id == dup_id)
                        )
                    ).scalar_one_or_none()
                # 裁剪结果预览移入本批次目录（与素材同卷，直接 rename）
                dup_batch.mkdir(parents=True, exist_ok=True)
                preview = dup_batch / f"{insp.id}_{dup_id}{tmp.suffix}"
                os.replace(tmp, preview)
                tmp = None
                duplicates.append(
                    {
                        "id": insp.id,
                        "dup_id": dup_id,
                        "dup_file_path": dup_insp.file_path if dup_insp else None,
                        "dup_thumbnail_path": dup_insp.thumbnail_path if dup_insp else None,
                        "dup_created_at": (
                            dup_insp.created_at.isoformat(sep=" ")
                            if dup_insp and dup_insp.created_at
                            else None
                        ),
                        "preview_path": str(preview.relative_to(settings.storage_root)),
                        "reason": f"裁剪结果与素材 {dup_id} 内容重复",
                    }
                )
                continue

            # 主色调在临时文件上提前重算（仅原值非空时刷新）：
            # 放到替换原图之前执行，替换后便不再有失败点需要回滚
            colors = insp.dominant_colors
            if colors is not None:
                new_colors = await asyncio.to_thread(extract_dominant_colors, tmp)
                colors = json.dumps(new_colors) if new_colors else insp.dominant_colors

            # 备份原图 → 原子替换。备份名带毫秒时间戳，避免同秒重复裁剪同一素材时覆盖备份
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_name = f"{insp.id}_{datetime.now().strftime('%H%M%S%f')}{full.suffix}"
            backup_path = backup_dir / backup_name
            shutil.copy2(full, backup_path)
            os.replace(tmp, full)
            tmp = None
            replaced = True

            # 重新生成缩略图：失败（返回 None）时删除旧缩略图并置空，避免缩略图
            # 仍指向修改前的内容造成错配（前端将回退展示原图）。
            # generate_thumbnail 内部捕获异常返回 None；若意外抛出（如实现变更），
            # 由外层 except 触发「从备份恢复原图」的回滚，保证磁盘与数据库一致。
            thumb_path = insp.thumbnail_path
            new_thumb = await generate_thumbnail(full)
            if new_thumb:
                if thumb_path and (settings.storage_root / thumb_path) != (settings.storage_root / new_thumb):
                    (settings.storage_root / thumb_path).unlink(missing_ok=True)
                thumb_path = new_thumb
            else:
                if thumb_path:
                    (settings.storage_root / thumb_path).unlink(missing_ok=True)
                thumb_path = None
                logger.warning(f"裁剪后缩略图生成失败，已置空旧缩略图: {insp.id}")

            successes.append((insp, new_hash or None, thumb_path, colors))
            if new_hash:
                seen_hashes[new_hash] = insp
        except Exception as e:
            # 原图已被替换但后续失败：从备份恢复原文件，保持磁盘与数据库一致；
            # 若新缩略图已写入，一并删除（避免与恢复后的原图错配）
            if replaced:
                if new_thumb:
                    (settings.storage_root / new_thumb).unlink(missing_ok=True)
                if backup_path is not None and backup_path.exists():
                    try:
                        shutil.copy2(backup_path, full)
                        logger.error(f"裁剪后处理异常，已从备份恢复原图: {insp.id}, err={e}")
                    except Exception as restore_err:
                        logger.error(
                            f"裁剪后处理异常且原图恢复失败（请手工从备份恢复）: {insp.id}, "
                            f"err={e}, restore_err={restore_err}"
                        )
            if tmp is not None and tmp.exists():
                tmp.unlink(missing_ok=True)
            skipped.append(_skip_entry(insp, f"处理失败: {e}"))

    # 写回数据库（标签/收藏/来源等字段不动）
    for insp, new_hash, thumb_path, colors in successes:
        if new_hash:
            insp.content_hash = new_hash
        insp.thumbnail_path = thumb_path
        insp.dominant_colors = colors
        insp.phash = None  # 文件内容已替换，感知哈希缓存作废（近似重复扫描时懒重算）
        insp.updated_at = utcnow()
    await db.commit()

    # 向量回填（攒批）：图像向量按新图重建，文本向量沿用现有标签。
    # 素材 ID 进入待回填队列，累计达到阈值（100）后统一创建批量任务；
    # 未达阈值时 vector_task_id 为 None（素材保留在待回填表，不会丢失）。
    # 登记失败不影响主流程（裁剪已成功提交，向量可由后续任务/手动重建兜底）。
    # 注意：enqueue 不再内部提交，裁剪变更已在上方 commit，这里需显式提交登记行
    vector_task_id: int | None = None
    if successes:
        try:
            task = await enqueue_vector_backfills(db, [s[0].id for s in successes])
            await db.commit()
            vector_task_id = task.id if task else None
        except Exception:
            logger.exception("裁剪后登记向量回填失败，不影响裁剪主流程")

    logger.info(
        f"手机图裁剪完成: 确认 {len(ids)}，成功 {len(successes)}，"
        f"跳过 {len(skipped)}，内容重复待用户决策 {len(duplicates)}，备份 {backup_dir}"
    )

    # 记录审计：批量裁剪替换素材图片属破坏性操作（原图仅备份可手动恢复），留痕便于追溯
    if successes:
        from app.services.audit_service import record_audit_log

        await record_audit_log(
            action="crop",
            count=len(successes),
            detail=f"手机图裁剪成功 {len(successes)} 个素材（备份于 {backup_dir}）",
        )

    return {
        "processed": len(successes),
        "skipped": skipped,
        "duplicates": duplicates,
        "backup_dir": str(backup_dir) if successes else None,
        "vector_task_id": vector_task_id,
    }


# ── 手动裁剪（素材详情页单张裁剪）──────────────────────────────────────────


async def crop_inspiration_region(
    db: AsyncSession, inspiration_id: str, y1_ratio: float, y2_ratio: float
) -> Inspiration:
    """手动裁剪单个图片素材：保留 [y1, y2) 区域替换原图，并同步全部派生数据。

    素材详情页「裁剪」入口调用：用户拖动上下分割线确认保留中间区域，
    后端按比例就地裁剪原图，触发与批量手机图裁剪一致的派生数据更新。

    流程:
        1. 校验素材存在 / 未删除 / 图片类型 / 文件可读；
        2. 校验比例合法（0 ≤ y1 < y2 ≤ 1）且保留高度 ≥ MIN_MANUAL_CROP_HEIGHT_PX；
        3. 按 EXIF 校正后的像素坐标裁剪到临时文件（阻塞 I/O 放线程池）；
        4. 备份原图 → 原子替换；替换后任一步失败都从备份恢复原文件；
        5. 重建缩略图、重算内容哈希（SHA-256）与感知哈希（phash）、刷新主色调；
        6. 登记向量回填（攒批方式，同 apply_crops）并记录审计。

    备份策略与 apply_crops（批量手机图裁剪，备份长期保留供手动恢复）不同：
    手动裁剪前用户已通过裁剪预览确认，成功后即删除备份，失败时才需要从
    备份恢复原图，避免手动裁剪备份无限累积占用磁盘。

    参数:
        db: 数据库会话
        inspiration_id: 素材 ID
        y1_ratio: 保留区域上边界（相对 EXIF 校正后图片高度的比例，0~1）
        y2_ratio: 保留区域下边界（相对 EXIF 校正后图片高度的比例，0~1）

    返回:
        裁剪完成后的素材记录（已提交，file_path 不变、派生字段已更新）

    异常:
        HTTPException: 素材不存在/在垃圾桶/非图片/文件缺失/比例非法/处理失败
    """
    # 用共享的详情加载方式预加载 tags/tag、bloggers/blogger、models/model 与
    # analysis_logs，确保裁剪成功返回时 _to_out 的响应转换不会触发异步懒加载
    # （async SQLAlchemy 的懒加载在同步转换函数中会抛 MissingGreenlet → 500）
    insp = await load_inspiration_full(db, inspiration_id)
    if insp is None:
        raise HTTPException(status_code=404, detail="灵感素材未找到")
    if insp.deleted_at is not None:
        raise HTTPException(status_code=400, detail="素材在垃圾桶中，无法裁剪")
    if insp.media_type != "image":
        raise HTTPException(status_code=400, detail="仅支持裁剪图片素材")
    if not (0 <= y1_ratio < y2_ratio <= 1):
        raise HTTPException(
            status_code=400,
            detail=f"裁剪比例非法，需满足 0 ≤ y1 < y2 ≤ 1（y1={y1_ratio}, y2={y2_ratio}）",
        )

    full = _resolve_storage_path(insp.file_path)
    if full is None or not full.exists():
        raise HTTPException(status_code=400, detail="素材文件缺失或路径越界，无法裁剪")

    # 保留高度校验按「EXIF 校正后的显示高度」计算，避免旋转图比例误判
    try:
        _, height = await asyncio.to_thread(_probe_size, full)
    except Exception:
        raise HTTPException(status_code=400, detail="素材文件无法解码，无法裁剪") from None
    if (y2_ratio - y1_ratio) * height < MIN_MANUAL_CROP_HEIGHT_PX:
        raise HTTPException(
            status_code=400,
            detail=f"保留区域高度过小，至少需要 {MIN_MANUAL_CROP_HEIGHT_PX}px",
        )

    tmp: Path | None = None
    backup_path: Path | None = None
    replaced = False
    new_thumb: str | None = None
    try:
        # 裁剪到临时文件（与素材同卷，保证 os.replace 原子）
        tmp = await asyncio.to_thread(crop_region_to_temp, full, y1_ratio, y2_ratio)

        # 主色调在临时文件上提前重算（仅原值非空时刷新，与 apply_crops 一致）：
        # 放在替换原图之前执行，替换后便不再有失败点需要回滚
        colors = insp.dominant_colors
        if colors is not None:
            new_colors = await asyncio.to_thread(extract_dominant_colors, tmp)
            colors = json.dumps(new_colors) if new_colors else insp.dominant_colors

        # 备份原图 → 原子替换。备份名带毫秒时间戳，避免同秒重复裁剪覆盖备份
        backup_dir = (
            settings.storage_root / "_crop_backup" / datetime.now().strftime("%Y-%m-%d_%H%M%S")
        )
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"{insp.id}_{datetime.now().strftime('%H%M%S%f')}{full.suffix}"
        shutil.copy2(full, backup_path)
        os.replace(tmp, full)
        tmp = None
        replaced = True

        # 重建缩略图：失败（返回 None）时删除旧缩略图并置空 thumbnail_path，
        # 避免缩略图仍指向修改前的内容造成错配（前端将回退展示原图）
        thumb_path = insp.thumbnail_path
        new_thumb = await generate_thumbnail(full)
        if new_thumb:
            old_full = (settings.storage_root / thumb_path) if thumb_path else None
            if old_full is not None and old_full != (settings.storage_root / new_thumb):
                old_full.unlink(missing_ok=True)
            thumb_path = new_thumb
        else:
            if thumb_path:
                (settings.storage_root / thumb_path).unlink(missing_ok=True)
            thumb_path = None
            logger.warning(f"手动裁剪后缩略图生成失败，已置空旧缩略图: {insp.id}")

        # 派生数据写回：内容哈希（SHA-256 精确去重 + 近似重复扫描基准）、
        # 感知哈希（重算，使缓存与替换后的文件一致，免去懒重算）、主色调、缩略图
        new_hash = await asyncio.to_thread(file_sha256, full)
        new_phash = await asyncio.to_thread(perceptual_hash, full)
        insp.content_hash = new_hash or insp.content_hash
        insp.phash = new_phash
        insp.thumbnail_path = thumb_path
        insp.dominant_colors = colors
        insp.updated_at = utcnow()
        # ⚠ 事务边界：commit 之后即为「裁剪成功」，后续仅做尽力而为的清理，
        # 任何清理失败都不得进入回滚分支（否则会用备份把新图覆盖回旧图，
        # 造成磁盘与数据库不一致）
        await db.commit()

        # 裁剪成功：清理备份（用户已通过预览确认；失败恢复不再需要）
        if backup_path.exists():
            try:
                backup_path.unlink(missing_ok=True)
            except OSError as e:
                logger.warning(f"手动裁剪备份清理失败（忽略）: {backup_path} — {e}")

        # 向量回填（攒批，同 apply_crops）：未达阈值时素材保留在待回填表，
        # 由后续攒批/手动一键回填/worker 启动兜底，登记失败不影响主流程
        try:
            await enqueue_vector_backfills(db, [insp.id])
            await db.commit()
        except Exception:
            logger.exception("手动裁剪后登记向量回填失败，不影响裁剪主流程")

        # 记录审计：手动裁剪替换素材图片属破坏性操作，留痕便于追溯。
        # record_audit_log 内部使用独立会话并吞异常，不影响主流程
        from app.services.audit_service import record_audit_log

        await record_audit_log(
            action="crop",
            count=1,
            detail=(
                f"手动裁剪素材 {insp.id}：保留区域 y1={y1_ratio:.4f}~y2={y2_ratio:.4f}"
                "（备份已随裁剪成功清理）"
            ),
        )

        logger.info(f"手动裁剪完成: id={insp.id}, y1={y1_ratio:.4f}, y2={y2_ratio:.4f}")
        return insp
    except HTTPException:
        raise
    except Exception as e:
        # 原图已被替换但后续失败（commit 之前）：从备份恢复原文件，保持磁盘
        # 与数据库一致；若新缩略图已写入，一并删除（避免与恢复后的原图错配）
        if replaced:
            if new_thumb:
                (settings.storage_root / new_thumb).unlink(missing_ok=True)
            if backup_path is not None and backup_path.exists():
                try:
                    shutil.copy2(backup_path, full)
                    logger.error(f"手动裁剪后处理异常，已从备份恢复原图: {insp.id}, err={e}")
                except Exception as restore_err:
                    logger.error(
                        f"手动裁剪后处理异常且原图恢复失败（请手工从备份恢复）: {insp.id}, "
                        f"err={e}, restore_err={restore_err}"
                    )
        if tmp is not None and tmp.exists():
            tmp.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"裁剪处理失败: {e}") from e
