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

import numpy as np
from fastapi import HTTPException
from PIL import Image, ImageOps
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.inspiration import Inspiration, NOT_DELETED
from app.services.file_service import generate_thumbnail
from app.services.task_runners.vector_backfill import enqueue_vector_backfills
from app.utils.file_hash import file_sha256
from app.utils.image_hash import perceptual_hash
from app.utils.image_utils import extract_dominant_colors
from app.utils.time import utcnow

logger = logging.getLogger(__name__)

# 竖屏截图判定：高/宽 ≥ 1.75（9:16≈1.78、19.5:9≈2.17）
MIN_RATIO = 1.75
# 手动裁剪最小保留高度（自然像素）：与前端 Cropper.js minCropBoxHeight=50
# 的口径对齐，避免确认出极窄（<50px）的裁剪结果
MIN_MANUAL_CROP_HEIGHT_PX = 50
# content 模式的竖屏下限放宽到 1.3：已被裁剪过的截图比例可能掉到 1.3~1.75
# （原 2.17 裁 40% 后 ≈1.3），顶部状态栏残留仍需二次裁剪；内容边界检测
# 自带「内容区占比」与「残留簇 + 后随内容更高」校验兜底，不会误裁普通照片。
CONTENT_MIN_RATIO = 1.3
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

# ── 内容边界检测参数（2026-08 对 100 张手动上传素材行剖面分析校准）──
# 内容行 = 行内颜色多样度 ≥ _CONTENT_MIN_D；灰带/状态栏/播放器条等非内容
# 地带多样度低（< 0.08），内容区通常 ≥ 0.22，取 0.15 做分界稳健且抗噪。
_CONTENT_MIN_D = 0.15  # 内容行多样度下限
_CONTENT_MIN_RUN = 3  # 内容区起始的最小连续行数（防单行噪声）
_CONTENT_MAX_GAP = 5  # 内容区内部允许的最大缺口行数（照片纯色块/暗部）
# 状态栏修正：顶部 8% 高度内首个内容簇多样度中位 < 0.25，且其后 20% 高度内
# 存在多样度中位 ≥ 首簇 × 1.25 的内容簇 → 首簇判定为状态栏图标行（顶部
# 状态栏与内容区多样度连续过渡，单靠阈值无法干净切分，需簇间对比）。
# 窗口取 8%：状态栏图标行簇极薄（实测 <3%），窗口过大（如 12%）会把
# 紧随其后的内容区并入首簇、拉高中位数导致修正失效。
_STATUS_BAR_MED = 0.25
_STATUS_BAR_RATIO = 1.25
_STATUS_BAR_SCAN_FRACTION = 0.08
_STATUS_BAR_SEARCH_FRACTION = 0.2
# 已裁截图顶格状态栏残留的簇多样度上限：透明状态栏图标叠加在照片上，
# 残留行多样度实测 0.16~0.27（如 51e564d6 类，仅裁 2 行会残留图标下沿），
# 用 0.28 完整框住残留簇（普通照片顶部自然波动由「后随内容显著更高」兜底）
_RESIDUAL_MED = 0.28
# 灰带判定（0~1 归一化）：低饱和 + 亮度平坦（纹理少）
_SAT_GRAY = 0.2
_GRAY_BAND_STD_MAX = 0.06
# 内容区占比下限（灰带包夹的照片主体不得过小）
_CONTENT_FRACTION_MIN = 0.25
# 内容边界检测的分析宽度（行剖面逐行统计，宽度只影响多样度灵敏度）
_CONTENT_ANALYZE_W = 96


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

    # 顶部状态栏：前 15% 高度内存在「图标行簇」（0.1~0.3），
    # 且其后在 40% 高度内出现内容区（>0.25）。
    # 状态栏前提：图标行簇之前必须存在纯色背景行（状态栏背景，多样度 < 0.1）——
    # 照片暗部/夜景等大块低多样度区域通常直接顶格（无背景行），据此排除
    top_bar = False
    top_limit = int(n * _TOP_SCAN_FRACTION)
    for i in range(min(2, top_limit), top_limit):
        if _ROW_UNIFORM < rows[i] <= _ROW_STATUS_BAR:
            # 图标行簇前 6 行内须有纯色背景行（状态栏背景）
            if not any(rows[k] < _ROW_UNIFORM for k in range(max(0, i - 6), i)):
                continue
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


# ── 内容边界检测（mode="content"，2026-08 新增，与黑边检测/固定比例并存）──

def _row_profiles(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """计算图片的行级剖面：亮度均值、饱和度均值、颜色多样度（0~1，逐行）。

    与 detect_screenshot_features 的缩放策略一致（EXIF 校正 → 统一宽度缩放），
    但宽度取 _CONTENT_ANALYZE_W（96），行剖面按相对高度输出，与绝对分辨率无关。

    参数:
        path: 图片绝对路径

    返回:
        (brightness, saturation, diversity) 三个长度 = 缩放后高度的数组
    """
    with Image.open(path) as im:
        img = ImageOps.exif_transpose(im).convert("RGB")
        small = img.resize(
            (_CONTENT_ANALYZE_W, max(16, img.height * _CONTENT_ANALYZE_W // img.width)),
            Image.Resampling.LANCZOS,
        )
    arr = np.asarray(small).astype(np.float32) / 255.0
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    brightness = (r * 0.299 + g * 0.587 + b * 0.114).mean(axis=1)
    mx = arr.max(axis=2)
    mn = arr.min(axis=2)
    with np.errstate(divide="ignore", invalid="ignore"):
        saturation = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0.0)
    saturation = saturation.mean(axis=1)
    # 颜色多样度：16 级量化后每行唯一色占比
    q = (arr * 16).astype(np.int32)
    diversity = np.array(
        [
            len({(int(v[0]), int(v[1]), int(v[2])) for v in q[y]}) / _CONTENT_ANALYZE_W
            for y in range(arr.shape[0])
        ]
    )
    return brightness, saturation, diversity


def _content_bounds(diversity: np.ndarray) -> tuple[int | None, int | None]:
    """找内容区的上下边界（非内容地带包夹检测的核心）。

    思路：内容行 = 多样度 ≥ _CONTENT_MIN_D 的行。从顶向下找首个「连续
    ≥ _CONTENT_MIN_RUN 行内容」的区段起点作为上界；从底向上对称找下界。
    扫描时允许内容区内部有 ≤ _CONTENT_MAX_GAP 行的缺口（照片中的纯色块/
    暗部），避免提前截断。

    参数:
        diversity: 行多样度剖面

    返回:
        (top_edge, bottom_edge) 内容区上下边界行；找不到返回 None
    """
    n = len(diversity)
    content = diversity >= _CONTENT_MIN_D

    # 上界：向下扫描，缺口内续接；连续内容达到 MIN_RUN 即定为起点
    top_edge: int | None = None
    run = 0
    last_content: int | None = None
    for y in range(n):
        if content[y]:
            if last_content is None or (y - last_content - 1) <= _CONTENT_MAX_GAP:
                run += 1
            else:
                run = 1
            last_content = y
            if run >= _CONTENT_MIN_RUN:
                top_edge = last_content - run + 1
                break

    # 下界：向上扫描对称处理
    bottom_edge: int | None = None
    run = 0
    last_content = None
    for y in range(n - 1, -1, -1):
        if content[y]:
            if last_content is None or (last_content - y - 1) <= _CONTENT_MAX_GAP:
                run += 1
            else:
                run = 1
            last_content = y
            if run >= _CONTENT_MIN_RUN:
                bottom_edge = last_content + run - 1
                break
    return top_edge, bottom_edge


def _residual_top_estimate(diversity: np.ndarray) -> int:
    """疑似顶部状态栏残留的裁剪行数估算（已裁截图，图标叠加在照片上）。

    透明状态栏图标叠加在照片上，残留行多样度实测 0.16~0.27（图标下沿渐变
    可达 0.27），与照片顶部自然低多样度区域（暗角/天空/柜台）在行剖面上
    几乎同构、无法可靠区分。因此本函数只做「疑似」估算，供人工确认后
    裁剪，绝不并入自动裁剪比例。

    判定：顶部 8% 窗口内存在多样度 < _RESIDUAL_MED（0.28）的连续簇（≥2 行）、
    簇中位 < _RESIDUAL_MED - 0.02（0.26）、且其后 30% 高度内内容中位显著
    更高（≥ 簇中位 + 0.08）→ 返回簇结束后首个多样度显著升高的行。

    参数:
        diversity: 行多样度剖面

    返回:
        建议裁剪行数（无法确认残留时返回 0）
    """
    n = len(diversity)
    window = int(n * _STATUS_BAR_SCAN_FRACTION)
    seg_end = 0
    while seg_end + 1 < window and diversity[seg_end + 1] < _RESIDUAL_MED:
        seg_end += 1
    if seg_end < 1:  # 簇至少 2 行才有意义
        return 0
    first_med = float(np.median(diversity[0 : seg_end + 1]))
    if first_med >= _RESIDUAL_MED - 0.02:
        return 0
    tail = diversity[seg_end + 1 : min(n, seg_end + 1 + int(n * 0.3))]
    if len(tail) < 5 or float(np.median(tail)) < first_med + 0.08:
        return 0
    threshold = max(_STATUS_BAR_MED, first_med + 0.05)
    for y in range(seg_end + 1, min(n, window + int(n * _STATUS_BAR_SEARCH_FRACTION))):
        if diversity[y] >= threshold:
            return y
    return 0


def _status_bar_correction(diversity: np.ndarray, top_edge: int) -> int:
    """状态栏修正：顶部内容簇多样度显著低于后续内容区时，视为状态栏并后移边界。

    仅处理未裁截图场景（top_edge > 0）：纯色背景（多样度 < 0.1）→ 状态栏
    图标行簇（多样度 0.15~0.24，极薄）→ 内容区。已裁截图的顶格残留
    （top_edge=0）由 _residual_top_estimate 单独估算，不并入本函数。

    判定规则：首区段（顶部 8% 窗口内）多样度中位 < 0.25、且区段前一行属
    低多样度地带、且区段结束后存在多样度 ≥ max(0.24, 首区段中位 + 0.05)
    的行 → 状态栏，内容边界后移到该行。

    参数:
        diversity: 行多样度剖面
        top_edge: 未修正的内容区上边界

    返回:
        修正后的内容区上边界（无法确认状态栏时原样返回）
    """
    n = len(diversity)
    window = int(n * _STATUS_BAR_SCAN_FRACTION)
    if top_edge >= window or top_edge == 0:
        return top_edge
    # 首区段：从 top_edge 起，仅在窗口内延伸（避免并入内容区拉高中位数）
    seg_end = top_edge
    while seg_end + 1 < window and diversity[seg_end + 1] >= _CONTENT_MIN_D:
        seg_end += 1
    first_med = float(np.median(diversity[top_edge : seg_end + 1]))
    if first_med >= _STATUS_BAR_MED:
        return top_edge
    # 背景前提：区段前一行属于低多样度地带（状态栏纯色背景）
    if diversity[top_edge - 1] >= _ROW_UNIFORM:
        return top_edge
    # 区段结束后找多样度显著升高的行（内容区起点）
    threshold = max(_STATUS_BAR_MED - 0.01, first_med + 0.05)
    search_end = min(n, window + int(n * _STATUS_BAR_SEARCH_FRACTION))
    for y in range(seg_end + 1, search_end):
        if diversity[y] >= threshold:
            return y
    return top_edge


def _band_stats(brightness: np.ndarray, saturation: np.ndarray, lo: int, hi: int) -> dict:
    """统计行区间的灰带特征（饱和度均值、亮度均值、亮度方差）。

    参数:
        brightness: 行亮度剖面
        saturation: 行饱和度剖面
        lo: 起始行（含）
        hi: 结束行（不含）

    返回:
        {"sat_mean", "bright_mean", "bright_std"}
    """
    if hi <= lo:
        return {"sat_mean": 0.0, "bright_mean": 0.0, "bright_std": 0.0}
    return {
        "sat_mean": float(saturation[lo:hi].mean()),
        "bright_mean": float(brightness[lo:hi].mean()),
        "bright_std": float(brightness[lo:hi].std()),
    }


def detect_content_bounds(path: Path) -> dict:
    """检测内容区（照片主体）的上下边界，输出相对高度的裁剪比例（模式 content）。

    针对「手机截图/视频截图」的通用检测：图片上下被非内容地带包夹——
    类型一为灰带（平坦低饱和），类型二为状态栏 + 播放器条/导航栏。两类
    统一用行多样度剖面找内容区边界，灰带平坦度与状态栏簇对比用于分类标注。

    已裁截图的「透明状态栏残留」（图标叠加在照片上）与照片顶部自然低多样度
    区域在行剖面上无法可靠区分（96 宽缩放实测同构），因此残留检测结果不
    并入 top_frac（避免自动误裁普通照片），而是通过 residual_top_frac 单独
    返回，由调用方标注「疑似残留」供人工确认后勾选裁剪。

    参数:
        path: 图片绝对路径

    返回:
        {
            "top_frac": 顶部裁剪比例（相对高度，0~1）,
            "bottom_frac": 底部裁剪比例,
            "top_edge": 内容区上边界行（缩放图坐标系）,
            "bottom_edge": 内容区下边界行,
            "correction": 状态栏修正是否生效,
            "kind": "gray_band"（上下灰带包夹）| "status_bar"（含状态栏修正）| "plain",
            "already_cropped": 是否已裁剪干净（无有效裁剪区域，合计 <1%），
                仅作标注，仍由调用方决定是否列入候选,
            "residual_top_frac": 疑似顶部状态栏残留建议裁剪比例（>0 时由
                人工确认后使用，不并入 top_frac 自动裁剪）,
        }

    异常:
        ValueError: 未检测到内容区或布局不合理（内容区占比过小等）
    """
    brightness, saturation, diversity = _row_profiles(path)
    n = len(diversity)
    if n < 8:
        raise ValueError("图片过小，无法检测内容边界")

    top_edge_raw, bottom_edge = _content_bounds(diversity)
    if top_edge_raw is None or bottom_edge is None or bottom_edge <= top_edge_raw:
        raise ValueError("未检测到内容区边界")
    top_edge = _status_bar_correction(diversity, top_edge_raw)
    correction = top_edge != top_edge_raw
    residual_top_frac = 0.0
    if top_edge == 0:
        # 顶格残留疑似检测：不并入 top_frac（防误裁普通照片），单独返回建议
        residual_top_frac = round(_residual_top_estimate(diversity) / n, 6)

    # 底部边界微调：播放器条/导航栏顶部常为半透明渐变过渡（亮度骤降但多样度
    # 仍 ≥0.15），content_bounds 会把过渡行算进内容区，裁后残留暗带。
    # 若 bottom_edge 行亮度显著低于其上方内容（< 中位 × 0.8），向上回退到
    # 亮度恢复正常处（最多回退 3 行，防误伤照片暗部）。
    if bottom_edge >= 5:
        ref = float(np.median(brightness[max(0, bottom_edge - 20) : bottom_edge]))
        y = bottom_edge
        while y > 0 and brightness[y] < ref * 0.8:
            y -= 1
        if bottom_edge - y <= 3:
            bottom_edge = y

    # 内容区占比下限校验（防灰带过厚/内容区过小误判）
    frac = (bottom_edge - top_edge + 1) / n
    if frac < _CONTENT_FRACTION_MIN:
        raise ValueError(f"内容区占比过小（{frac:.0%}），布局不规则")

    top_frac = top_edge / n
    bottom_frac = (n - 1 - bottom_edge) / n
    # 已裁剪干净：两侧合计可裁比例 <1%。不抛异常、不设内容占比上限——
    # 薄边框截图与残留修正（如顶部状态栏图标残余）都可正常给出裁剪建议，
    # 由调用方按 already_cropped 标注、人工勾选确认兜底
    already_cropped = top_frac + bottom_frac < 0.01

    # 灰带判定：边界外侧低饱和 + 亮度平坦
    top_gray = _band_stats(brightness, saturation, 0, top_edge)
    bot_gray = _band_stats(brightness, saturation, bottom_edge + 1, n)
    top_gray_ok = top_gray["sat_mean"] < _SAT_GRAY and top_gray["bright_std"] < _GRAY_BAND_STD_MAX
    bot_gray_ok = bot_gray["sat_mean"] < _SAT_GRAY and bot_gray["bright_std"] < _GRAY_BAND_STD_MAX

    if top_gray_ok and bot_gray_ok:
        kind = "gray_band"
    elif correction or residual_top_frac > 0:
        kind = "status_bar"
    else:
        kind = "plain"

    return {
        "top_frac": round(top_frac, 6),
        "bottom_frac": round(bottom_frac, 6),
        "top_edge": top_edge,
        "bottom_edge": bottom_edge,
        "correction": correction,
        "kind": kind,
        "already_cropped": already_cropped,
        "residual_top_frac": residual_top_frac,
    }


def _save_cropped_image(cropped: Image.Image, src: Image.Image, tmp: Path) -> Path:
    """把裁剪结果写入同目录临时文件（保留原格式，不可写回时降级 JPEG）。

    参数:
        cropped: 裁剪后的 PIL 图像
        src: 原图（用于读取原始格式）
        tmp: 目标临时文件路径

    返回:
        实际写入的临时文件路径（降级 JPEG 时后缀会变为 .jpg）
    """
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
            tmp = _save_cropped_image(cropped, src, tmp)
        return tmp
    except Exception:
        # 任何失败路径都清理残留临时文件后重抛
        tmp.unlink(missing_ok=True)
        raise


def crop_region_to_temp(path: Path, y1_ratio: float, y2_ratio: float) -> Path:
    """按「保留区域」上下边界比例裁剪图片并写入同目录临时文件（不改动原图）。

    与 crop_image_to_temp（分别裁掉顶部/底部多少）语义互补：本函数直接表达
    「保留区域 = [y1_ratio, y2_ratio)」的上下边界，供素材详情页手动裁剪使用。
    比例基准为 EXIF 方向校正后的显示高度（ImageOps.exif_transpose）。

    参数:
        path: 原图绝对路径
        y1_ratio: 保留区域上边界（相对高度，0~1）
        y2_ratio: 保留区域下边界（相对高度，0~1）

    返回:
        临时文件路径（由调用方负责替换或清理）

    异常:
        ValueError: 比例非法或保留高度小于 MIN_MANUAL_CROP_HEIGHT_PX
    """
    if not (0 <= y1_ratio < y2_ratio <= 1):
        raise ValueError(
            f"裁剪比例非法，需满足 0 ≤ y1 < y2 ≤ 1: y1={y1_ratio}, y2={y2_ratio}"
        )
    tmp = path.with_name(f"{path.stem}_crop_{uuid.uuid4().hex}{path.suffix}")
    try:
        with Image.open(path) as src:
            img = ImageOps.exif_transpose(src)
            width, height = img.size
            top = round(height * y1_ratio)
            bottom = round(height * y2_ratio)
            if bottom - top < MIN_MANUAL_CROP_HEIGHT_PX:
                raise ValueError(
                    f"保留区域高度过小（{bottom - top}px < {MIN_MANUAL_CROP_HEIGHT_PX}px）"
                )
            cropped = img.crop((0, top, width, bottom))
            tmp = _save_cropped_image(cropped, src, tmp)
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
        mode: auto（黑边自动检测，逐张计算裁剪比例）/ ratio（统一按比例裁剪）/
            content（内容边界检测：灰带/状态栏/播放器条包夹的内容区边界）
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
            try:
                bounds = await asyncio.to_thread(detect_content_bounds, full)
                item["crop_top"] = bounds["top_frac"]
                item["crop_bottom"] = bounds["bottom_frac"]
                item["boundary_kind"] = bounds["kind"]
                if bounds["residual_top_frac"] > 0 and bounds["top_frac"] == 0:
                    # 疑似顶部状态栏残留（透明图标叠加照片，自动检测不可靠）：
                    # 不自动判定可裁剪（防误裁普通照片），标注建议比例供人工
                    # 目检后勾选；勾选后 apply 按此建议裁剪
                    item["auto_ok"] = False
                    item["crop_top"] = bounds["residual_top_frac"]
                    item["note"] = (
                        f"疑似顶部状态栏残留（建议裁剪 {bounds['residual_top_frac']:.1%}），"
                        "确认后勾选裁剪"
                    )
                elif bounds["already_cropped"]:
                    # 已裁剪干净（或内容占满全图）：仍列入候选供用户可见，
                    # 标记不可裁剪并说明原因（人工可确认是否确实无需再裁）
                    item["auto_ok"] = False
                    item["note"] = "已裁剪过或内容占满全图，无需裁剪"
            except ValueError as e:
                item["auto_ok"] = False
                item["note"] = f"内容边界检测失败：{e}"
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
    insp = await db.get(Inspiration, inspiration_id)
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
