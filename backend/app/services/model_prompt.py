"""模型级 Prompt 管理：每个模型拥有独立的分析 Prompt，完全隔离。

Prompt 存储于 ``backend/prompt_configs.json``，键为模型名（如 ``qwen3-vl:8b-instruct``）。
未显式配置的模型回退到全局默认 ``settings.ai_analysis_prompt``。
"""

import asyncio
import json
from pathlib import Path

from app.config import settings

_PROMPT_FILE = Path(__file__).resolve().parent.parent.parent / "prompt_configs.json"

# 串行化读-改-写，避免并发更新互相覆盖
_write_lock = asyncio.Lock()

# 读缓存（按文件 mtime 失效重载）：get_model_prompt 位于 AI 分析高频路径
_cache: dict = {"mtime": None, "data": None}


def _load() -> dict[str, str]:
    """读取 Prompt 配置（按 mtime 缓存）；文件不存在或损坏时返回空字典。"""
    if not _PROMPT_FILE.exists():
        _cache["mtime"] = None
        _cache["data"] = None
        return {}
    try:
        mtime = _PROMPT_FILE.stat().st_mtime
    except OSError:
        return _cache["data"] or {}
    if _cache["mtime"] == mtime and _cache["data"] is not None:
        return _cache["data"]
    try:
        data = json.loads(_PROMPT_FILE.read_text(encoding="utf-8"))
        data = data if isinstance(data, dict) else {}
    except Exception:
        data = {}
    _cache["mtime"] = mtime
    _cache["data"] = data
    return data


def get_model_prompt(model_name: str) -> str:
    """返回指定模型的分析 Prompt（未配置则用全局默认）。"""
    return _load().get(model_name) or settings.ai_analysis_prompt


async def set_model_prompt(model_name: str, prompt: str) -> None:
    """保存指定模型的分析 Prompt（持久化到 prompt_configs.json）。"""
    async with _write_lock:
        data = _load()
        data[model_name] = prompt

        def _write() -> None:
            _PROMPT_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        await asyncio.to_thread(_write)


def get_all_model_prompts() -> dict[str, str]:
    """返回所有有自定义 Prompt 的模型（键为模型名）。"""
    return _load()


# Prompt 版本历史文件（由 AI 设置页的「保存版本 / 回滚」接口写入，
# 多模型组合分析读取该文件把历史版本 Prompt 解析为文本用于分析）
_VERSIONS_FILE = Path(__file__).resolve().parent.parent.parent / "prompt_versions.json"


def get_prompt_versions(model_name: str) -> list[dict]:
    """读取指定模型的 Prompt 版本历史（与 AI 设置页共用 prompt_versions.json）。

    返回按保存顺序排列的版本列表（最早的为 #1），每项含
    ``prompt`` / ``saved_at`` / ``length`` 字段；文件不存在或损坏时返回空列表。
    """
    if not _VERSIONS_FILE.exists():
        return []
    try:
        data = json.loads(_VERSIONS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            versions = data.get(model_name, [])
        else:
            versions = []  # 兼容旧的平铺格式（不按模型隔离的已废弃格式）
        return versions if isinstance(versions, list) else []
    except Exception:
        return []


async def copy_model_prompt(source: str, destination: str) -> None:
    """将源模型的 Prompt 复制到目标模型（无自定义 Prompt 则忽略）。"""
    async with _write_lock:
        data = _load()
        if source in data:
            data[destination] = data[source]

            def _write() -> None:
                _PROMPT_FILE.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                )

            await asyncio.to_thread(_write)
