"""质量审核「自由文本原因」→「结构化原因码」的归类规则。

背景：`quality_reason_code` 是 2026-10 才加的字段，之前 881 条历史 rejected 素材只有自由文本
`quality_reason`，导致「按原因筛选 / 灰区复审 / prompt A/B」三项功能没有数据可看。
这里把归类规则收敛成**纯函数**（可单测、可被回填脚本复用），规则按「越具体越先匹配」排序。

为什么不直接用模型重新判一遍：历史判定是旧 prompt 给的结论，重跑会引入新变量的干扰；
回填只做「把已有文本翻译成枚举」，不改变任何判定本身。
"""

from __future__ import annotations

# (原因码, 关键词元组)。按顺序匹配，命中即返回。
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # 初筛器直接拒绝：文本里带置信度说明
    ("prefilter_reject", ("初筛器",)),
    # 文件/图像层面的硬性不可判
    ("low_quality", ("文件缺失", "不存在", "无法读取", "损坏", "模糊", "低清", "太小", "纯色")),
    # 营销/非人像内容
    ("ad", ("广告", "文字", "截图", "尺码表", "二维码", "水印")),
    # 只有单品、没有穿在身上
    ("single_item", ("单品", "鞋的特写", "包的展示")),
    # 明确不是穿搭（放在 partial 之前：「无人物」比「不完整」更决定性）
    ("not_outfit", ("不是穿搭", "非穿搭", "无人物", "平铺", "不是人", "没有人", "无真人")),
    # 人像/穿搭被裁掉一部分。
    # 历史文本的主流说法是「无法看清完整穿搭」「仅展示上半身」，实测占 rejected 的六成，
    # 所以要显式覆盖这些表述，否则大批量落到 other（回填后筛选形同虚设）。
    (
        "partial",
        (
            "裁切", "局部", "特写", "只有腿", "只有脚", "只有手臂", "领口",
            "不完整", "无法看清", "无法判断整体", "未呈现完整", "仅展示", "上半身", "下半身",
            "部分下装", "部分上衣", "未展示", "未显示", "仅穿着",
        ),
    ),
)

FALLBACK_CODE = "other"
"""无法归类时的原因码（不猜成 not_outfit，避免污染「不是穿搭」的统计）。"""


def classify_quality_reason(text: str | None) -> str:
    """把自由文本的拒绝原因归类为 `quality_reason_code` 枚举值。

    Args:
        text: 历史 `quality_reason` 文本（可为空）

    Returns:
        原因码：命中规则则返回对应码，否则返回 ``other``。

    Examples:
        >>> classify_quality_reason("仅单品特写，没有整体搭配")
        'single_item'
        >>> classify_quality_reason(None)
        'other'
    """
    if not text:
        return FALLBACK_CODE
    lowered = text.strip().lower()
    for code, keywords in _RULES:
        if any(word in lowered for word in keywords):
            return code
    return FALLBACK_CODE
