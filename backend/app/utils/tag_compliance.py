"""AI 打标合规规则：服装裸词判定（落库过滤与存量标签治理共用同一份口径）。

规则集中在此模块，避免「打标落库过滤」与「存量标签治理扫描」出现两套口径：

- 打标链路（``app/services/ai_tag_saver.py``）用 :func:`is_bare_garment_word`
  在产出前丢弃裸词，保证新标签符合命名口径；
- 标签治理（``tag_health`` 的 ``noncompliant`` 类）用
  :func:`classify_noncompliant` 给出不合规原因，供人工确认后批量重命名 /
  合并 / 删除关联。

判定口径（与提示词约束一致，见 README「标签类别体系」）：

1. 袜类裸词：以袜类结尾词收尾且整名不含颜色（「丝袜」「过膝袜」「吊带袜」）；
2. 缺长度丝袜：以「丝袜」结尾但整名不含任何长度/形态维度
   （「黑色丝袜」「肉色半透明肤色丝袜」）——无法区分连裤/过膝/长筒；
3. 裙类纯长度裸词：整名仅由长度词 + 「裙」构成（「短裙」「迷你短裙」）；
4. 鞋靴裸品类名：整名恰为固有鞋靴词（「高跟鞋」「凉鞋」「乐福鞋」）。

带颜色 / 款式 / 图案 / 材质修饰的整名不匹配上述任一规则，正常保留
（如「黑色过膝袜」「黑色连裤丝袜」「尖头细跟高跟鞋」「格纹百褶短裙」）。
"""

# 袜类结尾词：命中其一即视为袜类单品名
HOSIERY_TYPE_SUFFIXES = (
    "丝袜", "长筒袜", "过膝袜", "连裤袜", "中筒袜", "短袜", "船袜",
    "网袜", "渔网袜", "堆堆袜", "踝袜", "袜套", "袜",
)

# 颜色描述词：整名含其一即视为「带了颜色」，不属于裸词（不误丢）
COLOR_HINTS = (
    "黑", "白", "红", "橙", "黄", "绿", "蓝", "紫", "灰", "银",
    "金", "棕", "粉", "米", "肤", "青", "肉",
)

# 裙类「纯长度修饰词」：按长度降序排列，匹配时优先吃掉长词（「超短」先于「短」）
SKIRT_LENGTH_WORDS = ("迷你", "超短", "短")

# 丝袜类「长度/形态维度词」：丝袜单品名至少应写明一种，否则无法区分长度
HOSIERY_LENGTH_HINTS = (
    "连裤", "过膝", "及膝", "膝上", "大腿", "长筒", "中筒", "及踝",
    "踩脚", "船袜", "短袜", "吊带", "堆堆", "渔网", "袜套",
)

# 鞋靴类「裸品类名」：整名恰等于表内词才算裸词（带修饰的整名不匹配）
# 注意：小白鞋本身含颜色语义，不列入。
SHOE_BARE_WORDS = frozenset({
    # 鞋
    "高跟鞋", "中跟鞋", "低跟鞋", "细跟鞋", "粗跟鞋", "坡跟鞋", "猫跟鞋",
    "凉鞋", "高跟凉鞋", "凉拖", "拖鞋", "人字拖", "洞洞鞋", "沙滩鞋",
    "乐福鞋", "牛津鞋", "德比鞋", "帆布鞋", "运动鞋", "跑鞋", "板鞋", "老爹鞋",
    "皮鞋", "单鞋", "玛丽珍鞋", "芭蕾鞋", "豆豆鞋", "穆勒鞋", "渔夫鞋",
    "布鞋", "绣花鞋", "德训鞋", "网面鞋",
    # 靴
    "靴子", "短靴", "长靴", "中筒靴", "高筒靴", "踝靴", "马丁靴", "切尔西靴",
    "骑士靴", "袜靴", "雪地靴", "过膝靴", "及膝靴", "雨靴", "筒靴",
    "机车靴", "沙漠靴",
})

# 不合规原因码 -> 中文说明（供治理面板展示）
NONCOMPLIANT_REASONS: dict[str, str] = {
    "bare_hosiery": "袜类裸词：只有品类没有颜色",
    "lengthless_silk": "丝袜缺长度：未写明连裤/过膝/长筒",
    "bare_skirt": "裙类裸词：只有长度没有颜色款式",
    "bare_shoe": "鞋靴裸词：只有品类没有颜色款式",
}


def is_bare_hosiery_word(name: str) -> bool:
    """判断标签名是否为「无颜色修饰的袜类裸品类名」。

    判定规则：名称以袜类结尾词收尾，且整名不含任何颜色描述字
    （如「丝袜」「过膝袜」「吊带袜」→ True；「黑色丝袜」「白丝」→ False）。
    """
    if not name.endswith(HOSIERY_TYPE_SUFFIXES):
        return False
    return not any(hint in name for hint in COLOR_HINTS)


def is_lengthless_silk_word(name: str) -> bool:
    """判断标签名是否为「缺长度维度的丝袜泛称」。

    丝袜单品名若以「丝袜」结尾、却整名不含任何长度/形态维度词，则无法
    区分连裤袜/过膝袜/长筒袜，判为不合格泛称（如「黑色丝袜」「肉色半透明
    肤色丝袜」「哑光肤色丝袜」→ True；「黑色连裤丝袜」「黑色透肉连裤袜」
    → False，正常保留）。「黑丝/白丝」是公认的黑色连裤丝袜简称（库内既有
    标签），不在此列。
    """
    if not name.endswith("丝袜"):
        return False
    return not any(hint in name for hint in HOSIERY_LENGTH_HINTS)


def is_bare_skirt_word(name: str) -> bool:
    """判断标签名是否为「仅长度修饰的裙类裸词」。

    判定规则：以「裙」收尾，且去掉尾字后剩余部分只能由纯长度词
    （短/迷你/超短）拼成，不含任何颜色/款式/图案/材质修饰
    （如「短裙」「迷你短裙」「超短裙」→ True；「黑色百褶短裙」
    「格纹百褶短裙」「高腰包臀短裙」→ False，正常保留）。
    """
    if not name.endswith("裙"):
        return False
    rest = name[:-1]
    while rest:
        for word in SKIRT_LENGTH_WORDS:
            if rest.startswith(word):
                rest = rest[len(word):]
                break
        else:
            # 存在长度词以外的修饰（颜色/款式/图案/材质）→ 不属于裸裙词
            return False
    return True


def is_bare_shoe_word(name: str) -> bool:
    """判断标签名是否为鞋靴固有裸品类名（如「高跟鞋」「凉鞋」「短靴」）。"""
    return name in SHOE_BARE_WORDS


def is_bare_garment_word(name: str) -> bool:
    """判断标签名是否为需要丢弃的服装裸词。

    覆盖四类：袜类裸词 / 缺长度丝袜泛称 / 裙类纯长度裸词 / 鞋靴固有裸品类名。
    打标链路在产出标签前调用本函数，命中即丢弃。
    """
    return (
        is_bare_hosiery_word(name)
        or is_lengthless_silk_word(name)
        or is_bare_skirt_word(name)
        or is_bare_shoe_word(name)
    )


def classify_noncompliant(name: str) -> str | None:
    """返回标签名的不合规原因码；合规返回 None。

    原因码见 :data:`NONCOMPLIANT_REASONS`。判定顺序与
    :func:`is_bare_garment_word` 一致（先袜类裸词，再缺长度丝袜，最后裙/鞋），
    保证同一名称的原因归类稳定可复现。
    """
    if is_bare_hosiery_word(name):
        return "bare_hosiery"
    if is_lengthless_silk_word(name):
        return "lengthless_silk"
    if is_bare_skirt_word(name):
        return "bare_skirt"
    if is_bare_shoe_word(name):
        return "bare_shoe"
    return None
