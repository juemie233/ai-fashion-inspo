"""AI 分析标签的保存与关联：将解析后的标签数据写入数据库。

包含标签标准化、颜色映射、素材-标签关联（去重/竞态处理）。
"""

from collections.abc import Iterator
from typing import TYPE_CHECKING

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tag import InspirationTag, Tag
from app.services.ai_parser import extract_tag_names
from app.services.tag_service import get_or_create_tag
from app.utils.tag_normalizer import normalize_tag_name

if TYPE_CHECKING:
    from app.models.tag import Tag


async def clear_ai_tags(db: AsyncSession, inspiration_id: str) -> int:
    """清除素材上所有由 AI 分析产生的标签关联（source=ai_generated）。

    业务规则（重新分析时先清后写，保证只保留最新一次 AI 结果）：
    - 仅删除关联行（inspiration_tags），不删除 tags 表中的标签本身
      （标签保留以便复用与历史追溯）；
    - 不影响手动标签（manual）与种子标签关联；
    - 不触碰 ai_extracted_tags 历史快照表。

    返回被删除的关联数。
    """
    result = await db.execute(
        delete(InspirationTag).where(
            InspirationTag.inspiration_id == inspiration_id,
            InspirationTag.source == "ai_generated",
        )
    )
    return result.rowcount or 0


async def save_tags(db: AsyncSession, inspiration_id: str, data: dict) -> int:
    """将 AI 分析提取的标签保存到数据库。返回关联的标签数。

    重新分析语义：先清除该素材既有的 AI 标签关联，再写入本次结果，
    与新增关联在同一事务内原子完成（调用方统一 commit）；
    手动/种子关联不受影响。
    """
    await clear_ai_tags(db, inspiration_id)
    tag_count = 0
    for name, category, confidence in iter_extracted_tags(data):
        tag = await get_or_create_tag(db, name, category, "ai_generated")
        await link_tag(db, inspiration_id, tag.id, confidence=confidence)
        tag_count += 1
    return tag_count


def iter_extracted_tags(data: dict) -> Iterator[tuple[str, str, float]]:
    """按与 save_tags 完全一致的规则，迭代 AI 分析结果中的 (标签名, 类别, 置信度)。

    供「素材-标签关联保存」与「ai_extracted_tags 结构化快照」共用，
    保证两种落库方式提取出的标签集合一致（支撑多版本对比的可信度）。
    """
    # 数据键 -> 标签类别的映射
    category_map = {
        "style": "style",
        "fit": "fit",
        "wear_style": "body_part",
        "attributes": "attribute",
        "Atmosphere": "Atmosphere",
        "Expression": "Expression",
        "Leg_Posture": "Leg_Posture"
    }

    # 处理简单列表型标签（风格、版型等） — 兼容 null 值
    for key, category in category_map.items():
        values = data.get(key) or []
        if not isinstance(values, list):
            values = [values] if values else []
        for value in values:
            extracted = extract_tag_names(value)
            for name in extracted:
                name = normalize_tag_name(name)
                if name:
                    yield name, category, 0.8

    # 处理结构化单品标签 — 兼容 type/color 为列表、features 为字符串
    items = data.get("items") or []
    if not isinstance(items, list):
        items = [items] if isinstance(items, dict) else []
    for item in items:
        if isinstance(item, dict):
            # type/color 可能是列表 → 取首元素或 join
            raw_type = item.get("type", "")
            if isinstance(raw_type, list):
                raw_type = raw_type[0] if raw_type else ""
            item_type = normalize_tag_name(str(raw_type).strip())

            raw_color = item.get("color", "")
            if isinstance(raw_color, list):
                raw_color = raw_color[0] if raw_color else ""
            color = normalize_color(str(raw_color).strip())

            features = item.get("features", [])
            # features 可能是字符串 → 按顿号/逗号拆分
            if isinstance(features, str):
                features = [p.strip() for p in features.replace('，', ',').replace('、', ',').split(',') if p.strip()]

            if item_type:
                yield item_type, "item_type", 0.8

            if color:
                yield color, "color", 0.85

            for feat in features:
                if isinstance(feat, str):
                    for fv in extract_tag_names(feat):
                        fv = normalize_tag_name(fv)
                        if fv:
                            yield fv, "body_part", 0.7
                elif isinstance(feat, dict):
                    for fv in extract_tag_names(feat):
                        fv = normalize_tag_name(fv)
                        if fv:
                            yield fv, "body_part", 0.7


async def resolve_tag_ids(
    db: AsyncSession, names: list[str]
) -> dict[str, int]:
    """按名称批量查询已有标签 ID（不创建），返回 {名称: tag_id}。

    供结构化快照使用：快照只记录「本次分析提取的标签」，不因快照产生新标签。
    """
    if not names:
        return {}
    result = await db.execute(select(Tag.id, Tag.name).where(Tag.name.in_(names)))
    return {name: tag_id for tag_id, name in result.all()}


# 常用 hex 颜色 → 中文名称映射
HEX_COLOR_MAP: dict[str, str] = {
    # 红/粉
    "#FF0000": "红色", "#FF0F1C": "红色", "#E60012": "红色",
    "#FF008C": "粉色", "#FF69B4": "粉色", "#FFC0CB": "粉色",
    "#FFB6C1": "粉色", "#f1a0d6": "粉色",
    # 橙/黄/金
    "#FFA500": "橙色", "#FF8C00": "橙色",
    "#FFD700": "金色", "#FFC41B": "金色", "#FFB30A": "金色", "#E4B53A": "金色",
    "#FFFF00": "黄色",
    # 绿
    "#008000": "绿色", "#00FF00": "绿色", "#128F7D": "青绿色", "#015342": "深绿色",
    # 蓝
    "#0000FF": "蓝色", "#0000A2": "深蓝色", "#0A3647": "深蓝色",
    "#0D173A": "深蓝色", "#0E1A3D": "深蓝色", "#000039": "深蓝色",
    "#1E90FF": "蓝色", "#4169E1": "蓝色",
    # 紫
    "#800080": "紫色", "#8B00FF": "紫色", "#4B0082": "紫色",
    # 黑/白/灰
    "#000000": "黑色", "#000": "黑色", "#0C1317": "黑色",
    "#1e1d20": "黑色", "#1A0B2C": "深紫色",
    "#FFFFFF": "白色", "#FFF": "白色",
    "#808080": "灰色", "#A2A2AA": "灰色", "#C0C0C0": "银色",
    # 棕/米
    "#8B4513": "棕色", "#6C4B2A": "棕色", "#8C6B49": "棕色", "#b78432": "棕色",
    "#A0522D": "棕色",
    "#F5DEB3": "米色", "#F5F5DC": "米色",
    # 肤色
    "#FFE4C4": "肤色", "#FFDAB9": "肤色", "#FFE4B5": "肤色", "#E5938D": "肤色",
}


def normalize_color(raw: str) -> str:
    """将原始颜色值标准化为中文颜色名。"""
    if not raw:
        return ""

    # 已经是中文颜色名（可能带 # 前缀，如 "#黑色"）
    has_chinese = any('一' <= c <= '鿿' for c in raw)
    if has_chinese:
        # 去掉 # 前缀
        return raw.lstrip("#").strip()

    # 英文颜色名（可能带 # 前缀，如 "#Black"）
    EN_COLOR_MAP = {"BLACK": "黑色", "WHITE": "白色", "RED": "红色", "BLUE": "蓝色",
                    "GREEN": "绿色", "YELLOW": "黄色", "PINK": "粉色", "PURPLE": "紫色",
                    "ORANGE": "橙色", "BROWN": "棕色", "GRAY": "灰色", "GREY": "灰色",
                    "GOLD": "金色", "SILVER": "银色", "BEIGE": "米色"}
    upper_raw = raw.lstrip("#").strip().upper()
    if upper_raw in EN_COLOR_MAP:
        return EN_COLOR_MAP[upper_raw]

    # 去除 # 前缀后查找
    cleaned = raw.strip().upper()
    if not cleaned.startswith("#"):
        cleaned = f"#{cleaned}"

    # 精确匹配
    if cleaned in HEX_COLOR_MAP:
        return HEX_COLOR_MAP[cleaned]

    # 前缀模糊匹配（如 #000039 → 深蓝色）
    if len(cleaned) >= 4:
        prefix = cleaned[:4]
        for hex_key, name in HEX_COLOR_MAP.items():
            if hex_key.startswith(prefix):
                return name

    # 按 RGB 分量推断基本颜色
    return guess_color_from_hex(cleaned)


def guess_color_from_hex(hex_str: str) -> str:
    """根据十六进制颜色值推断基本颜色名称。"""
    try:
        h = hex_str.lstrip("#")
        if len(h) >= 6:
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        elif len(h) == 3:
            r, g, b = int(h[0], 16) * 17, int(h[1], 16) * 17, int(h[2], 16) * 17
        else:
            return ""

        # 灰度检测
        if abs(r - g) < 20 and abs(g - b) < 20 and abs(r - b) < 20:
            if r < 40:
                return "黑色"
            elif r > 220:
                return "白色"
            elif r < 120:
                return "深灰色"
            else:
                return "浅灰色"

        # 基本颜色推断
        if r > g and r > b:
            if r - max(g, b) < 40:
                if g > b:
                    return "棕色"
                return "粉色" if r > 200 else "深红色"
            return "红色"
        if g > r and g > b:
            return "绿色"
        if b > r and b > g:
            return "蓝色"
        if r > 150 and g > 100 and b < 80:
            return "橙色"
        if r > 150 and g > 150 and b < 60:
            return "金色"
        return ""
    except (ValueError, IndexError):
        return ""


async def link_tag(
    db: AsyncSession,
    inspiration_id: str,
    tag_id: int,
    confidence: float = 1.0,
    source: str = "ai_generated",
) -> None:
    """将标签与素材关联，避免重复。纠竞态冲突，置信度更高时更新。

    新建关联写入 source（AI 关联默认 ai_generated）；已存在关联时
    保留其原 source（不把手动关联覆盖成 AI 关联）。
    """
    result = await db.execute(
        select(InspirationTag).where(
            InspirationTag.inspiration_id == inspiration_id,
            InspirationTag.tag_id == tag_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        if confidence > existing.confidence:
            existing.confidence = confidence
        await db.flush()
    else:
        link = InspirationTag(
            inspiration_id=inspiration_id,
            tag_id=tag_id,
            confidence=confidence,
            source=source,
        )
        db.add(link)
        try:
            async with db.begin_nested():
                await db.flush()
        except IntegrityError:
            # SAVEPOINT 已回滚（不影响同事务其它关联），移除失败对象后重查更新
            db.expunge(link)
            result2 = await db.execute(
                select(InspirationTag).where(
                    InspirationTag.inspiration_id == inspiration_id,
                    InspirationTag.tag_id == tag_id,
                )
            )
            retry_existing = result2.scalar_one_or_none()
            if retry_existing and confidence > retry_existing.confidence:
                retry_existing.confidence = confidence
                await db.flush()
