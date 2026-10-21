"""从素材反推 prompt：把库里已有的标签/正文/主色拼成可编辑的出图描述。

为什么值得做（相对裸 ComfyUI 的增量）：用户已经在 2.6 万素材上积累了 1.3 万个标签、
正文描述与主色，这些正是「描述一套穿搭」最费劲的部分。反推出来的 prompt 不是魔法，
但能省掉从零手打。

设计取舍（一期）：

- **不接 LLM 扩写**：多一跳 Ollama 调用既慢（与出图抢显存）又难测失败路径；
- 输出**带分段**（``parts``），每段标明来源（哪个标签/字段）—— 前端逐段删改，
  比"一整坨字符串"可控得多；
- 纯函数、无 I/O 之外的副作用，便于单测。
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inspiration import Inspiration
from app.models.tag import InspirationTag, Tag

logger = logging.getLogger(__name__)

"""标签类别 → 在 prompt 里的位置顺序（越靠前越先出现）。"""
_CATEGORY_ORDER = (
    "item",     # 单品
    "style",    # 风格
    "color",    # 颜色
    "scene",    # 场景
    "material", # 材质
    "other",
)

"""画质词：固定后缀，避免用户每次手打。"""
QUALITY_SUFFIX = "editorial fashion photography, soft natural light, sharp focus, film grain, 4k detail"

"""主色十六进制 → 中文色名（覆盖常见档位即可，未命中时回退原 hex）。"""
_COLOR_NAMES = {
    "000000": "黑色", "ffffff": "白色", "f5f5dc": "米色", "d2b48c": "卡其",
    "8b4513": "棕色", "808080": "灰色", "c0c0c0": "浅灰", "ff0000": "红色",
    "0000ff": "蓝色", "008000": "绿色", "ffff00": "黄色", "800080": "紫色",
    "ffc0cb": "粉色", "ffa500": "橙色", "e6e6fa": "淡紫", "f0e68c": "浅黄",
}


def _color_label(hex_code: str) -> str:
    """十六进制主色 → 中文色名（未登记时保留原值，至少不丢信息）。"""
    code = hex_code.lower().strip().lstrip("#")
    if len(code) != 6:
        return hex_code
    if code in _COLOR_NAMES:
        return _COLOR_NAMES[code]
    # 退化到粗粒度判断：很亮→浅色，很暗→深色
    try:
        r, g, b = int(code[0:2], 16), int(code[2:4], 16), int(code[4:6], 16)
    except ValueError:
        return hex_code
    if r > 220 and g > 220 and b > 220:
        return "白色"
    if r < 40 and g < 40 and b < 40:
        return "黑色"
    return f"#{code}"


async def build_prompt(db: AsyncSession, inspiration_ids: list[str]) -> dict:
    """根据 1~N 个素材反推 prompt。

    Args:
        inspiration_ids: 素材 id 列表（顺序即权重顺序：前面的素材先出现）。

    Returns:
        ``{"prompt": str, "parts": [{"text": str, "source": str}]}``
        —— parts 供前端逐段删改；prompt 是拼好的完整串。
    """
    if not inspiration_ids:
        return {"prompt": QUALITY_SUFFIX, "parts": [{"text": QUALITY_SUFFIX, "source": "画质词"}]}

    # 1) 标签：按类别分组，取每类置信度最高的若干个（一次查询，避免 N+1）
    tag_rows = await db.execute(
        select(Tag.category, Tag.name, func.max(InspirationTag.confidence))
        .join(InspirationTag, InspirationTag.tag_id == Tag.id)
        .where(InspirationTag.inspiration_id.in_(inspiration_ids))
        .group_by(Tag.gender, Tag.category, Tag.name)
        .order_by(func.max(InspirationTag.confidence).desc())
        .limit(40)
    )
    by_category: dict[str, list[str]] = {}
    for category, name, _confidence in tag_rows.all():
        cat = (category or "other").lower()
        bucket = by_category.setdefault(cat, [])
        if name not in bucket:
            bucket.append(name)

    parts: list[dict] = []
    for cat in _CATEGORY_ORDER:
        names = by_category.get(cat) or []
        if not names:
            continue
        # 每类最多 6 个：prompt 太长反而降低可控性
        text = "、".join(names[:6])
        parts.append({"text": text, "source": f"标签·{cat}"})
    # 未归类的类别（未来新增类别）兜底追加
    for cat, names in by_category.items():
        if cat in _CATEGORY_ORDER:
            continue
        parts.append({"text": "、".join(names[:4]), "source": f"标签·{cat}"})

    # 2) 主色：取第一个素材的前 3 个色
    color_row = await db.execute(
        select(Inspiration.dominant_colors, Inspiration.caption).where(
            Inspiration.id.in_(inspiration_ids)
        )
    )
    colors: list[str] = []
    captions: list[str] = []
    for dominant, caption in color_row.all():
        if caption:
            captions.append(caption.strip()[:60])
        if dominant and not colors:
            try:
                parsed = json.loads(dominant)
            except json.JSONDecodeError:
                parsed = []
            colors = [_color_label(str(c)) for c in parsed[:3] if c]
    if colors:
        parts.append({"text": "配色：" + "、".join(colors), "source": "主色"})
    if captions:
        parts.append({"text": "；".join(captions[:2]), "source": "正文"})

    parts.append({"text": QUALITY_SUFFIX, "source": "画质词"})

    prompt = "，".join(p["text"] for p in parts if p["text"])
    return {"prompt": prompt, "parts": parts}
