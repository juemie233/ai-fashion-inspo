"""AI 响应解析工具：从模型输出中提取、修复并校验 JSON 与标签名称。

本模块为纯函数集合，不依赖数据库；标签长度阈值从全局配置读取
（settings.tag_name_max_length，可用 .env 的 TAG_NAME_MAX_LENGTH 覆盖），
供 ai_service / ai_tag_saver 复用。
包含：
- 各类 MiniCPM-V 输出畸形格式的修复（注释、Python set、裸词数组、单引号、截断）
- 标签名称的递归提取与过滤
"""

import json
import re
from typing import Any


def quote_bare_array_words(text: str) -> str:
    """给数组内未加引号的裸中文/英文词加引号。

    MiniCPM-V 常输出 [宽松] 或 [阔腿, 运动风] 这类缺失引号的数组项。
    仅处理不含引号/花括号的简单数组，避免破坏已合法的 JSON。
    """
    def _repl(m: re.Match[str]) -> str:
        inner = m.group(1)
        if not inner.strip():
            return m.group(0)  # 空数组
        if '"' in inner or '{' in inner or '}' in inner:
            return m.group(0)  # 已含引号或对象，跳过
        # 按半角/全角逗号分隔
        parts = re.split(r'\s*[,，]\s*', inner.strip())
        quoted = []
        for p in parts:
            if re.fullmatch(r'[一-鿿A-Za-z][一-鿿A-Za-z0-9]*', p):
                quoted.append(f'"{p}"')
            else:
                quoted.append(p)
        return '[' + ', '.join(quoted) + ']'
    return re.sub(r'\[([^\[\]]*)\]', _repl, text)


def fix_python_sets(text: str) -> str:
    """将 Python set 语法转为 JSON。

    {"过膝"} → "过膝"（单元素，作为字符串）
    {"V领", "短袖"} → ["V领", "短袖"]（多元素，作为数组）
    不含冒号（冒号表示这是字典，跳过）。
    """
    def _repl(m: re.Match[str]) -> str:
        strings = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(0))
        if len(strings) == 1:
            return f'"{strings[0]}"'
        return '[' + ', '.join(f'"{s}"' for s in strings) + ']'
    return re.sub(
        r'\{\s*("(?:[^"\\]|\\.)*")(?:\s*,\s*"(?:[^"\\]|\\.)*")*\s*\}',
        _repl,
        text,
    )


def parse_analysis_response(raw: str) -> dict:
    """从模型响应中提取并解析 JSON。"""
    text = raw.strip()

    # 去除可能的 markdown 代码块标记
    if text.startswith("```"):
        lines = text.split("\n")
        start_idx = 0
        end_idx = len(lines)
        for i, line in enumerate(lines):
            if line.startswith("```") and start_idx == 0:
                start_idx = i + 1
            elif line.startswith("```") and start_idx > 0:
                end_idx = i
                break
        text = "\n".join(lines[start_idx:end_idx])

    # 先尝试直接解析
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
        # 顶层是数组等非 dict 时，继续走清洗+评分策略，避免下游 .get() 崩溃
    except json.JSONDecodeError:
        pass

    # 预处理：清洗各类注释和非法内容
    cleaned = text
    # 去除 /* */ 多行注释
    cleaned = re.sub(r'/\*.*?\*/', '', cleaned, flags=re.DOTALL)
    # 去除 // 单行注释（负向前查找避免误删 URL 里的 https://）
    cleaned = re.sub(r'(?<!:)//[^\n]*', '', cleaned)
    # 修复单引号字符串（MiniCPM-V 有时输出 '...' 而非 "..."）
    cleaned = re.sub(r"'([^'\"]*)'", r'"\1"', cleaned)
    # 修复 Python set 语法 {"值"} → "值"、{"a","b"} → ["a","b"]
    cleaned = fix_python_sets(cleaned)
    # 修复数组内未加引号的中文词 [围巾] → ["围巾"]、[阔腿, 运动风] → ["阔腿", "运动风"]
    cleaned = quote_bare_array_words(cleaned)
    # 去除 # 风格注释/颜色标注，但用负向前查找保留字符串内的颜色值
    # （"#F4D0A3" 是合法颜色值要保留，" #FFFFFF" 是注释要删除）
    cleaned = re.sub(r'(?<!")#[0-9A-Fa-f]{6}\b', '', cleaned)
    cleaned = re.sub(r'(?<!")#[0-9A-Fa-f]{3}\b', '', cleaned)
    # 去除独立的 # 注释（非字符串内的 #）
    cleaned = re.sub(r'(?<!")#[^\n,}\]\"]*', '', cleaned)
    # 修复注释清理后留下的格式问题
    cleaned = re.sub(r'"\s+"', '\", \"', cleaned)        # "value"  " → "value", "
    cleaned = re.sub(r'",\s*"\s*\]', '\"]', cleaned)     # "value", " ] → "value"]
    cleaned = re.sub(r'"\s+\]', '\"]', cleaned)          # "value" ] → "value"]
    cleaned = re.sub(r'",\s*"\s*\}', '\"}', cleaned)     # "value", " } → "value"}
    cleaned = re.sub(r'"\s+\}', '\"}', cleaned)          # "value" } → "value"}
    # 去除尾部逗号（在 ] 或 } 前的逗号；字符串感知，不误删标签名内的 ",}"）
    cleaned = _strip_trailing_commas(cleaned)

    # 策略1：找到所有完整 { ... } 块，按「更像分析结果」打分
    # 评分标准：含有越多预期键（style/items/fit等），得分越高
    EXPECTED_KEYS = {"style", "items", "fit", "wear_style", "attributes", "dominant_colors", "Atmosphere", "Expression", "Leg_Posture"}
    candidates = []
    for m in re.finditer(r'\{', cleaned):
        depth = 0
        start = m.start()
        end = -1
        for i in range(start, len(cleaned)):
            if cleaned[i] == '{':
                depth += 1
            elif cleaned[i] == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end > 0:
            candidates.append(cleaned[start:end])
    # 尝试解析，选包含最多预期键的结果
    best = {}
    for candidate in candidates:
        try:
            data = json.loads(candidate)
            score = sum(1 for k in EXPECTED_KEYS if k in data)
            if score > sum(1 for k in EXPECTED_KEYS if k in best):
                best = data
        except json.JSONDecodeError:
            continue
    if best:
        return best

    # 策略2：找第一个 { ... } 块（兼容旧行为）
    match = re.search(r'\{.*?\}', cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # 策略3：修复可能被截断的 JSON
    repaired = repair_truncated_json(cleaned)
    if repaired:
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    return {}


def _strip_trailing_commas(text: str) -> str:
    """删除结构层的尾部逗号（`]` 或 `}` 前的逗号）。

    仅处理字符串外的逗号：字符串内部内容（如标签名 "黑,}"）不受影响。
    通过统计匹配位置之前未闭合的双引号个数判断逗号是否位于字符串内。
    """

    def _repl(m: re.Match[str]) -> str:
        prefix = text[: m.start()]
        # 忽略转义引号后统计未闭合引号；奇数个说明逗号在字符串内
        if prefix.replace('\\"', "").count('"') % 2 == 1:
            return m.group(0)  # 字符串内，保留原样
        return m.group(1)  # 结构层，删除逗号（保留空白与闭合符）

    return re.sub(r",(\s*[}\]])", _repl, text)


def repair_truncated_json(text: str) -> str | None:
    """尝试修复被截断的 JSON（模型输出超限或提前结束）。"""
    start = text.find("{")
    if start == -1:
        return None
    fragment = text[start:]

    # 追踪括号栈
    brackets = []
    in_string = False
    escaped = False
    for ch in fragment:
        if escaped:
            escaped = False; continue
        if ch == '\\':
            escaped = True; continue
        if ch == '"':
            in_string = not in_string; continue
        if in_string:
            continue
        if ch == '{': brackets.append('}')
        elif ch == '[': brackets.append(']')
        elif ch == '}' and brackets and brackets[-1] == '}': brackets.pop()
        elif ch == ']' and brackets and brackets[-1] == ']': brackets.pop()

    # 闭合末尾未闭合的字符串（模型被截断时常见）
    if in_string:
        fragment += '"'

    # 补齐缺失的闭合符
    fragment += ''.join(reversed(brackets))

    # 补全后清理结构层尾部逗号（如 "...]," → "...]"），
    # JSON 不允许尾部逗号；字符串感知，不触碰字符串内部内容（如 "黑,}"）。
    fragment = _strip_trailing_commas(fragment)

    return fragment if fragment.startswith('{') else None


def parse_is_outfit(value: Any) -> bool | None:
    """严格解析模型的 is_outfit 输出，避免脏数据误判。

    仅布尔 True 或明确的真值字符串判定为通过；模糊值返回 None（保持 pending）。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "1", "是", "yes", "y"):
            return True
        if v in ("false", "0", "否", "no", "n"):
            return False
    return None


def looks_truncated(raw: str) -> bool:
    """粗略判断模型输出是否因 token 截断而 JSON 未闭合（括号不平衡）。"""
    if not raw:
        return False
    depth = 0
    in_str = False
    escape = False
    for ch in raw:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
    return depth > 0


_TAG_DROP_SENTENCES = ("这是一", "图片中", "背景为", "整体造型", "完整展示", "人物为")


def _extract_str_tag(s: str, max_length: int | None = None) -> list[str]:
    """从单个字符串值中提取合法标签（长度/标点/句式过滤）。

    参数:
        max_length: 标签名最大允许字数；为 None 时读取全局配置
            ``settings.tag_name_max_length``（默认 12），与低质命名判定阈值一致。
    """
    if max_length is None:
        from app.config import settings

        max_length = settings.tag_name_max_length
    if len(s) <= 1:
        return []
    if s.startswith("{") and s.endswith("}"):
        return []
    if any(c in s for c in "。！？…~"):
        return []
    if any(w in s for w in _TAG_DROP_SENTENCES):
        return []

    # 先做分隔符拆分（拆分后再递归检查每部分的长度和合法性）
    if "，" in s or "," in s:
        parts = [p.strip() for p in s.replace("，", ",").split(",") if p.strip()]
        results = []
        for p in parts:
            results.extend(extract_tag_names(p))
        return results
    if "、" in s:
        parts = [p.strip() for p in s.split("、") if p.strip()]
        results = []
        for p in parts:
            results.extend(extract_tag_names(p))
        return results
    if "/" in s:
        parts = [p.strip() for p in s.split("/") if p.strip()]
        results = []
        for p in parts:
            results.extend(extract_tag_names(p))
        return results

    # 去除括号内容（如 "室内拍摄（棚拍）" → "室内拍摄"），需在长度检查之前
    if "（" in s or "(" in s:
        s = s.split("（")[0].split("(")[0].strip() if "（" in s else s.split("(")[0].strip()
        if not s or len(s) <= 1:
            return []

    # 拆分后再检查长度
    if len(s) > max_length:
        return []
    if s.isascii() and not any(c.isdigit() for c in s):
        return []
    if s.startswith("#") or (len(s) == 6 and all(c in "0123456789ABCDEFabcdef" for c in s)):
        return []
    if s.isdigit():
        return []
    return [s]


def _extract_dict_tags(value: dict) -> list[str]:
    """从嵌套 dict 中按已知键优先级提取标签，兜底遍历所有值。"""
    results = []

    # 第1轮：已知的 value-only key（直接提取值）
    known_value_keys = (
        "type", "name", "label",
        "属性", "属性名称", "属性标签", "标签",
        "部位", "style_name", "style",
        "description", "描述",
        "Atmosphere", "Expression", "Leg_Posture"
    )
    for key in known_value_keys:
        v = value.get(key)
        if v is not None:
            results.extend(extract_tag_names(v))

    # 第2轮：pose/position 结构字段
    for key in ("pose", "position", "body_position", "orientation"):
        v = value.get(key)
        if v is not None:
            results.extend(extract_tag_names(v))

    # 第3轮：中文语境键（图片属性、季节等）
    for key in ("图片属性", "属性值", "穿着方式",
                 "穿着方式/身体部位关系"):
        v = value.get(key)
        if v is not None:
            results.extend(extract_tag_names(v))

    # 第4轮：兜底 — 遍历所有值，通过递归确保过滤一致
    # 处理 {"宽松/修身": "修身"} 或 {"上衣": "黑色长袖"} 等非标准键
    if not results:
        for k, v in value.items():
            # 跳过纯英文 boolean 键（如 {'face': True}）
            if isinstance(k, str) and not any("一" <= c <= "鿿" for c in k):
                if isinstance(v, bool):
                    continue
            # 所有值统一通过递归 extract_tag_names 处理，复用长度/标点过滤
            results.extend(extract_tag_names(v))

    return results


def extract_tag_names(value: Any) -> list[str]:
    """从任意 AI 返回值中递归提取标签名称字符串。

    AI 模型可能返回:
      - 纯字符串: "坐姿"
      - 嵌套 dict: {"type": "坐姿", "position": ["沙发"]}
      - 混合列表
    此函数递归提取所有有意义的字符串值。
    """
    if isinstance(value, str):
        return _extract_str_tag(value.strip() or "")

    if isinstance(value, (int, float, bool)):
        return []

    if isinstance(value, dict):
        return _extract_dict_tags(value)

    if isinstance(value, list):
        results = []
        for item in value:
            results.extend(extract_tag_names(item))
        return results

    return []
