"""人物简介生成 Prompt 管理：单一全局 Prompt，持久化到 JSON。

与 ``model_prompt`` 的区别：
- 分析 Prompt 按模型隔离（不同视觉模型的指令偏好不同）；
- 人物简介 Prompt 只用于文本生成，不依赖具体模型，故全局一条即可。

存储于 ``backend/person_bio_prompt.json``，未配置时回退到
``DEFAULT_PERSON_BIO_PROMPT``。模板使用 ``str.format`` 占位符：

- ``{kind}``        ：人物类型中文称呼（穿搭博主 / 职业模特）
- ``{name}``        ：人物名称
- ``{platform}``    ：平台中文名称（小红书 / 抖音 / 其他）
- ``{ip_location}`` ：IP 属地（可能为空串）
- ``{top_tags}``    ：高频标签名列表，顿号拼接（可能为空串）
- ``{category_summary}``：按标签类别聚合的文本（可能为空串）
"""

import asyncio
import json
from pathlib import Path

_PROMPT_FILE = Path(__file__).resolve().parent.parent.parent / "person_bio_prompt.json"

# 串行化读-改-写，避免并发更新互相覆盖
_write_lock = asyncio.Lock()

# 默认模板：60~120 字，客观、克制、可直接展示在人物详情页。
# 不允许编造粉丝数/身高/年龄等未提供的数据；不使用表情、# 话题标签、
# Markdown 列表或引导关注的话术，保证展示在卡片上时排版稳定。
DEFAULT_PERSON_BIO_PROMPT = """你是一名时尚穿搭内容编辑。请根据下面提供的标签数据，为一位{kind}写一段 60~120 字的中文简介。

人物信息：
- 昵称：{name}
- 主要平台：{platform}
- IP 属地：{ip_location}
- 高频标签：{top_tags}
- 标签类别分布：{category_summary}

要求：
1. 用第三人称、客观语气，概括其常见穿搭风格、偏好的单品/配色/场景；
2. 若标签信息不足，基于已有标签做合理概括，不要编造身高、年龄、职业、粉丝数等未提供的事实；
3. 不要使用表情符号、# 话题标签、Markdown 列表、「关注我」「点击主页」之类的互动话术；
4. 直接输出简介正文，不要加任何前缀、引号或解释。"""


def _read_persisted() -> str | None:
    """读取已持久化的 Prompt；文件不存在/损坏/为空时返回 None。"""
    if not _PROMPT_FILE.exists():
        return None
    try:
        data = json.loads(_PROMPT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return None
    return prompt


def get_person_bio_prompt() -> str:
    """返回当前生效的人物简介生成 Prompt（未配置则回退默认模板）。"""
    return _read_persisted() or DEFAULT_PERSON_BIO_PROMPT


async def set_person_bio_prompt(prompt: str) -> None:
    """保存人物简介生成 Prompt。

    仅做非空校验；占位符由调用方在渲染时捕获 ``KeyError`` 以提示用户修正。
    """
    prompt = prompt.strip()
    if not prompt:
        raise ValueError("Prompt 不能为空")

    async with _write_lock:
        def _write() -> None:
            _PROMPT_FILE.write_text(
                json.dumps({"prompt": prompt}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        await asyncio.to_thread(_write)


def reset_person_bio_prompt() -> None:
    """删除持久化文件，回退到默认模板（用于「恢复默认」按钮）。"""
    try:
        _PROMPT_FILE.unlink()
    except FileNotFoundError:
        pass
