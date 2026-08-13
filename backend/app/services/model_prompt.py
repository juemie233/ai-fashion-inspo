"""模型级 Prompt 管理：每个模型拥有独立的分析 Prompt，完全隔离。

Prompt 存储于 ``backend/prompt_configs.json``，键为模型名（如 ``qwen3-vl:8b-instruct``）。
未显式配置的模型回退到全局默认 ``settings.ai_analysis_prompt``。
"""

import asyncio
import json
from pathlib import Path

from app.config import settings

_PROMPT_FILE = Path(__file__).resolve().parent.parent.parent / "prompt_configs.json"


def _load() -> dict[str, str]:
    """读取 Prompt 配置；文件不存在或损坏时返回空字典。"""
    if not _PROMPT_FILE.exists():
        return {}
    try:
        data = json.loads(_PROMPT_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get_model_prompt(model_name: str) -> str:
    """返回指定模型的分析 Prompt（未配置则用全局默认）。"""
    return _load().get(model_name) or settings.ai_analysis_prompt


async def set_model_prompt(model_name: str, prompt: str) -> None:
    """保存指定模型的分析 Prompt（持久化到 prompt_configs.json）。"""
    data = _load()
    data[model_name] = prompt

    def _write() -> None:
        _PROMPT_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    await asyncio.to_thread(_write)
