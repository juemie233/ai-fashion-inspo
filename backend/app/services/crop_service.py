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

from PIL import Image, ImageOps
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.inspiration import Inspiration, NOT_DELETED
from app.services.file_service import generate_thumbnail
from app.services.task_runners.vector_backfill import create_vector_backfill_task
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
        arr = np.asarray(im.convert("L"))
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

    tmp = path.with_name(f"{path.stem}_crop_tmp{path.suffix}")
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
            # 个别格式（如 MPO）PIL 无法写回，降级为 JPEG
            if fmt != "JPEG":
                cropped.save(tmp, format="JPEG", quality=95, subsampling=0, optimize=True)
            else:
                raise
    return tmp


async def crop_phone_screenshots(
    db: AsyncSession,
    mode: str = "auto",
    crop_top: float = DEFAULT_CROP_TOP,
    crop_bottom: float = DEFAULT_CROP_BOTTOM,
    limit: int = 200,
) -> dict:
    """一键裁剪手动上传素材中的手机全屏截图（扫描 + 执行一步完成）。

    参数:
        db: 数据库会话
        mode: auto（黑边自动检测，检测失败跳过）/ ratio（统一按比例裁剪）
        crop_top: 顶部裁剪比例（仅 ratio 模式生效）
        crop_bottom: 底部裁剪比例（仅 ratio 模式生效）
        limit: 单次最多处理的候选数（0 表示不限制；超大库建议分批）

    返回:
        {
            "scanned": 候选总数,
            "processed": 成功裁剪数,
            "skipped": [{"id": str, "reason": str}, ...],
            "backup_dir": str | None,
            "vector_task_id": int | None,
        }
    """
    if mode not in ("auto", "ratio"):
        raise ValueError(f"不支持的裁剪模式: {mode}（允许 auto / ratio）")

    # 1. 扫描候选：手动上传 + 未删除 + 图片 + 竖屏比例（高/宽 ≥ 1.75）
    result = await db.execute(
        select(Inspiration).where(
            Inspiration.source_type == "manual_upload",
            Inspiration.deleted_at.is_(None),
            Inspiration.file_path.isnot(None),
        )
    )
    candidates = []
    for insp in result.scalars():
        if insp.media_type != "image":
            continue
        full = settings.storage_root / insp.file_path
        if not full.exists():
            continue
        try:
            with Image.open(full) as probe:
                width, height = probe.size
        except Exception:
            continue  # 无法解码的图片不做候选
        if height / width < MIN_RATIO:
            continue
        candidates.append((insp, full, width, height))

    candidates.sort(key=lambda c: c[3] / c[2], reverse=True)
    if limit > 0:
        candidates = candidates[:limit]
    if not candidates:
        return {"scanned": 0, "processed": 0, "skipped": [], "backup_dir": None, "vector_task_id": None}

    # 2. 逐张确定裁剪比例（auto：黑边检测；ratio：统一比例）
    plans: list[tuple[Inspiration, Path, float, float, str | None]] = []
    skipped: list[dict] = []
    for insp, full, width, height in candidates:
        if mode == "auto":
            try:
                top_px, bottom_px = await asyncio.to_thread(detect_photo_band, full)
                t_frac = round(top_px / height, 6)
                b_frac = round((height - 1 - bottom_px) / height, 6)
            except ValueError as e:
                skipped.append({"id": insp.id, "reason": f"自动检测失败：{e}"})
                continue
        else:
            t_frac, b_frac = crop_top, crop_bottom
        plans.append((insp, full, t_frac, b_frac, None))

    # 3. 逐张执行裁剪（PIL 操作为阻塞 I/O，放线程池）
    backup_dir = (
        settings.storage_root / "_crop_backup" / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    )
    successes: list[tuple[Inspiration, str | None, str | None, str | None]] = []
    for insp, full, t_frac, b_frac, _ in plans:
        tmp: Path | None = None
        try:
            tmp = await asyncio.to_thread(crop_image_to_temp, full, t_frac, b_frac)

            # 新内容哈希 + 去重检查（重复则放弃本条，保留原图）
            new_hash = file_sha256(tmp)
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
                if dup_id:
                    skipped.append({"id": insp.id, "reason": f"裁剪结果与素材 {dup_id} 内容重复"})
                    tmp.unlink(missing_ok=True)
                    continue

            # 备份原图 → 原子替换
            backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(full, backup_dir / f"{insp.id}{full.suffix}")
            os.replace(tmp, full)
            tmp = None

            # 重新生成缩略图（失败仅告警，沿用旧缩略图）
            thumb_path = insp.thumbnail_path
            new_thumb = await generate_thumbnail(full)
            if new_thumb:
                if thumb_path and (settings.storage_root / thumb_path) != (settings.storage_root / new_thumb):
                    (settings.storage_root / thumb_path).unlink(missing_ok=True)
                thumb_path = new_thumb

            # 主色调重算（仅原值非空时刷新，避免引入新的过滤属性）
            colors = insp.dominant_colors
            if colors is not None:
                new_colors = await asyncio.to_thread(extract_dominant_colors, full)
                colors = json.dumps(new_colors) if new_colors else insp.dominant_colors

            successes.append((insp, new_hash or None, thumb_path, colors))
        except Exception as e:
            if tmp is not None and tmp.exists():
                tmp.unlink(missing_ok=True)
            skipped.append({"id": insp.id, "reason": f"处理失败: {e}"})

    # 4. 写回数据库（标签/收藏/来源等字段不动）
    for insp, new_hash, thumb_path, colors in successes:
        if new_hash:
            insp.content_hash = new_hash
        insp.thumbnail_path = thumb_path
        insp.dominant_colors = colors
        insp.updated_at = utcnow()
    await db.commit()

    # 5. 向量回填：图像向量按新图重建，文本向量沿用现有标签
    vector_task_id: int | None = None
    if successes:
        task = await create_vector_backfill_task(db, [s[0].id for s in successes])
        vector_task_id = task.id if task else None

    logger.info(
        f"手机图裁剪完成: 候选 {len(candidates)}，成功 {len(successes)}，"
        f"跳过 {len(skipped)}，备份 {backup_dir}"
    )
    return {
        "scanned": len(candidates),
        "processed": len(successes),
        "skipped": skipped,
        "backup_dir": str(backup_dir) if successes else None,
        "vector_task_id": vector_task_id,
    }
