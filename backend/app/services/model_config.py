"""模型级配置管理：为每个模型保存独立的采样参数与超时，实现配置隔离。

配置存储于 ``backend/model_configs.json``，键为模型名（如 ``qwen3-vl:8b-thinking``）。
未显式配置的模型回退到 ``.env`` / ``config.py`` 中的全局默认值。

设计目的：思考型模型（如 qwen3-vl:8b-thinking）推理慢、token 消耗大，
需要更大的超时与 num_predict；普通模型（如 qwen3-vl:8b-instruct）则用更紧凑的值。
"""

import asyncio
import json
from pathlib import Path
from typing import Any

from app.config import settings

_CONFIG_FILE = Path(__file__).resolve().parent.parent.parent / "model_configs.json"

# 串行化读-改-写，避免并发更新互相覆盖
_write_lock = asyncio.Lock()


def _defaults() -> dict[str, Any]:
    """全局默认配置（来自 .env / config.py）。"""
    return {
        "timeout": settings.ai_analysis_timeout,
        "num_predict": settings.ai_num_predict,
        # 上下文窗口：必须显式传给 Ollama，默认 4096 会截断视觉模型输出
        "num_ctx": settings.ai_num_ctx,
        "temperature": settings.ai_temperature,
        "top_p": settings.ai_top_p,
        "top_k": settings.ai_top_k,
        "think": False,
    }


def _load() -> dict[str, dict[str, Any]]:
    """读取配置文件；文件不存在或损坏时返回空字典。"""
    if not _CONFIG_FILE.exists():
        return {}
    try:
        data = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get_model_config(model_name: str) -> dict[str, Any]:
    """返回指定模型的完整配置（全局默认值 + 文件覆盖）。"""
    cfg = _defaults()
    cfg.update(_load().get(model_name, {}))
    return cfg


async def update_model_config(
    model_name: str, updates: dict[str, Any]
) -> dict[str, Any]:
    """更新指定模型的配置并持久化，返回更新后的完整配置。

    参数:
        model_name: 模型名（作为配置键）
        updates: 要更新的字段（值为 None 的字段会被忽略）

    返回:
        更新后的完整配置字典
    """
    async with _write_lock:
        data = _load()
        model_cfg = data.setdefault(model_name, {})
        model_cfg.update({k: v for k, v in updates.items() if v is not None})

        def _write() -> None:
            _CONFIG_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        await asyncio.to_thread(_write)
    return get_model_config(model_name)


async def reset_model_config(model_name: str) -> dict[str, Any]:
    """删除指定模型的全部自定义配置（回退到全局默认值），返回默认配置。

    参数:
        model_name: 模型名（配置键）

    返回:
        全局默认配置字典
    """
    async with _write_lock:
        data = _load()
        if model_name in data:
            del data[model_name]

            def _write() -> None:
                _CONFIG_FILE.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                )

            await asyncio.to_thread(_write)
    return get_model_config(model_name)
