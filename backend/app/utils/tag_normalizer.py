"""标签标准化：同义词映射、编辑距离去重、置信度过滤。"""

from difflib import SequenceMatcher

from sqlalchemy import select

from app.config import settings


# 中文时尚术语的同义词映射表
SYNONYM_MAP: dict[str, str] = {
    "纯白": "白色",
    "奶白": "白色",
    "米白": "米色",
    "纯黑": "黑色",
    "藏青": "海军蓝",
    "粉色系": "粉色",
    "藍色": "蓝色",
    "紅色": "红色",
    "黑白": "黑色",  # 同时也会添加"白色"
    "jk": "JK制服",
    "JK": "JK制服",
    "Jk": "JK制服",
    "jk制服": "JK制服",
    "日式": "日系",
    "韓系": "韩系",
    "通勤风": "通勤",
    "学院": "学院风",
    "街头风": "街头",
    "暗黑": "暗黑风",
    "新中式风": "新中式",
    "国风": "新中式",
    "露臉": "露脸",
    "不露臉": "不露脸",
    "半身照": "半身",
    "全身照": "全身",
    "坐": "坐姿",
    "站": "站姿",
    "对镜拍": "对镜自拍",
    "宽松款": "宽松",
    "修身款": "修身",
    "紧身款": "紧身",
    "oversize": "Oversized",
    "叠搭": "叠穿",
    "春天": "春季",
    "夏天": "夏季",
    "秋天": "秋季",
    "冬天": "冬季",
}


def normalize_tag_name(name: str) -> str:
    """通过同义词映射标准化标签名称。"""
    # 精确匹配
    if name in SYNONYM_MAP:
        return SYNONYM_MAP[name]

    # 大小写不敏感匹配
    name_lower = name.lower()
    for key, value in SYNONYM_MAP.items():
        if key.lower() == name_lower:
            return value

    return name


async def normalize_tag_name_async(db, name: str) -> str:
    """异步版标签名标准化：先查数据库别名，再回退到硬编码同义词映射。

    用于 get_or_create_tag 链路，使 AI 打标也能命中用户自定义的别名归一化。
    别名表较小且带索引，这里不做进程内缓存，保证别名增删立即生效。
    """
    if not name:
        return name
    stripped = name.strip()

    # 先走硬编码同义词映射（无需查库）
    normalized = normalize_tag_name(stripped)
    if normalized != stripped:
        return normalized

    # 再查数据库别名（延迟导入，避免模块加载顺序耦合）
    from app.models.tag import Tag, TagAlias

    result = await db.execute(
        select(Tag.name)
        .join(TagAlias, Tag.id == TagAlias.tag_id)
        .where(TagAlias.alias == stripped)
    )
    main_name = result.scalar_one_or_none()
    return main_name or stripped


def is_low_confidence(confidence: float) -> bool:
    """判断置信度是否低于低置信度阈值。"""
    return confidence < settings.ai_low_confidence_threshold


def string_similarity(a: str, b: str) -> float:
    """计算两个字符串的相似度（0.0 - 1.0）。"""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def should_merge_tags(name1: str, name2: str, threshold: float = 0.75) -> bool:
    """判断两个标签名称是否足够相似，建议合并。"""
    return string_similarity(name1, name2) >= threshold and name1 != name2


def validate_tag_name(name: str) -> tuple[bool, str | None]:
    """校验标签名是否合法。返回 (是否合法, 错误原因)。"""
    s = name.strip()
    if not s or len(s) < 1:
        return False, "标签名为空"

    # 允许的纯英文时尚专有名词（大小写不敏感）
    ALLOWED_ENGLISH = {
        'y2k', 'lolita', 'jk', 'cleanfit', 'gorpcore',
        'oversized', 'h型', 'a字', 'x型', 'v领', 'u领',
    }
    if s.lower() in ALLOWED_ENGLISH:
        return True, None

    # 允许中文+英文/数字的混合标签（如 V领, A字裙, H型）
    has_chinese = any('一' <= c <= '鿿' for c in s)

    # 纯 ASCII 且无中文 → 检查是否为允许的混合型
    if not has_chinese:
        if s.isascii() and s.replace(' ', '').isalpha() and len(s) > 2:
            return False, f"标签名是英文: {s!r}"

    if len(s) > 8:
        return False, f"标签名过长 ({len(s)} 字): {s[:20]}..."
    # 不能有句号/问号/感叹号
    if any(c in s for c in '。！？…~'):
        return False, f"标签名含标点: {s!r}"
    # 不能是描述句
    sentence_markers = [
        '这是一', '图片中', '背景为', '背景是', '整体造型', '完整展示',
        '人物为', '人物坐在', '人物穿着', '展示穿搭', '图中人物',
        '穿着方式', '图片属性', '主色调',
        '宽松/修身', '过膝/露腰', '直筒/H型', '紧身/A字',
        '颜色为', '搭配了', '整体穿搭', '整体色调',
    ]
    if any(m in s for m in sentence_markers):
        return False, f"标签名是描述句: {s!r}"
    # 不能是 hex 颜色
    if s.startswith('#'):
        return False, f"标签名是 hex 颜色: {s!r}"
    if len(s) == 6 and all(c in '0123456789ABCDEFabcdef' for c in s):
        return False, f"标签名是 hex 颜色: {s!r}"
    # 不能是带括号的推测描述
    if '（' in s or '(' in s:
        return False, f"标签名含括号推测: {s!r}"
    return True, None
