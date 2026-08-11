"""标签标准化：同义词映射、编辑距离去重、置信度过滤。"""

from difflib import SequenceMatcher

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


def is_low_confidence(confidence: float) -> bool:
    """判断置信度是否低于低置信度阈值。"""
    return confidence < settings.ai_low_confidence_threshold


def string_similarity(a: str, b: str) -> float:
    """计算两个字符串的相似度（0.0 - 1.0）。"""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def should_merge_tags(name1: str, name2: str, threshold: float = 0.75) -> bool:
    """判断两个标签名称是否足够相似，建议合并。"""
    return string_similarity(name1, name2) >= threshold and name1 != name2
