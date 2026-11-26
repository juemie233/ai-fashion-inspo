"""AI 分析标签的保存与关联：将解析后的标签数据写入数据库。

包含标签标准化、颜色映射、素材-标签关联（去重/竞态处理）。
"""

import colorsys
import logging
from collections.abc import Iterator

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tag import InspirationTag, Tag
from app.services.ai_parser import extract_tag_names
from app.services.tag_service import get_or_create_tag
from app.utils.leg_posture import normalize_leg_posture
from app.utils.tag_compliance import is_bare_garment_word
from app.utils.tag_normalizer import is_similar_category_tag, normalize_tag_name

logger = logging.getLogger(__name__)


def _as_list(value) -> list:
    """把模型可能输出的标量/列表/None 统一成列表。"""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


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

    重新分析语义：先完整解析，确认有标签后才清除旧 AI 关联并写入本次结果，
    失败/空结果保留上一次标签（避免一次失败分析清空素材）；
    与新增关联在同一事务内原子完成（调用方统一 commit）；
    手动/种子关联不受影响。
    """
    # 先完整解析再清旧标签：解析失败/空结果时保留上一次 AI 标签，避免一次
    # 失败的分析把素材清空（历史快照仍在，但用户当前标签不应丢）。
    extracted = list(iter_extracted_tags(data))
    if not extracted:
        return 0

    await clear_ai_tags(db, inspiration_id)
    tag_count = 0
    for name, category, confidence in extracted:
        tag = await get_or_create_tag(db, name, category, "ai_generated")
        await link_tag(db, inspiration_id, tag.id, confidence=confidence)
        tag_count += 1
    return tag_count


# ---------------------------------------------------------------------------
# 服装裸词兜底过滤
# ---------------------------------------------------------------------------
# 判定规则集中在 app/utils/tag_compliance.py，与「存量标签治理扫描」共用同一份
# 口径（避免落库过滤与治理扫描两套规则打架）。此处只负责在产出前调用：
# 提示词口径要求袜/丝袜/鞋靴类 type 以颜色开头、裙类直接描述应带颜色和款式，
# 但模型仍可能漏写修饰，把「丝袜」「短裙」「高跟鞋」等裸品类名写进 type /
# material / design_detail / features。此类裸词与独立 color 字段语义重复、
# 无法体现颜色款式，落库前兜底丢弃（color 字段照常保留）。覆盖全部类别
# 来源：item_type（type）、material（material 键）、design_detail、
# body_part（items.features）。


def iter_extracted_tags(data: dict) -> Iterator[tuple[str, str, float]]:
    """迭代 AI 分析结果中的 (标签名, 类别, 置信度)，按 (名称, 类别) 去重。

    快照和关联都依赖这个迭代器；同一标签被多个单品/多个键重复输出时只能落一次，
    否则标签统计翻倍、快照表还会撞 (log_id, tag_id) 唯一约束。
    """
    seen: dict[tuple[str, str], tuple[str, str, float]] = {}
    order: list[tuple[str, str]] = []
    for name, category, confidence in _iter_extracted_tags_raw(data):
        key = (name, category)
        if key not in seen:
            seen[key] = (name, category, confidence)
            order.append(key)
        elif confidence > seen[key][2]:
            seen[key] = (name, category, confidence)
    for key in order:
        yield seen[key]


def _iter_extracted_tags_raw(data: dict) -> Iterator[tuple[str, str, float]]:
    """原始提取逻辑；去重与类型防护由外层 ``iter_extracted_tags`` 负责。"""
    if not isinstance(data, dict):
        return

    # 数据键 -> 标签类别的映射（类别值为 snake_case）
    # 新键：design_detail（款式细节）/ material（面料材质），由原 wear_style 拆分而来
    category_map = {
        "style": "style",
        "fit": "fit",
        "design_detail": "design_detail",
        "material": "material",
        # 历史键兼容：wear_style（旧「穿着方式」）保留映射到遗留类别 body_part，
        # 保证重新解析历史 AI 日志（旧格式输出）仍能正常落库
        "wear_style": "body_part",
        "attributes": "attribute",
        # 新键为 snake_case；旧 PascalCase 键（Atmosphere/Expression/Leg_Posture）
        # 同样兼容历史日志，统一归入新 snake_case 类别
        "atmosphere": "atmosphere",
        "expression": "expression",
        "leg_posture": "leg_posture",
        "Atmosphere": "atmosphere",
        "Expression": "expression",
        "Leg_Posture": "leg_posture",
    }

    # 预先提取风格大类标签名：氛围标签若与风格标签近似（如「甜美」vs「甜美风」），
    # 只保留风格标签，丢弃氛围标签，避免两个大类出现语义重复标签。
    style_values = _as_list(data.get("style"))
    style_names: list[str] = []
    for value in style_values:
        for name in extract_tag_names(value):
            norm = normalize_tag_name(name)
            if norm:
                style_names.append(norm)

    # 腿部姿态单独处理：先做可见性门控、同义归一和矛盾过滤，再落库。
    # 历史 PascalCase 键与 snake_case 键共用同一套规则。
    leg_values = _as_list(data.get("leg_posture")) + _as_list(data.get("Leg_Posture"))
    leg_names: list[str] = []
    for value in leg_values:
        if isinstance(value, str):
            leg_names.append(value)
        else:
            leg_names.extend(extract_tag_names(value))
    attr_values = _as_list(data.get("attributes"))
    attr_names: set[str] = set()
    for value in attr_values:
        attr_names.update(
            norm
            for norm in (normalize_tag_name(n) for n in extract_tag_names(value))
            if norm
        )
    normalized_leg_tags = normalize_leg_posture(leg_names, attr_names)
    for name in normalized_leg_tags:
        yield name, "leg_posture", 0.8

    # 处理简单列表型标签（风格、版型等） — 兼容 null 值
    for key, category in category_map.items():
        if category == "leg_posture":
            continue
        values = _as_list(data.get(key))
        for value in values:
            extracted = extract_tag_names(value)
            for name in extracted:
                name = normalize_tag_name(name)
                if not name:
                    continue
                # 氛围标签去重：与任一风格标签近似则跳过（保留风格标签）
                # 兼容旧 PascalCase 键与新 snake_case 键两种输出
                if category == "atmosphere" and any(
                    is_similar_category_tag(name, s) for s in style_names
                ):
                    continue
                # 服装裸词兜底：袜类裸词 / 裙类纯长度裸词（含 material、features
                # 路径漏出的「丝袜」「短裙」等），一律不落库
                if is_bare_garment_word(name):
                    continue
                yield name, category, 0.8

    # 处理结构化单品标签 — 兼容 type/color 为列表、features 为字符串/dict/null
    items = _as_list(data.get("items"))
    for item in items:
        # 兼容模型把 items 写成字符串列表：["衬衫", "半身裙"]
        if isinstance(item, str):
            item_type = normalize_tag_name(item.strip())
            if item_type and not is_bare_garment_word(item_type):
                yield item_type, "item_type", 0.8
            continue
        if not isinstance(item, dict):
            continue

        # type/color 可能是列表 → 取首元素；null/其他类型按空串处理
        raw_type = item.get("type") or ""
        if isinstance(raw_type, list):
            raw_type = raw_type[0] if raw_type else ""
        if not isinstance(raw_type, str):
            raw_type = ""
        item_type = normalize_tag_name(raw_type.strip())

        raw_color = item.get("color") or ""
        if isinstance(raw_color, list):
            raw_color = raw_color[0] if raw_color else ""
        if not isinstance(raw_color, str):
            raw_color = ""
        color = normalize_color(raw_color.strip())

        if item_type and not is_bare_garment_word(item_type):
            yield item_type, "item_type", 0.8

        if color:
            yield color, "color", 0.85

        features = item.get("features")
        if isinstance(features, str):
            features = [
                part.strip()
                for part in features.replace("，", ",").replace("、", ",").split(",")
                if part.strip()
            ]
        else:
            features = _as_list(features)
        for feat in features:
            if isinstance(feat, str):
                names = extract_tag_names(feat)
            elif isinstance(feat, dict):
                names = extract_tag_names(feat)
            else:
                continue
            for raw_name in names:
                feature_name = normalize_tag_name(raw_name)
                if feature_name and not is_bare_garment_word(feature_name):
                    yield feature_name, "body_part", 0.7


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


# 常用 hex 颜色 → 中文名称映射（统一大写键，避免小写 hex 永远匹配不到）
HEX_COLOR_MAP: dict[str, str] = {
    # 红/粉
    "#FF0000": "红色", "#FF0F1C": "红色", "#E60012": "红色",
    "#FF008C": "粉色", "#FF69B4": "粉色", "#FFC0CB": "粉色",
    "#FFB6C1": "粉色", "#F1A0D6": "粉色",
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
    "#1E1D20": "黑色", "#1A0B2C": "深紫色",
    "#FFFFFF": "白色", "#FFF": "白色",
    "#808080": "灰色", "#A2A2AA": "灰色", "#C0C0C0": "银色",
    # 棕/米
    "#8B4513": "棕色", "#6C4B2A": "棕色", "#8C6B49": "棕色", "#B78432": "棕色",
    "#A0522D": "棕色",
    "#F5DEB3": "米色", "#F5F5DC": "米色",
    # 肤色
    "#FFE4C4": "肤色", "#FFDAB9": "肤色", "#FFE4B5": "肤色", "#E5938D": "肤色",
}
HEX_COLOR_MAP = {key.upper(): value for key, value in HEX_COLOR_MAP.items()}

EN_COLOR_MAP: dict[str, str] = {
    "BLACK": "黑色",
    "WHITE": "白色",
    "RED": "红色",
    "BLUE": "蓝色",
    "GREEN": "绿色",
    "YELLOW": "黄色",
    "PINK": "粉色",
    "PURPLE": "紫色",
    "ORANGE": "橙色",
    "BROWN": "棕色",
    "GRAY": "灰色",
    "GREY": "灰色",
    "GOLD": "金色",
    "SILVER": "银色",
    "BEIGE": "米色",
}
COLOR_ALIASES: dict[str, str] = {
    "黑": "黑色",
    "白": "白色",
    "红": "红色",
    "蓝": "蓝色",
    "绿": "绿色",
    "黄": "黄色",
    "粉": "粉色",
    "紫": "紫色",
    "橙": "橙色",
    "棕": "棕色",
    "灰": "灰色",
    "金": "金色",
    "银": "银色",
    "米": "米色",
}


def normalize_color(raw: str | None) -> str:
    """将原始颜色值标准化为中文颜色名。"""
    if not raw or not isinstance(raw, str):
        return ""

    # 中文颜色：先做同义词归一（黑→黑色、纯黑→黑色等）
    if any("\u4e00" <= char <= "\u9fff" for char in raw):
        name = normalize_tag_name(raw.lstrip("#").strip())
        return COLOR_ALIASES.get(name, name)

    # 英文颜色名（可能带 # 前缀，如 "#Black"）
    upper_raw = raw.lstrip("#").strip().upper()
    if upper_raw in EN_COLOR_MAP:
        return EN_COLOR_MAP[upper_raw]

    # 十六进制颜色：统一大写后先精确匹配，再走 HSV 推断；不再做 3 位前缀模糊匹配
    cleaned = raw.strip().upper()
    if not cleaned.startswith("#"):
        cleaned = f"#{cleaned}"
    if cleaned in HEX_COLOR_MAP:
        return HEX_COLOR_MAP[cleaned]
    return guess_color_from_hex(cleaned)


def guess_color_from_hex(hex_str: str) -> str:
    """根据十六进制颜色值按 HSV 色相/饱和度/明度推断中文颜色名。"""
    try:
        raw = hex_str.lstrip("#")
        if len(raw) >= 6:
            r, g, b = int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
        elif len(raw) == 3:
            r, g, b = int(raw[0], 16) * 17, int(raw[1], 16) * 17, int(raw[2], 16) * 17
        else:
            return ""

        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        hue = h * 360

        # 低饱和度按明度分灰度；极暗的彩色（如 #000123）仍按色相归入深色
        if s < 0.12:
            if v < 0.18:
                return "黑色"
            if v > 0.90:
                return "白色"
            if v < 0.45:
                return "深灰色"
            return "浅灰色"

        # 高明度、低饱和度的红/品红偏粉（#ffdce6、#ff8fa3）
        if v > 0.75 and s < 0.45 and (hue < 20 or hue >= 320):
            return "粉色"

        # 橙色色相但整体偏暗 → 棕色（#786446）
        if 15 <= hue < 50 and v < 0.55:
            return "棕色"

        if hue < 15 or hue >= 345:
            return "红色"
        if hue < 40:
            return "橙色"
        if hue < 70:
            return "黄色"
        if hue < 170:
            return "绿色"
        if hue < 200:
            return "青色"
        if hue < 260:
            return "蓝色"
        if hue < 300:
            return "紫色"
        return "品红"
    except (ValueError, IndexError) as exc:
        logger.debug("guess_color_from_hex: 忽略异常（预期降级）：%s", exc)
        return ""


async def link_tag(
    db: AsyncSession,
    inspiration_id: str,
    tag_id: int,
    confidence: float = 1.0,
    source: str = "ai_generated",
) -> None:
    """把标签关联到素材（原子 upsert，幂等）。

    语义（三条都必须成立，写错了不报错只会让数据慢慢变脏）：

    - **同一 (素材, 标签) 只允许一行**：复合主键保证，重复调用不新增；
    - **置信度取较大值**：同一次分析内重复 yield、或视频融合等调用方重复写入时
      保留更高结果；重新分析前 save_tags 会先清掉 AI 关联，因此这里不负责
      “跨轮次保留旧高分”；
    - **不覆盖已有关联的 source**：手动/种子关联保持 manual，不会被二次分析改写成
      ai_generated（否则人工修正记录被抹掉）。

    实现用 SQLite 的 ``INSERT … ON CONFLICT DO UPDATE`` 而不是「先查后写 + 捕获
    IntegrityError」：
    - 先查后写有并发窗口，批量分析（多任务并发写同一素材）时必然撞唯一约束；
    - 原来的补偿路径是 ``begin_nested()`` + ``except IntegrityError``，但 flush 失败后
      Session 进入 pending-rollback 状态，补偿逻辑里的下一次 execute 直接抛
      ``PendingRollbackError``——**恢复路径自己成了新的异常**，整个分析批次跟着失败。
    upsert 是单条原子语句，既没有窗口也不需要异常处理。
    """
    stmt = (
        sqlite_insert(InspirationTag)
        .values(
            inspiration_id=inspiration_id,
            tag_id=tag_id,
            confidence=confidence,
            source=source,
        )
        .on_conflict_do_update(
            index_elements=[
                InspirationTag.inspiration_id,
                InspirationTag.tag_id,
            ],
            # 只动置信度（取较大值）；source 不在 set_ 里 → 保留原值。
            # 防御性 coalesce：当前 confidence 列为 NOT NULL；若未来/外部数据出现 NULL，
            # 标量 max 会把整列更新成 NULL。
            set_={
                "confidence": func.max(
                    func.coalesce(InspirationTag.confidence, 0), confidence
                )
            },
        )
    )
    await db.execute(stmt)
