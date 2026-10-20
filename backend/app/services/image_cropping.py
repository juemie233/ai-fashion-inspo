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

# 状态栏字形证据检测（含 `_GLYPH_*` 校准常量）已独立成
# ``services/image_cropping_glyph.py``：它与本模块的行剖面证据体系互不依赖，
# 只共享「按 EXIF 校正后的 RGB 图」这一个输入。这里保留同名再导出，本模块内部
# 调用方（``detect_content_bounds`` / ``analyze_screenshot_combined``）、
# ``crop_service``、诊断脚本与测试的引用路径全部不变。
from app.services.image_cropping_glyph import (  # noqa: F401
    _collect_glyph_components,
    _glyph_evidence,
    _glyph_fallback_profile,
    _glyph_strip_features,
    _glyph_time_signature,
    _glyph_top_frac,
    _right_icon_supports,
)

# 行剖面 / 条带 / 残留证据链已独立成 image_cropping_content.py（原样搬移，行为不变）。
# 这里导入回本模块仍在用的常量，并**原样再导出** crop_service / 测试 / 诊断脚本还在引用的
# 助手——它们的 import 路径保持 `app.services.image_cropping` 不变。
from app.services.image_cropping_content import (  # noqa: F401
    _ANALYZE_W,
    _BOTTOM_SCAN_FRACTION,
    _CONTENT_ANALYZE_W,
    _FULL_SCREENSHOT_RATIO,
    _ROW_CONTENT,
    _ROW_STATUS_BAR,
    _ROW_UNIFORM,
    _TOP_SCAN_FRACTION,
    _band_stats,
    _content_bounds,
    _content_bounds_from_small,
    _residual_top_estimate,
    _row_diversity,
    _status_bar_correction,
    _ui_band_valid,
)

# 手动裁剪最小保留高度（自然像素）：与前端 Cropper.js minCropBoxHeight=50
# 的口径对齐，避免确认出极窄（<50px）的裁剪结果
MIN_MANUAL_CROP_HEIGHT_PX = 50

# ── 黑边自动检测参数 ──
BRIGHT_PIXEL_THRESHOLD = 25  # 灰度值 > 25 视为「亮像素」
CONTENT_ROW_FRACTION = 0.005  # 一行中亮像素占比 > 0.5% 视为内容行
MIN_MAIN_BAND_HEIGHT_FRACTION = 0.25  # 主体条带高度至少占全图 25%
MAX_OTHER_BANDS_FRACTION = 0.5  # 其他条带总高度不超过主体的 50%

# ── 手机截图特征检测参数 ──
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
        result["glyph_found"] = glyph["found"]
        result["bounds_valid"] = True
        return result


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
                "glyph_found": True,
            }
        # 无字形证据：维持 None（调用方按「检测失败」排除，语义不变）
    else:
        # 字形建议仅在「无实底条带裁剪建议」时使用：实底条带的 top_frac
        # 由行剖面精确定位，比字形底部锚点更准
        bounds["glyph_top_frac"] = 0.0 if bounds["top_frac"] > 0 else glyph_top
        # strong 只认时间签名判定本身。历史教训：曾按「already_cropped +
        # ratio≥1.5」放宽，但 already 只表示「行剖面无实底带」而非「确证
        # 截图」，普通照片大量满足该组合（20 张真实 FP 的根因），已回退。
        bounds["glyph_strong"] = glyph["strong"]
        # found（弱证据也计）：非完整截图（ratio < 完整截图先验）的候选资格
        # 要求「实底条带 + 字形交叉印证」，实底带单独在照片暗部/灰底上极易
        # 误命中（用户标注 24 张负样本中 3 张仅靠底部暗带误列）
        bounds["glyph_found"] = glyph["found"]
    return features, bounds
