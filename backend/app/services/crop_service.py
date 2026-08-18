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
import uuid
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.inspiration import Inspiration, NOT_DELETED
from app.services.file_service import generate_thumbnail
from app.services.task_runners.vector_backfill import enqueue_vector_backfills
from app.utils.file_hash import file_sha256
from app.utils.image_utils import extract_dominant_colors
from app.utils.time import utcnow

logger = logging.getLogger(__name__)

# 竖屏截图判定：高/宽 ≥ 1.75（9:16≈1.78、19.5:9≈2.17）
MIN_RATIO = 1.75
# 默认裁剪比例（相对图片高度）
DEFAULT_CROP_TOP = 0.03  # 顶部 3%（状态栏区域）
DEFAULT_CROP_BOTTOM = 0.05  # 底部 5%（底部导航栏/手势条区域）

# ── 黑边自动检测参数 ──
BRIGHT_PIXEL_THRESHOLD = 25  # 灰度值 > 25 视为「亮像素」
CONTENT_ROW_FRACTION = 0.005  # 一行中亮像素占比 > 0.5% 视为内容行
MIN_MAIN_BAND_HEIGHT_FRACTION = 0.25  # 主体条带高度至少占全图 25%
MAX_OTHER_BANDS_FRACTION = 0.5  # 其他条带总高度不超过主体的 50%

# 内容重复时的裁剪结果预览目录（每次 apply 前清空，仅存活于对比决策期间）
DUP_PREVIEW_DIR_NAME = "_crop_dups"

# 预览目录的清理/创建在进程内串行化：多个 apply_crops 并发执行时，
# 避免互相删除对方刚创建的对比预览批次（跨进程部署时仍有竞态，
# 但本服务为单进程 uvicorn，进程内锁即可覆盖实际并发场景）。
_dups_dir_lock = asyncio.Lock()

# EXIF Orientation 取值：5/6/7/8 表示 90°/270° 旋转，宽高互换
_EXIF_TRANSPOSE_90 = frozenset((5, 6, 7, 8))


def _probe_size(path: Path) -> tuple[int, int]:
    """读取图片显示尺寸（按 EXIF 方向校正宽高，仅读文件头，不完整解码）。

    裁剪阶段用 ``ImageOps.exif_transpose`` 校正方向，扫描/检测阶段必须与之一致，
    否则带旋转 EXIF 的图片会出现比例误判或裁剪位置偏移。

    参数:
        path: 图片绝对路径

    返回:
        (width, height) 按 EXIF 方向校正后的显示尺寸
    """
    with Image.open(path) as im:
        width, height = im.size
        orientation = im.getexif().get(0x0112, 1)
    if orientation in _EXIF_TRANSPOSE_90:
        width, height = height, width
    return width, height


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


def detect_photo_band(path: Path) -> tuple[int, int]:
    """检测图片中最大的内容条带（照片主体）的上下边界。

    思路：把图像转灰度后逐行统计「亮像素占比」，占比极低的连续行视为
    黑边/留白；从所有内容条带中选出最高的一条作为照片主体，返回
    (顶部行, 底部行)（含端点，0 基像素坐标）。

    参数:
        path: 图片绝对路径

    返回:
        (top, bottom) 主体条带的上下边界

    异常:
        ValueError: 布局不规则（无内容/主体过矮/多主体拼贴等），需人工处理
    """
    import numpy as np

    with Image.open(path) as im:
        # 与裁剪阶段一致：先做 EXIF 方向校正，避免旋转图片的黑边检测基准错位
        arr = np.asarray(ImageOps.exif_transpose(im).convert("L"))
    height = arr.shape[0]
    row_frac = (arr > BRIGHT_PIXEL_THRESHOLD).mean(axis=1)

    bands: list[tuple[int, int]] = []
    start: int | None = None
    for y, frac in enumerate(row_frac):
        if frac > CONTENT_ROW_FRACTION:
            if start is None:
                start = y
        elif start is not None:
            bands.append((start, y - 1))
            start = None
    if start is not None:
        bands.append((start, height - 1))

    if not bands:
        raise ValueError("未检测到内容条带")

    main = max(bands, key=lambda b: b[1] - b[0] + 1)
    top, bottom = main
    main_height = bottom - top + 1

    if main_height < height * MIN_MAIN_BAND_HEIGHT_FRACTION:
        raise ValueError(f"主体条带过矮（{main_height}px），可能不是居中照片")
    if top == 0 and bottom == height - 1:
        raise ValueError("主体占满全图，无黑边可裁")
    other_height = sum(e - s + 1 for s, e in bands if (s, e) != main)
    if other_height > main_height * MAX_OTHER_BANDS_FRACTION:
        raise ValueError(f"其他内容条带过多（{other_height}px），布局复杂")
    return top, bottom


# ── 手机截图特征检测参数 ──
_ANALYZE_W = 64  # 分析图宽度（统一缩放，降低计算量）
# 行内颜色多样度（量化后唯一颜色占比）判据（实测校准）：
# - 状态栏：背景纯色 + 时间/信号图标 → 多样度 0.02~0.3，且下方最终过渡到内容区
# - 底部手势条/导航栏：最后几行几乎纯色（多样度 < 0.1）
# - 真实照片内容区：多样度通常 > 0.25
_ROW_UNIFORM = 0.1  # 纯色条带行：多样度低于此值
_ROW_STATUS_BAR = 0.3  # 状态栏行（含图标）多样度上限
_ROW_CONTENT = 0.25  # 内容区行多样度下限
_TOP_SCAN_FRACTION = 0.15  # 顶部扫描范围（状态栏出现在前 15% 高度内）
_BOTTOM_SCAN_FRACTION = 0.12  # 底部扫描范围（手势条在最后 12% 高度内）


def detect_screenshot_features(path: Path) -> dict:
    """检测手机系统截图特征：顶部状态栏条带 + 底部导航栏/手势条。

    方法：把图片统一缩放到 64 宽后逐行统计「行内颜色多样度」（量化后唯一
    颜色占比）。
    - 状态栏特征：顶部区域存在「多样度 0.02~0.3 的行簇」（纯色背景上的
      时间/信号图标），且其下方在合理范围内过渡到多样度更高的内容区。
    - 底部特征：底部最后几行为近纯色（手势条/导航栏），其上方为内容区。
    两个特征都用于区分「手机系统截图」与「普通竖图/纯色模板」：
    模板图/渐变图整图多样度低、不存在「条带 → 内容区」的结构突变。

    参数:
        path: 图片绝对路径

    返回:
        {"top_bar": bool, "bottom_bar": bool}
    """
    with Image.open(path) as im:
        # 与裁剪阶段一致：先做 EXIF 方向校正，再缩放分析
        img = ImageOps.exif_transpose(im).convert("RGB")
        small = img.resize(
            (_ANALYZE_W, max(16, img.height * _ANALYZE_W // img.width)),
            Image.Resampling.LANCZOS,
        )
    n = small.height
    px = small.load()

    def diversity(y: int) -> float:
        colors = set()
        for x in range(_ANALYZE_W):
            r, g, b = px[x, y]
            colors.add((r // 16, g // 16, b // 16))
        return len(colors) / _ANALYZE_W

    rows = [diversity(y) for y in range(n)]

    # 顶部状态栏：前 15% 高度内存在「图标行簇」（0.02~0.3），
    # 且其后在 40% 高度内出现内容区（>0.25）；同时该行簇之前允许纯色行（状态栏背景）
    top_bar = False
    top_limit = int(n * _TOP_SCAN_FRACTION)
    for i in range(min(2, top_limit), top_limit):
        if _ROW_UNIFORM < rows[i] <= _ROW_STATUS_BAR:
            # 找到图标行，检查其后是否过渡到内容区
            if any(rows[j] > _ROW_CONTENT for j in range(i, min(n, i + int(n * 0.4)))):
                top_bar = True
                break

    # 底部手势条/导航栏：最后 12% 高度内，存在连续 ≥2 行近纯色，
    # 且其上方（60% 高度内）存在内容区
    bottom_bar = False
    bot_start = int(n * (1 - _BOTTOM_SCAN_FRACTION))
    for i in range(bot_start, n - 1):
        if rows[i] < _ROW_UNIFORM and rows[i + 1] < _ROW_UNIFORM:
            if any(rows[j] > _ROW_CONTENT for j in range(max(0, i - int(n * 0.6)), i)):
                bottom_bar = True
                break

    return {"top_bar": top_bar, "bottom_bar": bottom_bar}


def screenshot_confidence(features: dict) -> str:
    """根据截图特征输出置信度：high（状态栏+底部栏齐全）/ medium（单侧）/ low（无特征）。"""
    top_bar, bottom_bar = features.get("top_bar", False), features.get("bottom_bar", False)
    if top_bar and bottom_bar:
        return "high"
    if top_bar or bottom_bar:
        return "medium"
    return "low"


def crop_image_to_temp(path: Path, top_frac: float, bottom_frac: float) -> Path:
    """按比例裁剪图片并写入同目录临时文件，返回临时文件路径（不改动原图）。

    参数:
        path: 原图绝对路径
        top_frac: 顶部裁剪比例（相对高度）
        bottom_frac: 底部裁剪比例（相对高度）

    返回:
        临时文件路径（由调用方负责替换或清理）

    异常:
        ValueError: 裁剪比例非法或合计超出图片高度
    """
    if not (0 <= top_frac < 1) or not (0 <= bottom_frac < 1):
        raise ValueError(f"裁剪比例必须位于 [0, 1): top={top_frac}, bottom={bottom_frac}")

    # 临时文件名带随机串：同一素材被并发处理或存在历史残留文件时互不干扰；
    # 任何失败路径都会在下方清理临时文件后重抛
    tmp = path.with_name(f"{path.stem}_crop_{uuid.uuid4().hex}{path.suffix}")
    try:
        with Image.open(path) as src:
            img = ImageOps.exif_transpose(src)
            width, height = img.size
            top = round(height * top_frac)
            bottom = round(height * bottom_frac)
            if top + bottom >= height:
                raise ValueError(f"裁剪比例过大：顶部 {top}px + 底部 {bottom}px ≥ 高度 {height}px")
            cropped = img.crop((0, top, width, height - bottom))
            fmt = src.format or "JPEG"
            save_kwargs: dict = {}
            if fmt == "JPEG":
                save_kwargs = {"quality": 95, "subsampling": 0, "optimize": True}
            try:
                cropped.save(tmp, format=fmt, **save_kwargs)
            except (ValueError, OSError):
                # 个别格式（如 MPO）PIL 无法写回，降级为 JPEG；后缀同步改为 .jpg，
                # 避免文件扩展名与实际内容格式不一致导致前端解码失败
                if fmt == "JPEG":
                    raise
                tmp.unlink(missing_ok=True)
                tmp = tmp.with_suffix(".jpg")
                cropped.save(tmp, format="JPEG", quality=95, subsampling=0, optimize=True)
        return tmp
    except Exception:
        # 任何失败路径都清理残留临时文件后重抛
        tmp.unlink(missing_ok=True)
        raise


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
        mode: auto（黑边自动检测，逐张计算裁剪比例）/ ratio（统一按比例裁剪）
        crop_top: 顶部裁剪比例（仅 ratio 模式生效）
        crop_bottom: 底部裁剪比例（仅 ratio 模式生效）
        limit: 单次最多返回的候选数（0 表示不限制）

    返回:
        {
            "total": 候选总数（limit 截断前）,
            "items": [{
                "id": str, "file_path": str, "width": int, "height": int,
                "ratio": float, "crop_top": float, "crop_bottom": float,
                "auto_ok": bool, "note": str | None,  # auto 检测失败原因
                "confidence": "high" | "medium" | "low",  # 截图特征置信度
                "created_at": str | None,  # 上传时间（ISO）
            }, ...],
        }
    """
    if mode not in ("auto", "ratio"):
        raise ValueError(f"不支持的裁剪模式: {mode}（允许 auto / ratio）")
    if mode == "ratio" and crop_top + crop_bottom >= 1:
        raise ValueError(f"裁剪比例合计必须 < 1：顶部 {crop_top} + 底部 {crop_bottom}")

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
            width, height = _probe_size(full)
        except Exception:
            continue  # 无法解码的图片不做候选
        if height / width < MIN_RATIO:
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
            "confidence": "low",
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
        # 截图特征检测：状态栏/底部栏 → 置信度分级（供人工筛选）
        try:
            features = await asyncio.to_thread(detect_screenshot_features, full)
            item["confidence"] = screenshot_confidence(features)
        except Exception:
            item["confidence"] = "low"
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
        mode: auto（黑边自动检测）/ ratio（统一按比例裁剪）
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
    if mode not in ("auto", "ratio"):
        raise ValueError(f"不支持的裁剪模式: {mode}（允许 auto / ratio）")
    if mode == "ratio" and crop_top + crop_bottom >= 1:
        raise ValueError(f"裁剪比例合计必须 < 1：顶部 {crop_top} + 底部 {crop_bottom}")
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
        if height / width < MIN_RATIO:
            skipped.append(_skip_entry(insp, "非竖屏截图（高/宽 < 1.75）"))
            continue
        if mode == "auto":
            try:
                top_px, bottom_px = await asyncio.to_thread(detect_photo_band, full)
                t_frac = round(top_px / height, 6)
                b_frac = round((height - 1 - bottom_px) / height, 6)
            except ValueError as e:
                skipped.append(_skip_entry(insp, f"自动检测失败：{e}"))
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
    # 登记失败不影响主流程（裁剪已成功提交，向量可由后续任务/手动重建兜底）
    vector_task_id: int | None = None
    if successes:
        try:
            task = await enqueue_vector_backfills(db, [s[0].id for s in successes])
            vector_task_id = task.id if task else None
        except Exception:
            logger.exception("裁剪后登记向量回填失败，不影响裁剪主流程")

    logger.info(
        f"手机图裁剪完成: 确认 {len(ids)}，成功 {len(successes)}，"
        f"跳过 {len(skipped)}，内容重复待用户决策 {len(duplicates)}，备份 {backup_dir}"
    )
    return {
        "processed": len(successes),
        "skipped": skipped,
        "duplicates": duplicates,
        "backup_dir": str(backup_dir) if successes else None,
        "vector_task_id": vector_task_id,
    }
