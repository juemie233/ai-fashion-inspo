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
import time
import uuid
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
    analyze_screenshot_combined,
    crop_image_to_temp,
    crop_region_to_temp,
    detect_photo_band,
    detect_screenshot_features,
    probe_size as _probe_size,
    screenshot_confidence,
    _FULL_SCREENSHOT_RATIO,
)
# 内部算法接缝重新导出：内容边界/状态栏修正的纯函数单测仍从本模块导入，
# 实现已下沉到 image_cropping（纯算法 module，无需 DB/app 即可直测）。
# detect_content_bounds 的函数体调用（apply 路径）已改用
# analyze_screenshot_combined（携带截图特征证据），此处仅为测试兼容保留
from app.services.image_cropping import (  # noqa: F401
    _residual_top_estimate,
    _status_bar_correction,
    detect_content_bounds,
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
    cursor: str | None = None,
    time_budget: float = 60.0,
) -> dict:
    """扫描手动上传素材中的手机全屏截图候选（只读，不执行任何裁剪）。

    参数:
        db: 数据库会话
        mode: auto（黑边自动检测——小红书截图：上下黑边包夹图片主体，双侧
            黑边才默认勾选）/ ratio（统一按比例裁剪）/
            content（内容边界检测——抖音截图：状态栏/播放器条残留，按字形证据
            勾选。
            自动化口径——只列手机截图候选：须检出系统 UI 特征（状态栏/
            导航栏）或状态栏残留信号；无 UI 证据的普通竖屏照片静默排除，
            检测失败（无内容区边界）的同样排除）
        crop_top: 顶部裁剪比例（仅 ratio 模式生效）
        crop_bottom: 底部裁剪比例（仅 ratio 模式生效）
        limit: 单次最多返回的候选数（0 表示不限制）
        cursor: 分页游标（上一批返回的 next_cursor；素材按 id 全序分批扫描，
            传游标从断点继续，避免大批量素材单次请求超时。注意 id 为 UUID，
            顺序稳定但与上传时间无关——只保证分批不重不漏，不代表先后批次
            的新旧关系）
        time_budget: 单次扫描的时间预算（秒）。素材量大（5000+ 竖屏）时
            全量检测远超前端请求超时，预算耗尽即返回已找到的候选并置
            truncated=True，由前端提示用户继续扫描。

    返回:
        {
            "total": 本次扫描列入候选的数量（受 limit 封顶，等于 len(items））,
            "items": [候选列表，同旧结构],
            "scanned": 本次实际扫描的素材数,
            "next_cursor": 截断时下一次扫描的起点（素材 id）；扫完返回 None,
            "truncated": 是否因时间预算/候选上限提前结束（还有未扫描素材）,
        }
    """
    if mode not in ("auto", "ratio", "content"):
        raise ValueError(f"不支持的裁剪模式: {mode}（允许 auto / ratio / content）")
    if mode == "ratio" and crop_top + crop_bottom >= 1:
        raise ValueError(f"裁剪比例合计必须 < 1：顶部 {crop_top} + 底部 {crop_bottom}")
    # content 模式放宽竖屏下限（被裁剪过的截图比例可低至 1.3）
    min_ratio = MIN_RATIO if mode != "content" else CONTENT_MIN_RATIO

    cursor_id: str | None = None
    if cursor is not None:
        try:
            # 素材主键为 UUID 字符串：规范化（小写连字符形式）后做字符串比较，
            # 与库中存储格式严格一致；不能 int() 转换（曾导致续扫恒 400）
            cursor_id = str(uuid.UUID(cursor))
        except ValueError:
            raise ValueError(f"分页游标格式无效：{cursor}（应为上次扫描返回的素材 id）") from None

    query = select(Inspiration).where(
        Inspiration.source_type == "manual_upload",
        NOT_DELETED,
        Inspiration.file_path.isnot(None),
        Inspiration.media_type == "image",
    )
    if cursor_id is not None:
        query = query.where(Inspiration.id < cursor_id)
    query = query.order_by(Inspiration.id.desc())
    result = await db.execute(query)

    candidates: list[dict] = []
    scanned = 0
    truncated = False
    last_insp_id: str | None = None
    deadline = time.monotonic() + max(1.0, time_budget)
    for insp in result.scalars():
        # 时间预算检查（检测是耗时大头，循环级检查粒度足够）
        if time.monotonic() >= deadline:
            truncated = True
            break
        scanned += 1
        last_insp_id = insp.id
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

        # content 模式：单次解码合并「截图特征 + 内容边界」（性能关键——
        # 全量扫描 5000+ 张时每张只解码一次，旧实现要解码三次）
        bounds_result = None
        if mode == "content":
            try:
                features, bounds_result = await asyncio.to_thread(
                    analyze_screenshot_combined, full
                )
                confidence = screenshot_confidence(features)
            except Exception:
                confidence = "low"
                bounds_result = None
            if bounds_result is None:
                # 检测失败（未检出内容区边界/布局不规则）且无字形证据
                # （combined 已在字形存在时返回字形建议而非 None）：静默排除
                continue
            glyph_top_frac = bounds_result.get("glyph_top_frac", 0)
            glyph_strong = bounds_result.get("glyph_strong", False)
            residual_bottom = bounds_result.get("residual_bottom_frac", 0)
            # ── 候选资格（FP/FN 裁决层）──
            # 1) 低置信 + 无任何建议（残留/字形）：大概率普通照片，静默排除
            #    （保留原规则，字形证据可救援）
            if (
                confidence == "low"
                and bounds_result["residual_top_frac"] <= 0
                and glyph_top_frac <= 0
                and residual_bottom <= 0
            ):
                continue
            # 2) 完整截图先验：ratio ≥ 1.8 的竖图极大概率是完整手机截图
            #    （真实库验证：明显状态栏素材全部 ratio≈2.16，误报样本全部
            #    <1.8）。非完整截图从严——需要实底 UI 带、字形建议、或
            #    「残留估算 + 强字形」相互印证（纯色背景照片的残留估算无
            #    字形佐证，在此排除——历史自动勾选 FP 的根因）
            full_screenshot = height / width >= _FULL_SCREENSHOT_RATIO
            if not full_screenshot:
                # 残留估算（residual）无字形佐证时保留候选但绝不默认勾选——
                # 自动勾选误报（用户核心投诉）由勾选规则消灭，列表噪音由
                # 人工勾选池消化
                qualified = (
                    bounds_result["top_frac"] > 0
                    or bounds_result["bottom_frac"] > 0
                    or glyph_top_frac > 0
                    or bounds_result["residual_top_frac"] > 0
                    or residual_bottom > 0
                    or (
                        confidence != "low"
                        and bounds_result["already_cropped"]
                        and features.get("top_bar", False)
                    )
                )
                if not qualified:
                    continue
            # 检出截图特征（top_bar/bottom_bar）的素材即使已裁剪干净、无
            # 残留建议也继续列入候选：它是真实截图，顶部状态栏残留肉眼
            # 可见，交给人工目检勾选（此前把这类素材静默过滤，导致
            # 「大部分素材找不到、无法选中」），item 构造中如实标注
        else:
            # 非 content 模式：截图特征检测（状态栏/底部栏 → 置信度分级）
            try:
                features = await asyncio.to_thread(detect_screenshot_features, full)
                confidence = screenshot_confidence(features)
            except Exception:
                confidence = "low"

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
            "auto_checked": None,
            "confidence": confidence,
            "boundary_kind": None,
            "created_at": insp.created_at.isoformat(sep=" ") if insp.created_at else None,
        }
        if mode == "auto":
            try:
                top_px, bottom_px = await asyncio.to_thread(detect_photo_band, full)
                item["crop_top"] = round(top_px / height, 6)
                item["crop_bottom"] = round((height - 1 - bottom_px) / height, 6)
                # 本模式为小红书截图设计：其形态是「上下黑边包夹图片主体」。
                # 双侧黑边才默认勾选；单侧「黑边」多为抖音截图的播放器条或
                # 照片暗部（应走 content 模式处理），保留候选但不自动勾选，
                # 交人工判断
                item["auto_checked"] = item["crop_top"] > 0 and item["crop_bottom"] > 0
            except ValueError as e:
                item["auto_ok"] = False
                item["auto_checked"] = False
                item["note"] = f"自动检测失败：{e}"
        elif mode == "content":
            # 使用上方缓存的检测结果
            item["crop_top"] = bounds_result["top_frac"]
            item["crop_bottom"] = bounds_result["bottom_frac"]
            item["boundary_kind"] = bounds_result["kind"]
            glyph_top_frac = bounds_result.get("glyph_top_frac", 0)
            residual_bottom = bounds_result.get("residual_bottom_frac", 0)
            suggestion = max(bounds_result["residual_top_frac"], glyph_top_frac)
            if (
                bounds_result["top_frac"] == 0
                and suggestion == 0
                and residual_bottom > 0
            ):
                # 仅底部残留（半透明播放器条/进度条叠加，顶部干净）：建议裁底部
                item["auto_ok"] = False
                item["crop_bottom"] = residual_bottom
                # already_cropped 先验下的均匀暗带建议可信度高，默认勾选
                item["auto_checked"] = True
                item["note"] = (
                    f"疑似底部导航条/进度条残留（建议裁剪 {residual_bottom:.1%}），"
                    "已默认勾选，请预览确认"
                )
            elif bounds_result["top_frac"] == 0 and suggestion > 0:
                # 疑似顶部状态栏残留（透明图标叠加照片——抖音全屏浏览态的
                # 典型特征，或实底状态栏残留）：不自动判定可裁剪（防误裁
                # 普通照片），标注建议比例。
                # 默认勾选收紧（历史 FP 根因）：仅「字形证据 + 左右两角齐备」
                # 的候选默认勾选——状态栏字形是行剖面之外唯一与底图无关的
                # 稳定信号，纯色背景照片/海报大字（无两角字形）不再自动勾选。
                # 残留估算（residual）本身仍要求字形证据才勾选，无字形的
                # 残留建议保持候选但由人工决定。
                item["auto_ok"] = False
                item["crop_top"] = suggestion
                # 默认勾选仅认强字形（时间签名）证据：完整比例先验只影响
                # 候选资格，不构成勾选理由（ratio≈2.16 的拼图/长图无状态栏）
                item["auto_checked"] = glyph_strong = bool(
                    glyph_top_frac > 0 and bounds_result.get("glyph_strong", False)
                )
                item["note"] = (
                    f"疑似顶部状态栏残留（建议裁剪 {suggestion:.1%}），"
                    + ("已默认勾选，请预览确认" if item["auto_checked"] else "请预览确认")
                )
            elif (
                bounds_result["already_cropped"]
                and bounds_result["top_frac"] <= 0
                and bounds_result["residual_top_frac"] <= 0
            ):
                # 检出截图特征（状态栏/导航栏）但未给出可裁建议：可能是已
                # 裁过或界面以内容为主。如实标注，不编造比例，由人工目检决定
                item["auto_ok"] = False
                item["crop_top"] = 0.0
                item["auto_checked"] = False
                item["note"] = "未检出可裁区域（可能已裁剪过），请目检确认"
        candidates.append(item)
        # 候选上限检查必须放在 append 之后：此时当前候选已入选，断点游标
        # （last_insp_id）指向它，续扫从它之后接续，不重不漏。若放在 append
        # 之前，边界候选会被计入 total 却不入选，续扫游标又跳过它 → 永久丢失
        if limit > 0 and len(candidates) >= limit:
            truncated = True
            break

    # 按上传时间倒序（最新批次在前，便于定位特定时间段导入的图）
    candidates.sort(key=lambda c: c.get("created_at") or "", reverse=True)
    return {
        "total": len(candidates),
        "items": candidates,
        "scanned": scanned,
        "next_cursor": str(last_insp_id) if truncated and last_insp_id is not None else None,
        "truncated": truncated,
    }


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
                # 用「特征+边界」合并检测（与扫描口径一致）：携带截图特征
                # 证据，低多样度内容区的真实截图也能检出残留建议，避免
                # 扫描能列、apply 却跳过的不一致
                features, bounds = await asyncio.to_thread(analyze_screenshot_combined, full)
                if bounds is None:
                    raise ValueError("未检测到内容区边界")
                t_frac = bounds["top_frac"]
                b_frac = bounds["bottom_frac"]
                if b_frac == 0 and t_frac == 0 and bounds.get("residual_bottom_frac", 0) > 0:
                    # 用户已勾选 = 确认底部残留：按建议比例裁剪底部
                    b_frac = bounds["residual_bottom_frac"]
                elif t_frac == 0 and (
                    bounds["residual_top_frac"] > 0
                    or bounds.get("glyph_top_frac", 0) > 0
                ):
                    # 用户已勾选 = 确认疑似残留：按建议比例裁剪顶部。
                    # 与扫描同口径：实底条带 residual 与字形建议（透明叠加，
                    # combined 的 ValueError 路径已返回字形建议 dict）取大者
                    t_frac = max(bounds["residual_top_frac"], bounds.get("glyph_top_frac", 0))
                if bounds["already_cropped"] and t_frac == 0 and not (
                    features.get("top_bar") or features.get("bottom_bar")
                ):
                    # 无截图特征且检测无建议：普通照片/已裁干净 → 跳过
                    skipped.append(_skip_entry(insp, "已裁剪过或内容占满全图，无需裁剪"))
                    continue
                if t_frac <= 0 and b_frac <= 0:
                    # 有截图特征（用户确认勾选）但本函数仍无建议（如顶部
                    # 残留比例低于门槛）：不编造比例白裁（裁剪 0/0 等于
                    # 原样复制，属假成功），明确跳过由人工另行处理
                    skipped.append(_skip_entry(insp, "未检出可裁区域（无有效裁剪比例）"))
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
