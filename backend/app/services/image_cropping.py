"""纯图像裁剪算法：黑边检测、截图特征、内容边界检测、按比例裁剪。

本 module 是 **deep module**：interface 只暴露按比例裁剪到临时文件
（``crop_image_to_temp`` / ``crop_region_to_temp``）与边界检测
（``detect_photo_band`` / ``detect_content_bounds`` /
``detect_screenshot_features`` / ``screenshot_confidence``）等少量函数，
内部封装了 EXIF 方向校正、行亮度/多样度剖面、状态栏簇对比、灰带判定等
大量实现细节。

设计约束
    - **纯函数**：只依赖路径与 PIL/numpy，不 import sqlalchemy、不碰
      async session、不读写数据库或应用配置。因此算法可用一张合成图在
      毫秒级直测，无需起 FastAPI app / DB / 存储。
    - 裁剪编排（查素材、业务过滤、事务、备份、重建缩略图/哈希/向量）在
      ``services/crop_service.py``，它调用本 module 的纯函数。
    - 所有「显示尺寸/裁剪坐标」均按 EXIF Orientation 校正后的图像计算，
      与实际写回阶段的 ``ImageOps.exif_transpose`` 保持一致。
"""

import uuid

import numpy as np
from PIL import Image, ImageOps

# 手动裁剪最小保留高度（自然像素）：与前端 Cropper.js minCropBoxHeight=50
# 的口径对齐，避免确认出极窄（<50px）的裁剪结果
MIN_MANUAL_CROP_HEIGHT_PX = 50

# ── 黑边自动检测参数 ──
BRIGHT_PIXEL_THRESHOLD = 25  # 灰度值 > 25 视为「亮像素」
CONTENT_ROW_FRACTION = 0.005  # 一行中亮像素占比 > 0.5% 视为内容行
MIN_MAIN_BAND_HEIGHT_FRACTION = 0.25  # 主体条带高度至少占全图 25%
MAX_OTHER_BANDS_FRACTION = 0.5  # 其他条带总高度不超过主体的 50%

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
_CONTENT_MIN_D = 0.15  # 内容行多样度下限
_CONTENT_MIN_RUN = 3  # 内容区起始的最小连续行数（防单行噪声）
_CONTENT_MAX_GAP = 5  # 内容区内部允许的最大缺口行数（照片纯色块/暗部）
# 状态栏修正窗口/阈值（详见 _status_bar_correction / _residual_top_estimate）
_STATUS_BAR_MED = 0.25
_STATUS_BAR_RATIO = 1.25
_STATUS_BAR_SCAN_FRACTION = 0.08
_STATUS_BAR_SEARCH_FRACTION = 0.2
_RESIDUAL_MED = 0.28
# 灰带判定（0~1 归一化）：低饱和 + 亮度平坦（纹理少）
_SAT_GRAY = 0.2
_GRAY_BAND_STD_MAX = 0.06
# 内容区占比下限（灰带包夹的照片主体不得过小）
_CONTENT_FRACTION_MIN = 0.25
# 内容边界检测的分析宽度（行剖面逐行统计，宽度只影响多样度灵敏度）
_CONTENT_ANALYZE_W = 96

# EXIF Orientation 取值：5/6/7/8 表示 90°/270° 旋转，宽高互换
_EXIF_TRANSPOSE_90 = frozenset((5, 6, 7, 8))


def probe_size(path) -> tuple[int, int]:
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


def detect_photo_band(path) -> tuple[int, int]:
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


def detect_screenshot_features(path) -> dict:
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


# ── 内容边界检测（mode="content"，与黑边检测/固定比例并存）──


def _row_profiles(path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
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


def detect_content_bounds(path) -> dict:
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


def _save_cropped_image(cropped: Image.Image, src: Image.Image, tmp) :
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


def crop_image_to_temp(path, top_frac: float, bottom_frac: float):
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


def crop_region_to_temp(path, y1_ratio: float, y2_ratio: float):
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
