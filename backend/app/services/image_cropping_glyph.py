"""顶部状态栏字形证据检测（glyph）：行多样度剖面的原理性盲区补丁。

为什么单独成模块：这段算法与 `image_cropping.py` 的行剖面/条带判定是两套
**互相独立**的证据体系，只共享「按 EXIF 校正后的 RGB 图」这一个输入，内部
不引用后者的任何函数或常量（从 `image_cropping.py` 原样抽出，行为不变）。
抽出的收益是 `image_cropping.py` 少 380 余行，字形参数的校准与回归可以只
盯这一个文件。

为什么需要它（`image_cropping.py` 行剖面的两个盲区）：

- **透明叠加状态栏**（图标直接叠在照片上）没有「低多样度条带」，行剖面与照片
  顶部自然低多样度区域同构 → 必然漏检；
- **影棚纯色背景照片**（顶部纯色带 + 内容抬升）反向误报 → 必然误勾。

两者的共同解法是状态栏唯一稳定的结构特征——**字形布局**：时间（左上）+
信号/电量（右上）是「小、孤立、高对比、集中两角、中间空」的连通域，与主题
无关、与底图无关。

实现要点：取图片顶部 ``_GLYPH_TOP_FRACTION`` 缩放到 ``_GLYPH_ANALYZE_W`` 宽，
灰度做「像素 − 5×5 均值」局部对比二值化，连通域分析后按布局签名判定。
判定阈值全部来自真实标注样本校准（见各常量注释），改动前先跑
``backend/tests/test_crop_service.py`` 里的字形用例。

设计约束：与 ``image_cropping`` 一样是**纯函数**模块（只依赖 PIL/numpy），
可脱离 FastAPI/DB 用合成图直测。
"""

import numpy as np
from PIL import Image

# ── 顶部状态栏字形证据检测（glyph）──
# 行多样度剖面的原理性盲区：透明叠加状态栏（图标直接叠在照片上）没有「低多
# 样度条带」，行剖面与照片顶部自然低多样度区域同构；而影棚纯色背景照片的反
# 向误报（顶部纯色带 + 内容抬升被当状态栏）也无法靠行剖面排除。两者的共同
# 解法是状态栏唯一稳定的结构特征——字形布局：时间（左上）+ 信号/电量（右上）
# 是「小、孤立、高对比、集中两角、中间空」的连通域，与主题无关、与底图无关。
_GLYPH_ANALYZE_W = 320  # 字形分析宽度（96 宽下状态栏字形仅数像素，分辨率不足）
_GLYPH_TOP_FRACTION = 0.10  # 只分析图片顶部 10%（状态栏 + 少量余量）
_GLYPH_CONTRAST = 22.0  # 局部对比二值化阈值（像素 − 5×5 均值，0~255）
# 顶部条带饱和度上限：真实状态栏/透明残留饱和 ≤0.12，>0.15 为彩色照片顶部
# （天空/头发/水印等）——高饱和不是状态栏，直接拒绝字形证据（批量误报修正）
_GLYPH_MAX_SATURATION = 0.15
_GLYPH_MARGIN_ROWS = 2  # 字形底部再多裁的行数（缩放坐标系，覆盖图标抗锯齿边）
_GLYPH_TOP_FRAC_CAP = 0.12  # 字形路径建议裁剪比例上限（状态栏不会超过全高 12%）
# 字形高度上限（占条带高）：状态栏字号约全高 1.6%~2%，在顶部 10% 条带里约
# 17%~25%（实测真实截图 12/65=18%、15/65=23%）。照片顶部的大块内容/大字
# 往往占条带 50% 以上（实测误报样本 24/48、25/42），超过此上限不是状态栏字形
_GLYPH_MAX_HEIGHT_FRAC = 0.40
# 字形上沿最小位置（占条带高）：状态栏文字垂直居中于状态栏（约全高 4%），
# 在顶部 10% 条带里上沿落在 15%~25% 处（实测真实截图 16/65、17/69）。贴图片
# 最顶行的连通域是照片内容（人物/背景压到画面顶端），不是状态栏字形。
_GLYPH_MIN_TOP_FRAC = 0.08
# 字形带上方的前景密度上限：状态栏文字之上是状态栏自身背景（纯色/半透明），
# 实测真实截图 4 张全为 0；照片在字形「上方」永远还有内容（实测误报
# 0.034~0.142）。这是区分「状态栏文字」与「照片纹理块」最直接的结构信号。
_GLYPH_ABOVE_FG_MAX = 0.01
# 字形周边背景灰度标准差上限：状态栏文字印在平滑底上（实测真实截图 0.5~6.9），
# 照片边缘（发丝/饰品/衣料轮廓）周边是纹理（实测误报 15~89）。这是区分
# 「印刷体文字」与「照片边缘」最本质的信号，只用于强字形判定，不剔除连通域。
_GLYPH_BG_STD_MAX = 12.0
# 单块时间回退的附加条件：时间块垂直中心须落在条带上半部（实测真实截图
# 36%，照片内容块常在 52% 以上），且右区存在同带的低背景方差图标块
# （状态栏右侧信号/电量）——只有左侧一个孤立块不构成状态栏证据。
# 时间签名的字形整体宽度下限：真实「12:30」两段式字形总宽 ≈10% 图宽，
# 照片纹理上凑出的「相邻小碎片」（总宽 <6%）不算时间签名。
_GLYPH_MAX_CENTER_Y = 0.45
_GLYPH_MIN_SPAN_FRAC = 0.06
_GLYPH_RIGHT_ICON_X = 0.70
_GLYPH_TIME_X_MAX = 0.42  # 单块时间的中心 x 上限（时间渲染在中央偏左，实测 0.34~0.38）


def _right_icon_supports(g: tuple, glyphs: list[tuple], w: int) -> bool:
    """单块时间回退的右侧印证：右区是否存在同带、低背景方差的图标块。

    真实状态栏是「时间（左）+ 信号/电量（右）」的成对结构，只有一个左侧
    孤立块时不能判定为状态栏（真实 FP：照片左侧的装饰/发丝块）。

    参数:
        g: 候选时间块 (x0, x1, y0, y1, area, bg_std)
        glyphs: 全部通过过滤的连通域
        w: 条带宽度（缩放坐标系）

    返回:
        右区是否存在垂直同带（重叠 ≥ 较矮一方一半）且背景平滑的块
    """
    for o in glyphs:
        if o is g:
            continue
        if (o[0] + o[1]) / 2 <= w * _GLYPH_RIGHT_ICON_X:
            continue
        if o[5] > _GLYPH_BG_STD_MAX:
            continue
        overlap = min(g[3], o[3]) - max(g[2], o[2])
        if overlap >= 0.5 * min(g[3] - g[2], o[3] - o[2]):
            return True
    return False


def _glyph_top_frac(bottom_row: int, h: int) -> float:
    """字形底行 → 建议裁剪比例（占全图高度，带上限 ``_GLYPH_TOP_FRAC_CAP``）。

    该公式原在 :func:`_glyph_evidence` 里重复三次（弱证据 / 正常返回 /
    无 scipy 兜底），收敛到一处避免改余量或上限时漏改。
    """
    return min(_GLYPH_TOP_FRAC_CAP, (bottom_row + _GLYPH_MARGIN_ROWS) / h * _GLYPH_TOP_FRACTION)


def _glyph_time_signature(
    glyphs: list[tuple[int, int, int, int, int, float]], fg: "np.ndarray", h: int, w: int
) -> tuple[bool, str]:
    """时间签名判定（strong）：返回 (是否强证据, 说明文案)。

    两种成立路径：

    1. **多 blob 时间签名**：同一 ≤18% 宽度的窗口内存在 ≥2 个「数字状」blob——
       高度相近（差 <40%）、水平相邻（间隙 <3% 宽）、基线对齐（垂直中心差
       <10% 条带高），且位于左右两角（中央 x 占比 <0.40 或 >0.60）、块上方
       无前景、周边背景平滑。心形装饰（单 blob 孤立）、门框/相框两侧线段
       （水平相距远）、海报大字（横贯大 blob，另被宽度过滤拒绝）均不满足。
    2. **单块回退**：小字号渲染下「12:30」合并成一块——左上时间位（x 中心
       <35% 宽）、宽 4%~18%、高 15%~上限的紧凑块，附加两道印证：垂直中心落在
       条带上半部（真实时间块 36%，照片内容块常在 80%）、右区存在同带低背景
       方差的图标块（信号/电量）——左侧孤立一块不构成状态栏。
    """
    strong = False
    strong_note = ""
    gs = sorted(glyphs, key=lambda g: g[0])
    for i in range(len(gs)):
        window = [gs[i]]
        for j in range(i + 1, len(gs)):
            gap = gs[j][0] - window[-1][1]
            # 相邻字形必须水平「不重叠且紧邻」：状态栏「12:30」的每个数字
            # 各自成块、块间有正间隙（实测间隙 1px）。水平重叠的两个 blob
            # 是同一内容块被局部对比二值化切碎（照片纹理常见），不是时间签名
            if 0 <= gap < w * 0.03:
                window.append(gs[j])
            elif gap < 0:
                continue
            else:
                break
        span = window[-1][1] - window[0][0]
        if len(window) >= 2 and w * _GLYPH_MIN_SPAN_FRAC <= span <= w * 0.18:
            # 区域限制（误报修正）：状态栏元素只在左右两角——时间在左区、
            # 信号/电量在右区，中央是空的。画面中央出现「两个相邻数字状
            # blob」是照片内容（水印/花纹/文字），不是状态栏。
            # 真实素材诊断：误报样本 efef31ba 的 strong 来自中央
            # （中心 x 占比 0.48/0.50）的两个 blob，属照片内容误判。
            wc = (window[0][0] + window[-1][1]) / 2.0 / w
            if not (wc < 0.40 or wc > 0.60):
                continue
            hs = [g[3] - g[2] for g in window]
            cs = [(g[2] + g[3]) / 2 for g in window]
            band_top = min(g[2] for g in window)
            above_fg = float(fg[:band_top].mean()) if band_top > 0 else 0.0
            if (
                max(hs) / max(1, min(hs)) < 1.4
                and max(cs) - min(cs) < h * 0.10
                and max(cs) <= h * _GLYPH_MAX_CENTER_Y
                and max(hs) <= h * _GLYPH_MAX_HEIGHT_FRAC
                and above_fg <= _GLYPH_ABOVE_FG_MAX
                and all(g[5] <= _GLYPH_BG_STD_MAX for g in window)
            ):
                strong = True
                strong_note = f"时间签名：{len(window)} 个相邻字形（中心 x 占比 {wc:.2f}）"
                break
    if not strong:
        # 单 blob 回退：小字号渲染下「12:30」会合并为一个块——左上时间位
        # （x 中心 <35% 宽）、宽 4~18%、高 15%~上限的紧凑块视为时间块。
        # 手机入镜（镜面自拍，居中）不在左区，不受影响。
        # 附加两道印证（误报修正）：① 垂直中心落在条带上半部（真实
        # 时间块 36%，照片内容块常在 80%）；② 右区存在同带、低背景方差
        # 的图标块（状态栏右侧信号/电量）——左侧孤立一块不构成状态栏。
        for g in glyphs:
            gw, gh = g[1] - g[0], g[3] - g[2]
            gcx = (g[0] + g[1]) / 2
            above_fg = float(fg[: g[2]].mean()) if g[2] > 0 else 0.0
            if (
                0.04 * w <= gw <= 0.18 * w
                and 0.15 * h <= gh <= _GLYPH_MAX_HEIGHT_FRAC * h
                and gcx < w * _GLYPH_TIME_X_MAX
                and g[5] <= _GLYPH_BG_STD_MAX
                and (g[2] + g[3]) / 2 <= h * _GLYPH_MAX_CENTER_Y
                and above_fg <= _GLYPH_ABOVE_FG_MAX
                and _right_icon_supports(g, glyphs, w)
            ):
                strong = True
                strong_note = f"单块时间回退（中心 x 占比 {gcx / w:.2f}）"
                break
    return strong, strong_note


def _glyph_strip_features(img: "Image.Image") -> tuple:
    """取顶部条带并算出字形检测所需的灰度/前景/饱和度：

    返回 (灰度 arr, 前景掩码 fg, 前景占比 fg_ratio, 条带饱和度 strip_sat, h, w) ——
    h/w 是条带在分析宽度下的像素尺寸（不是原图尺寸）。

    前景 = 「像素 − 5×5 均值」的局部对比二值化结果（cumsum 实现盒均值，
    条带很小、开销可忽略）。饱和度单独取出用于拒绝彩色照片顶部。
    """
    W, H = img.size
    strip_h = max(12, int(H * _GLYPH_TOP_FRACTION))
    strip = img.crop((0, 0, W, strip_h)).resize(
        (_GLYPH_ANALYZE_W, max(6, strip_h * _GLYPH_ANALYZE_W // W)),
        Image.Resampling.LANCZOS,
    )
    arr = np.asarray(strip.convert("L"), dtype=np.float32)
    h, w = arr.shape

    # 饱和度门槛（误报修正）：真实状态栏 / 透明残留的顶部条带饱和度极低
    # （实测 ≤0.12），而彩色照片顶部（天空/头发/衣服/水印）饱和度常 >0.15——
    # 高饱和条带几乎不可能是状态栏，直接用饱和度拒绝，消灭「彩色照片顶被
    # 判残留」的批量误报（真实素材诊断：8 个误报样本 5 个饱和 0.17~0.45）。
    hsv = np.asarray(strip.convert("HSV"), dtype=np.float32)
    strip_sat = float(hsv[..., 1].mean()) / 255.0

    # 5×5 盒均值背景（cumsum 实现，条带很小，开销可忽略）
    pad = np.pad(arr, 2, mode="edge")
    c = np.cumsum(np.cumsum(pad, axis=0), axis=1)
    c = np.pad(c, ((1, 0), (1, 0)))
    box = c[5:, 5:] - c[:-5, 5:] - c[5:, :-5] + c[:-5, :-5]
    blur = box / 25.0
    fg = np.abs(arr - blur) > _GLYPH_CONTRAST
    fg_ratio = float(fg.mean())
    return arr, fg, fg_ratio, strip_sat, h, w


def _collect_glyph_components(fg: "np.ndarray", h: int, w: int, reject, bg_std) -> tuple:
    """连通域分析 + 逐块过滤，返回 (字形块列表, 是否超预算, 连通域总数)。

    过滤口径（顺序即从严顺序，被剔除的块交给 reject 记录调试信息）：
    尺寸下限（面积/高/宽）→ 贴条带下沿/上沿 → 高宽上限 → 紧凑度（线条与
    网格 fill ratio 低）→ 连通域预算（超出即降级为弱证据，不默认勾选）。

    返回的连通域总数为 0 表示「无连通域」，与「有连通域但全被过滤」是两种
    不同情况（调用方给不同提示）。
    """
    glyphs: list[tuple[int, int, int, int, int, float]] = []

    from scipy import ndimage

    fg_d = ndimage.binary_dilation(fg, iterations=1)
    labels, num = ndimage.label(fg_d)
    # 状态栏字形只有时间+图标 ≈5~12 个连通域；吊灯/饰品高光碎片类照片
    # 纹理会产出几十个小 blob。超预算不直接放弃（真实截图也可能叠在
    # 复杂内容上），降级为「弱证据」：found=True 但 strong=False，
    # 候选保留、不默认勾选，交人工判断
    glyph_budget = 25
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
            reject(x0, x1, y0, y1, area, "过小（面积/高/宽）")
            continue
        # 下沿截断剔除（误报修正）：照片内容在条带下沿被「切一刀」，其
        # 连通域必然延伸到条带最后一行；真实状态栏字形完整落在条带内，
        # 底部留有大量余量（实测真实截图字形底 ≈ 条带 45%，误报样本
        # 全部贴到条带下沿）。贴边 blob 是照片内容，不是状态栏字形。
        if y1 >= h - 1:
            reject(x0, x1, y0, y1, area, "贴条带下沿（内容被截断）")
            continue
        # 上沿剔除（误报修正）：状态栏文字不会压在图片最顶端（垂直居中于
        # 状态栏，上沿实测在条带 23%~26% 处），贴最顶行的连通域是照片
        # 内容压到画面顶端（真实 FP：发丝/背景块 y0=0）。
        if y0 < h * _GLYPH_MIN_TOP_FRAC:
            reject(x0, x1, y0, y1, area, "贴条带上沿（照片内容顶到画面顶）")
            continue
        # 字形过滤：高 ≤ 条带 65%、宽 ≤ 图宽 22%（大块内容/标题排除）
        if bh > h * 0.65 or bw > w * 0.22:
            reject(x0, x1, y0, y1, area, "过大（高/宽超限）")
            continue
        # 紧凑度过滤：线条（门框/相框边、拼图网格线）fill ratio 低
        # （面积/外接框 < 0.3），字形（文字/图标笔画）fill ratio 高
        if area / (bw * bh) < 0.3:
            reject(x0, x1, y0, y1, area, "紧凑度不足（线条/网格）")
            continue
        if kept >= glyph_budget:
            budget_exceeded = True
            reject(x0, x1, y0, y1, area, "超连通域预算")
            continue
        kept += 1
        glyphs.append((x0, x1, y0, y1, area, bg_std(x0, x1, y0, y1)[0]))

    return glyphs, budget_exceeded, num


def _glyph_fallback_profile(fg: "np.ndarray", h: int, w: int) -> tuple:
    """无 scipy 时的兜底：列密度剖面（两角有前景、中部稀疏）。

    返回 (found, top_frac, note)；判不出前景时为 (False, 0.0, 原因)。
    精度低于连通域路径，故一律按弱证据返回（调用方传 strong=False）。
    """
    col = fg.mean(axis=0)
    left_d = col[: int(w * 0.30)].mean()
    mid_d = col[int(w * 0.30) : int(w * 0.66)].mean()
    right_d = col[int(w * 0.66) :].mean()
    if not (left_d > 0.02 or right_d > 0.02) or mid_d > max(left_d, right_d, 0.02) * 0.5:
        return False, 0.0, "无 scipy：列剖面不满足"
    rows = fg.mean(axis=1)
    nz = np.nonzero(rows > 0.02)[0]
    if len(nz) == 0:
        return False, 0.0, "无 scipy：无前景行"
    top_frac = _glyph_top_frac(int(nz[-1]), h)
    return True, top_frac, "无 scipy：列剖面兜底"


def _glyph_evidence(img: "Image.Image", debug: bool = False) -> dict:
    """顶部状态栏字形证据检测（透明叠加状态栏的关键信号，行剖面的盲区补丁）。

    方法：取图片顶部 ``_GLYPH_TOP_FRACTION`` 裁剪并缩放到 ``_GLYPH_ANALYZE_W``
    宽，灰度做「像素 − 5×5 均值」局部对比二值化，连通域分析后按状态栏字形
    的布局签名判定：

    - 字形连通域：高 ≤ 条带 65%、宽 ≤ 图宽 22%（排除大块内容）；
    - 贴条带下沿（最后一行）的连通域剔除——照片内容被条带切一刀必然贴边，
      真实状态栏字形完整落在条带内（底部余量约 55% 条带高）；
    - 贴上沿（条带前 8%）的连通域剔除——状态栏文字垂直居中于状态栏，上沿
      实测在条带 23%~26% 处，压在最顶行的是照片内容；
    - 分布：左区（<28% 宽）或右区（>66% 宽）存在字形；
    - 中区（28%~66% 宽）字形像素占比 ≤ 30%（状态栏中间是空的；
      照片/海报的文字横跨中部会被拒绝）；
    - 整体前景占比 ≤ 35%（复杂照片顶部满屏纹理时字形不可分辨，放弃）。
    - 时间签名另要求：相邻字形水平不重叠（重叠是同一内容块被切碎）、
      字形高 ≤ 条带 40%（状态栏字号实测占条带 17%~25%，照片大字 50%+）、
      字形垂直中心 ≤ 条带 45%（状态栏文字居中于顶部状态栏，实测 33%~36%；
      照片内容块垂直中心常在 52% 以上，即更靠下）、
      字形整体宽度 ≥ 图宽 6%（真实「12:30」≈10%）、
      字形带上方前景密度 ≤ 1%（状态栏文字之上是状态栏自身背景，实测真实
      截图全为 0；照片在字形上方永远还有内容）、
      字形周边背景灰度标准差 ≤ 12（印刷体文字印在平滑底上，照片边缘周边
      是纹理——这是区分「文字」与「照片边缘」最本质的信号）。
    - 单块时间回退（小字号渲染下「12:30」合并成一块）另要求：块中心 x ≤ 图宽
      42%、垂直中心 ≤ 条带 45%、带上方无前景、背景平滑，且右区存在同带平滑
      图标块。

    参数:
        img: 已按 EXIF 校正的 RGB 图
        debug: 为 True 时在返回中附带 ``debug`` 字段（条带尺寸/饱和度/前景
            占比、保留与剔除的连通域及剔除原因），供诊断脚本复现判定过程，
            不影响判定本身

    返回:
        {"found": bool, "strong": bool, "top_frac": float}
        strong = 左右两角字形齐备（高置信，可默认勾选）；top_frac 为建议
        裁剪比例（字形底部 + 余量，占全图高度），未检出时为 0。
    """
    arr, fg, fg_ratio, strip_sat, h, w = _glyph_strip_features(img)

    glyphs: list[tuple[int, int, int, int, int, float]] = []
    rejected: list[dict] = []  # 调试：被剔除的连通域及原因

    def _result(found: bool, strong: bool, top_frac: float, note: str = "") -> dict:
        """构造返回字典；debug 模式附带判定过程明细。"""
        out = {"found": found, "strong": strong, "top_frac": round(top_frac, 6)}
        if debug:
            band_top = min((g[2] for g in glyphs), default=0)
            out["debug"] = {
                "strip_h": h,
                "strip_w": w,
                "strip_sat": round(strip_sat, 4),
                "fg_ratio": round(fg_ratio, 4),
                "fg_above": round(float(fg[:band_top].mean()) if band_top > 0 else 0.0, 4),
                "note": note,
                "glyphs": [
                    {
                        "x0": g[0],
                        "x1": g[1],
                        "y0": g[2],
                        "y1": g[3],
                        "area": g[4],
                        "bg_std": g[5],
                    }
                    for g in glyphs
                ],
                "rejected": rejected,
            }
        return out

    def _bg_stats(x0: int, x1: int, y0: int, y1: int, pad: int = 4) -> tuple[float, int]:
        """统计连通域「外扩一圈」的背景灰度标准差（调试用）。

        真实状态栏字形印在平滑背景上（系统栏底色/半透明叠加），字形周围
        灰度方差极小；照片边缘（发丝/饰品/衣料轮廓）周围是纹理，方差大。
        """
        ry0, ry1 = max(0, y0 - pad), min(h, y1 + pad)
        rx0, rx1 = max(0, x0 - pad), min(w, x1 + pad)
        region = arr[ry0:ry1, rx0:rx1]
        mask = fg[ry0:ry1, rx0:rx1].copy()
        mask[y0 - ry0 : y1 - ry0, x0 - rx0 : x1 - rx0] = True  # 排除字形自身
        bg = region[~mask]
        if bg.size < 8:
            return 0.0, int(bg.size)
        return round(float(bg.std()), 2), int(bg.size)

    def _reject(x0, x1, y0, y1, area, reason: str) -> None:
        """记录被剔除的连通域（仅调试用）。"""
        if debug:
            bg_std, bg_n = _bg_stats(x0, x1, y0, y1)
            rejected.append(
                {
                    "x0": x0,
                    "x1": x1,
                    "y0": y0,
                    "y1": y1,
                    "area": area,
                    "bg_std": bg_std,
                    "bg_n": bg_n,
                    "reason": reason,
                }
            )

    if strip_sat > _GLYPH_MAX_SATURATION:
        return _result(False, False, 0.0, f"条带饱和 {strip_sat:.3f} > 上限")
    if fg_ratio > 0.35:
        # 顶部条带满屏高对比纹理：照片内容，字形无法与内容区分
        return _result(False, False, 0.0, f"前景占比 {fg_ratio:.3f} > 0.35")

    try:
        # scipy 缺失时，由 _collect_glyph_components 内部的 import 抛 ImportError，
        # 落到下面的无 scipy 兜底分支
        glyphs, budget_exceeded, num = _collect_glyph_components(
            fg, h, w, _reject, _bg_stats
        )
        if num == 0:
            return _result(False, False, 0.0, "无连通域")
        if not glyphs:
            return _result(False, False, 0.0, "无通过过滤的连通域")
        # 分布签名（真实数据修正两轮）：
        # - 国产系统时间渲染在状态栏中央偏左（26259563 样本），「中区必须空/
        #   仅左右两角」会误拒 → 不做区域限制
        # - 时间签名（strong）：同一 ≤18% 宽度的窗口内存在 ≥2 个「数字状」
        #   blob——高度相近（差<40%）、水平相邻（间隙 <3% 宽）、基线对齐
        #   （垂直中心差 <10% 条带高）。心形装饰（单 blob 孤立）、门框/相框
        #   两侧线段（水平相距远）均不满足；海报大字（横贯大 blob）另被
        #   宽度过滤拒绝
        if budget_exceeded:
            # 弱证据：内容过于杂乱，时间签名不可靠，不默认勾选
            top_frac = _glyph_top_frac(max(g[3] for g in glyphs), h)
            return _result(True, False, top_frac, "超连通域预算 → 弱证据")
        strong, strong_note = _glyph_time_signature(glyphs, fg, h, w)
        top_frac = _glyph_top_frac(max(g[3] for g in glyphs), h)
        return _result(True, strong, top_frac, strong_note)
    except ImportError:
        # 无 scipy：列密度剖面兜底（两角有前景、中部稀疏）
        found, top_frac, note = _glyph_fallback_profile(fg, h, w)
        return _result(found, False, top_frac, note)
