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

# ── 系统 UI 地带有效性校验（2026-08 修复「普通照片误报可裁剪」）──
# 背景：_content_bounds 只凭「多样度低于内容下限」找边界，会把照片顶部/底部的
# 自然低多样度区域（暗角、渐变、纯色天空/地面）误判为「状态栏/导航栏/灰带」，
# 产出 0.8% / 4.7% 之类的微小"可裁剪"比例。这里用三个判据区分系统 UI 与照片内容：
# 1. 宽度：地带至少 _UI_BAND_MIN_FRACTION 高度（防 1~2 行噪声）
# 2. 硬跃变：内容区与地带多样度中位差 ≥ _UI_BAND_JUMP_MIN（系统 UI 是突变边界，
#    照片暗角/渐变是渐进过渡，跃变不足）
# 3. 结构：地带要么整段近纯色（导航栏/灰带/手势条），要么是「纯色背景行 + 图标
#    行簇」的状态栏结构（带内多样度起伏但峰前存在纯色背景行）
_UI_BAND_MIN_FRACTION = 0.015  # 地带最小宽度（占全图高度）
_UI_BAND_PURE_MAX = 0.13  # 纯色地带多样度上限（整段低于此值视为纯色带）
_UI_BAND_NOISE_MAX = 0.09  # 压缩/JPEG 伪影噪声上限：低于此值不判渐变（纯色块边缘
# 伪影会把后几行多样度抬到 0.03~0.07，非照片渐变）
_UI_BAND_JUMP_MIN = 0.12  # 地带 → 内容区的多样度硬跃变下限
_UI_BAND_GRADIENT_DELTA = 0.05  # 地带前后半段中位差超过此值 → 缓升渐变（暗角）→ 拒绝
_UI_BAND_SPIKE_MIN = 0.08  # 图标/文字行的「突变峰」单步增量下限（区别于渐变缓升）
# 单侧最小可裁比例：低于此值的裁剪建议视为噪声置 0（真实状态栏 ≥2.5%；
# 1%~2% 的薄残留交由 residual 疑似路径人工确认，不自动裁）
_CONTENT_MIN_CROP_FRACTION = 0.02

# ── 顶部疑似残留估算参数（_residual_top_estimate）──
# 真实状态栏条带（含透明残留）饱和度极低（实测 ≤0.12），照片顶部渐变
# （天空/虚化背景）饱和度高（实测 0.15~0.8）——sat 是残留 vs 照片渐变
# 的首要区分信号
_RESIDUAL_SAT_MAX = 0.15
# 条带后内容区多样度相对条带中位的抬升下限：有 UI 特征（截图证据）时
# 从宽（低多样度内容区也能续上建议）。无证据时沿用原 0.08——实测照片
# 渐变的 tail 抬升普遍 >0.15，收紧到 0.12 并不能多拦照片误报（真正的
# 区分信号是 sat 通道与条带内部渐升检测），反而会误伤真实残留
_RESIDUAL_TAIL_JUMP_LOOSE = 0.06
_RESIDUAL_TAIL_JUMP_STRICT = 0.08
# 内容区起点多样度下限：有 UI 特征图纸截图内容区常为低多样度
# （白底/浅色穿搭图 0.15~0.25），阈值过高会漏检；无证据时用原 0.25
_RESIDUAL_CONTENT_MIN_LOOSE = 0.16
_RESIDUAL_CONTENT_MIN_STRICT = 0.25

# ── 顶部状态栏字形证据检测（glyph）──
# 行多样度剖面的原理性盲区：透明叠加状态栏（图标直接叠在照片上）没有「低多
# 样度条带」，行剖面与照片顶部自然低多样度区域同构；而影棚纯色背景照片的反
# 向误报（顶部纯色带 + 内容抬升被当状态栏）也无法靠行剖面排除。两者的共同
# 解法是状态栏唯一稳定的结构特征——字形布局：时间（左上）+ 信号/电量（右上）
# 是「小、孤立、高对比、集中两角、中间空」的连通域，与主题无关、与底图无关。
_GLYPH_ANALYZE_W = 320  # 字形分析宽度（96 宽下状态栏字形仅数像素，分辨率不足）
_GLYPH_TOP_FRACTION = 0.10  # 只分析图片顶部 10%（状态栏 + 少量余量）
_GLYPH_CONTRAST = 22.0  # 局部对比二值化阈值（像素 − 5×5 均值，0~255）
_GLYPH_MARGIN_ROWS = 2  # 字形底部再多裁的行数（缩放坐标系，覆盖图标抗锯齿边）
_GLYPH_TOP_FRAC_CAP = 0.12  # 字形路径建议裁剪比例上限（状态栏不会超过全高 12%）
_FULL_SCREENSHOT_RATIO = 1.8  # 完整手机截图先验：高/宽 ≥ 此值的竖图极大概率是截图

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


def _row_diversity(small: Image.Image, analyze_w: int) -> np.ndarray:
    """计算缩放图的行内颜色多样度（16 级量化后每行唯一色占比，numpy 向量化）。

    逐行 Python set 在 5000+ 张素材的全量扫描里是性能瓶颈（每张 ~30ms 纯
    Python 循环）；向量化后单张 <1ms。量化口径与旧实现一致（r//16 等，
    16 级），输出 0~1 的每行多样度数组。
    """
    arr = np.asarray(small)
    q = (arr // 16).astype(np.int64)
    enc = (q[..., 0] << 16) | (q[..., 1] << 8) | q[..., 2]
    s = np.sort(enc, axis=1)
    unique_per_row = (s[:, 1:] != s[:, :-1]).sum(axis=1).astype(np.float32) + 1.0
    return unique_per_row / analyze_w


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
    return _features_from_small(small)


def _features_from_small(small: Image.Image) -> dict:
    """从缩放图计算截图特征（detect_screenshot_features 的计算核心，供单次
    解码合并路径复用；扫描全量素材时避免每张图重复完整解码）。"""
    n = small.height
    rows = _row_diversity(small, _ANALYZE_W)

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


def _profiles_from_small(small: Image.Image) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """从缩放图计算行级剖面（内容边界检测的计算核心，供 detect_content_bounds
    与单次解码合并路径 analyze_screenshot_combined 复用，避免重复完整解码）。"""
    arr = np.asarray(small).astype(np.float32) / 255.0
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    brightness = (r * 0.299 + g * 0.587 + b * 0.114).mean(axis=1)
    mx = arr.max(axis=2)
    mn = arr.min(axis=2)
    with np.errstate(divide="ignore", invalid="ignore"):
        saturation = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0.0)
    saturation = saturation.mean(axis=1)
    # 颜色多样度：16 级量化后每行唯一色占比（numpy 向量化；逐行 set 是
    # 全量扫描的性能瓶颈，旧实现每张 ~30ms 纯 Python 循环）
    q = (arr * 16).astype(np.int64)
    enc = (q[..., 0] << 16) | (q[..., 1] << 8) | q[..., 2]
    s = np.sort(enc, axis=1)
    unique_per_row = (s[:, 1:] != s[:, :-1]).sum(axis=1).astype(np.float32) + 1.0
    diversity = unique_per_row / _CONTENT_ANALYZE_W
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


def _residual_top_estimate(
    diversity: np.ndarray, saturation: np.ndarray | None = None, ui_evidence: bool = False
) -> int:
    """疑似顶部状态栏残留的裁剪行数估算（已裁截图，图标叠加在照片上）。

    透明状态栏图标叠加在照片上，残留行多样度实测 0.16~0.27（图标下沿渐变
    可达 0.27），与照片顶部自然低多样度区域（暗角/天空/柜台）在行剖面上
    几乎同构、无法仅凭多样度可靠区分。因此本函数只做「疑似」估算，供人工
    确认后裁剪，绝不并入自动裁剪比例。

    区分信号（真实数据校准，2026-09）：
    - 饱和度：真实状态栏条带（含透明残留）饱和度极低（实测 ≤0.12）；
      照片顶部渐变（天空/虚化背景）饱和度高（实测 0.15~0.8）且随行渐升。
      条带内 sat 中位过高 → 照片渐变，拒绝。
    - 条带内部形态：照片渐变多样度单调渐升（前后半中位差 ≥0.05）；系统
      UI 条带为「低位平台 + 图标小峰」，前后半持平。
    - 内容抬升量：条带后内容区须显著更高。无 UI 证据时从严（+0.12，
      防照片渐变误报）；有 UI 特征（top_bar/bottom_bar 截图证据）时从宽
      （+0.06，低多样度内容区——白底/浅色穿搭图——也能续上建议）。
    - 内容起点多样度：无证据时要求 ≥0.25（原判据）；有证据时放宽到
      0.16，避免低多样度内容图被漏检。

    参数:
        diversity: 行多样度剖面
        saturation: 行饱和度剖面（None 时跳过饱和度通道校验）
        ui_evidence: 是否具备截图证据（detect_screenshot_features 检出
            top_bar/bottom_bar）。True 时放宽内容抬升/起点门槛，False 时
            从严防照片渐变误报。

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
    # 饱和度通道：条带内 sat 中位过高 → 照片渐变（天空/虚化），拒绝
    if saturation is not None:
        band_sat = float(np.median(saturation[0 : seg_end + 1]))
        if band_sat > _RESIDUAL_SAT_MAX:
            return 0
    # 内容抬升量：有证据从宽（0.06），无证据沿用原 0.08
    tail = diversity[seg_end + 1 : min(n, seg_end + 1 + int(n * 0.3))]
    min_jump = _RESIDUAL_TAIL_JUMP_LOOSE if ui_evidence else _RESIDUAL_TAIL_JUMP_STRICT
    if len(tail) < 5 or float(np.median(tail)) < first_med + min_jump:
        return 0
    # 内容起点多样度：无证据用原 0.25，有证据放宽到 0.16
    content_min = _RESIDUAL_CONTENT_MIN_LOOSE if ui_evidence else _RESIDUAL_CONTENT_MIN_STRICT
    threshold = max(content_min, first_med + 0.05)
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


def _ui_band_valid(
    diversity: np.ndarray, lo: int, hi: int, content_row: int
) -> bool:
    """判定 [lo, hi) 地带是否为「系统 UI 地带」（状态栏/导航栏/播放器条/灰带）。

    照片顶部/底部的自然低多样度区域（暗角、渐变、纯色天空/地面）与系统 UI
    在行剖面上相似，本函数用三个判据区分：

    1. 宽度：地带至少 ``_UI_BAND_MIN_FRACTION`` 高度（防 1~2 行噪声）；
    2. 硬跃变：内容区首行多样度比地带中位高出至少 ``_UI_BAND_JUMP_MIN``
       ——系统 UI 与内容区是突变边界，照片暗角/渐变是渐进过渡（中位被
       后半段拉高，跃变不足）；
    3. 结构：地带要么整段近纯色（导航栏/灰带/手势条），要么是「纯色背景行
       + 图标行簇」的状态栏结构（带内多样度最高行之前存在纯色背景行）；
       纯粹的单调上升（渐变）视为照片自然区域，拒绝。

    参数:
        diversity: 行多样度剖面
        lo: 地带起始行（含）
        hi: 地带结束行（不含）
        content_row: 内容区紧邻地带的行（多样度跃变参照）

    返回:
        该地带是否为可裁剪的系统 UI 地带
    """
    n = len(diversity)
    w = hi - lo
    if w < max(2, int(n * _UI_BAND_MIN_FRACTION)):
        return False
    band = diversity[lo:hi]
    content_val = float(diversity[content_row])
    band_med = float(np.median(band))
    # 硬跃变：内容区与地带中位差异不足 → 渐进过渡（暗角/渐变）→ 拒绝
    if content_val - band_med < _UI_BAND_JUMP_MIN:
        return False

    # 结构判定：
    band_max = float(band.max())
    if band_max < _UI_BAND_PURE_MAX:
        # 纯色地带（导航栏/灰带/手势条/状态栏纯色背景）——排除「缓升渐变」：
        # 照片暗角/天空渐变的多样度前后半段呈上升趋势，纯色地带前后一致。
        # 注意 JPEG 压缩会在纯色块边缘产生 0.03~0.07 的伪影上升（非照片渐变），
        # 仅当多样度突破 _UI_BAND_NOISE_MAX 时才启用渐变检测。
        if (
            band_max >= _UI_BAND_NOISE_MAX
            and len(band) >= 4
        ):
            half = len(band) // 2
            lo_med = float(np.median(band[:half]))
            hi_med = float(np.median(band[half:]))
            if hi_med >= lo_med + _UI_BAND_GRADIENT_DELTA:
                return False
        return True

    # 图标/文字簇结构（状态栏、带文字的播放器条）：带内存在中等多样度的峰，
    # 峰前有纯色背景行，且峰是「突变」（单步增量大，区别于渐变的缓升）
    peak = int(np.argmax(band))
    if band[peak] >= _UI_BAND_PURE_MAX and any(
        d < _ROW_UNIFORM for d in band[:peak]
    ):
        step = float(band[peak] - band[peak - 1]) if peak > 0 else float(band[peak])
        if step >= _UI_BAND_SPIKE_MIN:
            return True
    return False


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
    with Image.open(path) as im:
        img = ImageOps.exif_transpose(im).convert("RGB")
        src_w, src_h = img.size
        small = img.resize(
            (_CONTENT_ANALYZE_W, max(16, img.height * _CONTENT_ANALYZE_W // img.width)),
            Image.Resampling.LANCZOS,
        )
        result = _content_bounds_from_small(small)
        # 字形证据合并（与 analyze_screenshot_combined 同口径，供 apply 路径
        # 复现扫描阶段的字形建议）：仅当无实底条带裁剪建议时使用
        glyph = _glyph_evidence(img)
        result["glyph_top_frac"] = (
            0.0 if result["top_frac"] > 0 else (glyph["top_frac"] if glyph["found"] else 0.0)
        )
        result["glyph_strong"] = glyph["strong"]
        # strong 放宽（与 combined 同口径）：已确证截图（already_cropped）+
        # 字形存在 + 完整截图比例先验（≥1.5）→ 视为强证据。透明状态栏的字形
        # 常因 JPEG 压缩粘连而不满足严格时间签名，但 already+ratio 先验下
        # 误报风险可控（历史 FP 全部 ratio<1.5）
        if (
            not result["glyph_strong"]
            and result["already_cropped"]
            and result["glyph_top_frac"] > 0
            and src_h >= 1.5 * src_w
        ):
            result["glyph_strong"] = True
        result["bounds_valid"] = True
        return result


def _content_bounds_from_small(small: Image.Image, ui_evidence: bool = False) -> dict:
    """从缩放图计算内容边界（detect_content_bounds 的计算核心，供单次解码
    合并路径复用；扫描全量素材时避免每张图重复完整解码）。

    参数:
        small: 统一宽度缩放的 RGB 小图
        ui_evidence: 是否具备截图证据（detect_screenshot_features 检出
            top_bar/bottom_bar）。True 时残留估算放宽内容抬升/起点门槛，
            让「低多样度内容区」的真实截图（白底/浅色穿搭图）也能给出
            建议；False（无截图证据）时从严防照片渐变误报。

    异常:
        ValueError: 未检测到内容区或布局不合理（内容区占比过小等）
    """
    brightness, saturation, diversity = _profiles_from_small(small)
    n = len(diversity)
    if n < 8:
        raise ValueError("图片过小，无法检测内容边界")

    top_edge_raw, bottom_edge_raw = _content_bounds(diversity)
    if top_edge_raw is None or bottom_edge_raw is None or bottom_edge_raw <= top_edge_raw:
        raise ValueError("未检测到内容区边界")

    # ── 顶部地带有效性校验：照片顶部自然低多样度（暗角/渐变/纯色块）不是系统 UI，
    #    即便 _content_bounds 算出了边界也不裁；只有通过 _ui_band_valid 的地带
    #    才允许进入状态栏精调并计入 top_frac ──
    correction = False
    top_frac = 0.0
    top_band_valid = False
    if top_edge_raw > 0:
        if ui_evidence:
            # 有截图证据（状态栏/导航栏特征）：低饱和条带（sat≤0.15）比照片
            # 渐变可信，允许更小的多样度跃变（内容区多样度本身低的截图——
            # 白底/浅色穿搭图——也能接上状态栏边界）；照片渐变是彩色缓升，
            # sat 高，仍走严格跃变门槛
            _sat_ok = bool(saturation is not None) and float(
                np.median(saturation[0:top_edge_raw])
            ) <= _RESIDUAL_SAT_MAX
            _jump_min = 0.05 if _sat_ok else _UI_BAND_JUMP_MIN
            _band = diversity[0:top_edge_raw]
            _band_med = float(np.median(_band))
            top_band_valid = (
                float(diversity[top_edge_raw]) - _band_med >= _jump_min
            ) or _ui_band_valid(diversity, 0, top_edge_raw, top_edge_raw)
        else:
            # 无截图证据：一律走严格判据，防止普通照片顶部的纯色天空/暗部
            # 被当作状态栏自动裁剪
            top_band_valid = _ui_band_valid(diversity, 0, top_edge_raw, top_edge_raw)
    if top_band_valid:
        top_edge = _status_bar_correction(diversity, top_edge_raw)
        correction = top_edge != top_edge_raw
        top_frac = top_edge / n
    else:
        top_edge = 0

    residual_top_frac = 0.0
    if top_edge == 0:
        # 顶格残留疑似检测：不并入 top_frac（防误裁普通照片），单独返回建议
        residual_top_frac = round(
            _residual_top_estimate(diversity, saturation, ui_evidence) / n, 6
        )

    # ── 底部地带有效性校验（同上）：非系统 UI 的底部低多样度区域不裁 ──
    bottom_edge = bottom_edge_raw
    bottom_frac = 0.0
    if bottom_edge < n - 1 and _ui_band_valid(
        diversity, bottom_edge + 1, n, bottom_edge
    ):
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
        bottom_frac = (n - 1 - bottom_edge) / n
    else:
        bottom_edge = n - 1

    # 内容区占比下限校验（防灰带过厚/内容区过小误判）
    frac = (bottom_edge - top_edge + 1) / n
    if frac < _CONTENT_FRACTION_MIN:
        raise ValueError(f"内容区占比过小（{frac:.0%}），布局不规则")

    # 单侧最小可裁比例门槛：真实状态栏/导航栏/手势条都有一定高度，
    # 低于门槛的微比例（如 0.8%）是照片边缘噪声，置 0
    if top_frac < _CONTENT_MIN_CROP_FRACTION:
        top_frac = 0.0
    if bottom_frac < _CONTENT_MIN_CROP_FRACTION:
        bottom_frac = 0.0

    # 已裁剪干净：两侧合计可裁比例 <1%。不抛异常、不设内容占比上限——
    # 薄边框截图与残留修正（如顶部状态栏图标残余）都可正常给出裁剪建议，
    # 由调用方按 already_cropped 标注、人工勾选确认兜底
    already_cropped = top_frac + bottom_frac < 0.01

    # ── 底部残留估算（仅 already_cropped 时启用）──
    # 已裁截图的底部播放器条/导航栏常是「半透明暗色叠加」（多样度极低且均
    # 匀、亮度低于内容区），_ui_band_valid 的硬跃变判据对它失效（内容区贴
    # 边行多样度本就低，跃变不足）。already_cropped 先验把「均匀暗带」的
    # 解释空间收窄到残留叠加（照片暗部地面会给出实底带建议走 bottom_frac，
    # 不会进入本分支），误报风险可控；建议不并入 bottom_frac，单独返回供
    # 人工确认（与顶部 residual_top_frac 同语义）。
    residual_bottom_frac = 0.0
    if already_cropped and bottom_edge >= n - 1:
        content_bright = float(
            np.median(brightness[int(n * 0.3) : int(n * 0.7)])
        )
        # 从底部向上找「均匀暗带」：多样度中位 <0.06 且行间 std <0.02
        y = n - 1
        while y >= 0 and diversity[y] < 0.06:
            y -= 1
        band_len = n - 1 - y  # 暗带长度（不含首个内容行）
        if (
            2 <= band_len <= int(n * 0.15)
            and float(np.std(diversity[y + 1 :])) < 0.02
            and float(np.median(brightness[y + 1 :])) < content_bright * 0.92
        ):
            residual_bottom_frac = round(band_len / n, 6)

    # 灰带判定：边界外侧低饱和 + 亮度平坦（边界被校验回退到 0 / n-1 时
    # 视为无该侧地带，不参与灰带判定，避免空区间误判为灰带）
    top_gray_ok = False
    if top_edge > 0:
        top_gray = _band_stats(brightness, saturation, 0, top_edge)
        top_gray_ok = (
            top_gray["sat_mean"] < _SAT_GRAY
            and top_gray["bright_std"] < _GRAY_BAND_STD_MAX
        )
    bot_gray_ok = False
    if bottom_edge < n - 1:
        bot_gray = _band_stats(brightness, saturation, bottom_edge + 1, n)
        bot_gray_ok = (
            bot_gray["sat_mean"] < _SAT_GRAY
            and bot_gray["bright_std"] < _GRAY_BAND_STD_MAX
        )

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
        "residual_bottom_frac": residual_bottom_frac,
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


def _glyph_evidence(img: "Image.Image") -> dict:
    """顶部状态栏字形证据检测（透明叠加状态栏的关键信号，行剖面的盲区补丁）。

    方法：取图片顶部 ``_GLYPH_TOP_FRACTION`` 裁剪并缩放到 ``_GLYPH_ANALYZE_W``
    宽，灰度做「像素 − 5×5 均值」局部对比二值化，连通域分析后按状态栏字形
    的布局签名判定：

    - 字形连通域：高 ≤ 条带 65%、宽 ≤ 图宽 22%（排除大块内容）；
    - 分布：左区（<28% 宽）或右区（>66% 宽）存在字形；
    - 中区（28%~66% 宽）字形像素占比 ≤ 30%（状态栏中间是空的；
      照片/海报的文字横跨中部会被拒绝）；
    - 整体前景占比 ≤ 35%（复杂照片顶部满屏纹理时字形不可分辨，放弃）。

    返回:
        {"found": bool, "strong": bool, "top_frac": float}
        strong = 左右两角字形齐备（高置信，可默认勾选）；top_frac 为建议
        裁剪比例（字形底部 + 余量，占全图高度），未检出时为 0。
    """
    W, H = img.size
    strip_h = max(12, int(H * _GLYPH_TOP_FRACTION))
    strip = img.crop((0, 0, W, strip_h)).resize(
        (_GLYPH_ANALYZE_W, max(6, strip_h * _GLYPH_ANALYZE_W // W)),
        Image.Resampling.LANCZOS,
    )
    arr = np.asarray(strip.convert("L"), dtype=np.float32)
    h, w = arr.shape

    # 5×5 盒均值背景（cumsum 实现，条带很小，开销可忽略）
    pad = np.pad(arr, 2, mode="edge")
    c = np.cumsum(np.cumsum(pad, axis=0), axis=1)
    c = np.pad(c, ((1, 0), (1, 0)))
    box = c[5:, 5:] - c[:-5, 5:] - c[5:, :-5] + c[:-5, :-5]
    blur = box / 25.0
    fg = np.abs(arr - blur) > _GLYPH_CONTRAST
    if fg.mean() > 0.35:
        # 顶部条带满屏高对比纹理：照片内容，字形无法与内容区分
        return {"found": False, "strong": False, "top_frac": 0.0}

    not_found = {"found": False, "strong": False, "top_frac": 0.0}
    try:
        from scipy import ndimage

        fg_d = ndimage.binary_dilation(fg, iterations=1)
        labels, num = ndimage.label(fg_d)
        if num == 0:
            return not_found
        # 状态栏字形只有时间+图标 ≈5~12 个连通域；吊灯/饰品高光碎片类照片
        # 纹理会产出几十个小 blob。超预算不直接放弃（真实截图也可能叠在
        # 复杂内容上），降级为「弱证据」：found=True 但 strong=False，
        # 候选保留、不默认勾选，交人工判断
        glyph_budget = 25
        glyphs = []
        budget_exceeded = False
        kept = 0
        for i, sl in enumerate(ndimage.find_objects(labels), start=1):
            if sl is None:
                continue
            x0, x1 = sl[1].start, sl[1].stop
            y0, y1 = sl[0].start, sl[0].stop
            area = int((labels[sl] == i).sum())
            bw, bh = x1 - x0, y1 - y0
            # 尺寸门槛：状态栏数字/图标在 320 宽坐标系下高 ≥ max(5, 条带 12%)、
            # 宽 ≥ 2px——更小的碎片是照片纹理噪声（真实 FP：拼图内容碎片
            # 3×4px 曾凑成「时间对」误判 strong）
            if area < 4 or bh < max(5, h * 0.12) or bw < 2:
                continue
            # 字形过滤：高 ≤ 条带 65%、宽 ≤ 图宽 22%（大块内容/标题排除）
            if bh > h * 0.65 or bw > w * 0.22:
                continue
            # 紧凑度过滤：线条（门框/相框边、拼图网格线）fill ratio 低
            # （面积/外接框 < 0.3），字形（文字/图标笔画）fill ratio 高
            if area / (bw * bh) < 0.3:
                continue
            if kept >= glyph_budget:
                budget_exceeded = True
                continue
            kept += 1
            glyphs.append((x0, x1, y0, y1, area))
        if not glyphs:
            return not_found
        # 分布签名（真实数据修正两轮）：
        # - 国产系统时间渲染在状态栏中央偏左（26259563 样本），「中区必须空/
        #   仅左右两角」会误拒 → 不做区域限制
        # - 时间签名（strong）：同一 ≤18% 宽度的窗口内存在 ≥2 个「数字状」
        #   blob——高度相近（差<40%）、水平相邻（间隙 <3% 宽）、基线对齐
        #   （垂直中心差 <10% 条带高）。心形装饰（单 blob 孤立）、门框/相框
        #   两侧线段（水平相距远）均不满足；海报大字（横贯大 blob）另被
        #   宽度过滤拒绝
        strong = False
        if budget_exceeded:
            # 弱证据：内容过于杂乱，时间签名不可靠，不默认勾选
            bottom = max(g[3] for g in glyphs)
            top_frac = min(
                _GLYPH_TOP_FRAC_CAP,
                (bottom + _GLYPH_MARGIN_ROWS) / h * _GLYPH_TOP_FRACTION,
            )
            return {"found": True, "strong": False, "top_frac": round(top_frac, 6)}
        gs = sorted(glyphs, key=lambda g: g[0])
        for i in range(len(gs)):
            window = [gs[i]]
            for j in range(i + 1, len(gs)):
                if gs[j][0] - window[-1][1] < w * 0.03:
                    window.append(gs[j])
                else:
                    break
            span = window[-1][1] - window[0][0]
            if len(window) >= 2 and span <= w * 0.18:
                hs = [g[3] - g[2] for g in window]
                cs = [(g[2] + g[3]) / 2 for g in window]
                if max(hs) / max(1, min(hs)) < 1.4 and max(cs) - min(cs) < h * 0.10:
                    strong = True
                    break
        if not strong:
            # 单 blob 回退：小字号渲染下「12:30」会合并为一个块——左上时间位
            # （x 中心 <35% 宽）、宽 4~18%、高 15~60% 条带的紧凑块视为时间块。
            # 手机入镜（镜面自拍，居中）不在左区，不受影响
            for g in glyphs:
                gw, gh = g[1] - g[0], g[3] - g[2]
                gcx = (g[0] + g[1]) / 2
                if (
                    0.04 * w <= gw <= 0.18 * w
                    and 0.15 * h <= gh <= 0.60 * h
                    and gcx < w * 0.35
                ):
                    strong = True
                    break
        bottom = max(g[3] for g in glyphs)
        top_frac = min(
            _GLYPH_TOP_FRAC_CAP,
            (bottom + _GLYPH_MARGIN_ROWS) / h * _GLYPH_TOP_FRACTION,
        )
        return {"found": True, "strong": strong, "top_frac": round(top_frac, 6)}
    except ImportError:
        # 无 scipy：列密度剖面兜底（两角有前景、中部稀疏）
        col = fg.mean(axis=0)
        left_d = col[: int(w * 0.30)].mean()
        mid_d = col[int(w * 0.30) : int(w * 0.66)].mean()
        right_d = col[int(w * 0.66) :].mean()
        if not (left_d > 0.02 or right_d > 0.02) or mid_d > max(left_d, right_d, 0.02) * 0.5:
            return not_found
        rows = fg.mean(axis=1)
        nz = np.nonzero(rows > 0.02)[0]
        if len(nz) == 0:
            return not_found
        top_frac = min(
            _GLYPH_TOP_FRAC_CAP,
            (nz[-1] + _GLYPH_MARGIN_ROWS) / h * _GLYPH_TOP_FRACTION,
        )
        return {"found": True, "strong": False, "top_frac": round(top_frac, 6)}


def analyze_screenshot_combined(path) -> tuple[dict, dict | None]:
    """单次解码完成「截图特征 + 内容边界」检测（扫描候选路径专用）。

    全量扫描时若分别调用 detect_screenshot_features 与 detect_content_bounds，
    每张图要完整解码两次（5000+ 张素材时解码是大头，实测单张 ~60-100ms）。
    本函数一次打开解码，产出 64 宽（特征）与 96 宽（内容边界）两份缩放图，
    把两次解码合并为一次。

    参数:
        path: 图片绝对路径

    返回:
        (features, bounds_or_None)：features 为截图特征字典；bounds 为内容
        边界结果，检测失败（未检出内容区/布局不规则）时为 None（与
        detect_content_bounds 抛 ValueError 语义等价，由调用方统一处理）。
    """
    with Image.open(path) as im:
        img = ImageOps.exif_transpose(im).convert("RGB")
        src_w, src_h = img.size
        small64 = img.resize(
            (_ANALYZE_W, max(16, img.height * _ANALYZE_W // img.width)),
            Image.Resampling.LANCZOS,
        )
        small96 = img.resize(
            (_CONTENT_ANALYZE_W, max(16, img.height * _CONTENT_ANALYZE_W // img.width)),
            Image.Resampling.LANCZOS,
        )
    features = _features_from_small(small64)
    # 字形证据：透明叠加状态栏的独立信号（行剖面盲区补丁），同时用于收紧
    # 残留估算的 UI 证据语义——bottom_bar（底部近纯色行，摄影棚白底/暗部
    # 照片极易命中）不再单独构成放宽残留门槛的证据
    glyph = _glyph_evidence(img)
    ui_evidence = features.get("top_bar", False) or glyph["found"]
    try:
        bounds = _content_bounds_from_small(small96, ui_evidence=ui_evidence)
        bounds["bounds_valid"] = True
    except ValueError:
        bounds = None
    glyph_top = glyph["top_frac"] if glyph["found"] else 0.0
    if bounds is None:
        if glyph["found"]:
            # 内容边界检测失败（整图皆内容、无包夹结构——恰是叠加状态栏最
            # 常见的场景）：只要有字形证据仍返回字形建议，避免「明显状态栏
            # 的截图被静默排除」
            bounds = {
                "top_frac": 0.0,
                "bottom_frac": 0.0,
                "top_edge": 0,
                "bottom_edge": 0,
                "correction": False,
                "kind": "glyph_only",
                "already_cropped": False,
                "residual_top_frac": 0.0,
                "bounds_valid": False,
                "glyph_top_frac": glyph_top,
                "glyph_strong": glyph["strong"],
            }
        # 无字形证据：维持 None（调用方按「检测失败」排除，语义不变）
    else:
        # 字形建议仅在「无实底条带裁剪建议」时使用：实底条带的 top_frac
        # 由行剖面精确定位，比字形底部锚点更准
        bounds["glyph_top_frac"] = 0.0 if bounds["top_frac"] > 0 else glyph_top
        bounds["glyph_strong"] = glyph["strong"]
        # strong 放宽：已确证截图 + 字形存在 + 完整截图比例先验（≥1.5）。
        # 透明状态栏字形常因 JPEG 压缩粘连而不满足严格时间签名
        # （13 张真实漏检样本中 10 张因此漏勾）；历史 FP 全部 ratio<1.5
        if (
            not bounds["glyph_strong"]
            and bounds["already_cropped"]
            and bounds["glyph_top_frac"] > 0
            and src_h >= 1.5 * src_w
        ):
            bounds["glyph_strong"] = True
    return features, bounds
