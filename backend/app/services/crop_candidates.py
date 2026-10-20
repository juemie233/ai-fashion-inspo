"""手机图剪裁的「候选裁决 + 分页游标」纯函数。

从 `crop_service` 拆出：这两个函数都不碰数据库、不读文件，只做纯判定/解析，
是剪裁链路里最容易被单测覆盖、也最可能被其它入口复用的部分。

拆分原则（与项目其它拆分一致）：**行为完全不变**——函数体原样搬移，`crop_service`
继续以同名再导出，既有调用方与测试无需改动。
"""

from __future__ import annotations

import uuid

from app.services.image_cropping import _FULL_SCREENSHOT_RATIO

_STAGE_FULL = "full"
_STAGE_CROPPED = "cropped"


def _content_candidate_qualified(bounds_result: dict, confidence: str, ratio: float) -> bool:
    """content 模式候选资格裁决（FP/FN 裁决层）。

    判定顺序（从严）：

    1. 强字形（左右两角时间/信号签名）：状态栏最本质特征，单独成立
       （不受置信度影响——字形证据本身就是 UI 证据）；
    2. 低置信 + 无任何建议（残留/字形）：大概率普通照片，静默排除；
    3. 完整截图先验 + UI 结构证据：ratio ≥ ``_FULL_SCREENSHOT_RATIO`` 且
       检出字形证据（弱证据也可）或检出灰带/状态栏结构（kind = gray_band /
       status_bar，行剖面「地带 + 硬跃变」）→ 列入。真实库验证：带状态栏的
       抖音截图 ratio ≈ 2.05~2.17 且均有字形证据；用户标注的「上下都没有
       问题」负样本全部 ≤ 1.98。照片即使比例达到 2.0（如 640x1280 竖版照片）
       既无字形也无 UI 结构（kind = plain），不会混入。

    其余一律不列：仅实底条带（照片暗部/灰底极易命中）、仅弱字形、仅残留
    估算——用户标注的 24 张负样本里 3 张仅靠底部暗带、11 张仅靠弱字形误列。

    参数:
        bounds_result: ``analyze_screenshot_combined`` 的边界结果
        confidence: 截图置信度（high/medium/low）
        ratio: 高/宽

    返回:
        是否列入候选
    """
    if bounds_result.get("glyph_strong", False):
        return True
    glyph_found = bounds_result.get("glyph_found", False)
    residual_bottom = bounds_result.get("residual_bottom_frac", 0)
    if (
        confidence == "low"
        and bounds_result["residual_top_frac"] <= 0
        and residual_bottom <= 0
        and not glyph_found
    ):
        return False
    if ratio < _FULL_SCREENSHOT_RATIO:
        return False
    has_ui_band = bounds_result.get("kind") in ("gray_band", "status_bar")
    return bool(glyph_found or has_ui_band)


def _resolve_scan_cursor(cursor: str | None) -> tuple[str, str | None]:
    """解析分页游标，返回 ``(阶段, 素材 id 或 None)``。

    游标格式 ``<阶段>:<素材 id>``（阶段 ∈ full / cropped，id 为空表示该阶段从头开始）；
    兼容旧格式（纯素材 UUID，来自分段前的实现）→ 按「完整截图段」处理，语义等价。

    素材主键为 UUID 字符串，规范化后做字符串比较。不能 int() 转换（曾导致续扫恒 400）；
    与库中存储格式严格一致。
    """
    if cursor is None:
        return _STAGE_FULL, None
    stage, sep, raw = cursor.partition(":")
    if not sep:
        stage, raw = _STAGE_FULL, cursor  # 旧格式：纯 UUID 游标
    if stage not in (_STAGE_FULL, _STAGE_CROPPED):
        raise ValueError(f"分页游标格式无效：{cursor}（应为上次扫描返回的 next_cursor）")
    if not raw:
        return stage, None
    try:
        return stage, str(uuid.UUID(raw))
    except ValueError:
        raise ValueError(f"分页游标格式无效：{cursor}（应为上次扫描返回的 next_cursor）") from None
