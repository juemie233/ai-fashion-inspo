"""AI 子路由。"""

import asyncio
import json
import logging
import re
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import case, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session, get_db
from app.models.inspiration import (
    AIAnalysisLog,
    Inspiration,
    analysis_log_filter as _analysis_log_filter,
)
from app.routers.ai_shared import (
    _analysis_semaphore,
    _active_analyses,
    _analysis_tasks,
    _task_by_id,
    _pending_queue,
    _queue_paused,
    _quality_active,
    _run_analysis,
    _run_quality_check,
    _update_env_file,
    _fmt_utc,
    _format_size,
)
from app.services.model_config import get_model_config, update_model_config
from app.utils.auth import require_api_key

logger = logging.getLogger(__name__)
router = APIRouter()


# ============ Prompt 版本管理 ============

# prompt 版本历史文件路径
_prompt_versions_file = Path(__file__).parent.parent.parent / "prompt_versions.json"


def _load_prompt_versions() -> list[dict]:
    """加载 prompt 版本历史。"""
    if _prompt_versions_file.exists():
        try:
            return json.loads(_prompt_versions_file.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_prompt_versions(versions: list[dict]):
    """保存 prompt 版本历史（保留最近 50 条）。"""
    _prompt_versions_file.write_text(
        json.dumps(versions[-50:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


@router.get("/prompt/versions")
async def prompt_versions():
    """获取 prompt 版本历史列表。"""
    versions = _load_prompt_versions()
    return {
        "versions": versions[::-1],  # 最新的在前
        "current": settings.ai_analysis_prompt[:100] + "...",
    }


@router.post("/prompt/rollback")
async def rollback_prompt(payload: dict):
    """回滚 prompt 到指定版本。请求体: {"index": 0} 其中 index 0 = 最新版本（与 GET /versions 顺序一致）"""
    versions = _load_prompt_versions()  # 按时间正序存储
    idx = payload.get("index", 0)
    # 前端发送的 index：0 = 最新 = versions 最后一个
    real_idx = len(versions) - 1 - idx
    if real_idx < 0 or real_idx >= len(versions):
        raise HTTPException(status_code=400, detail="无效的版本索引")
    prompt_text = versions[real_idx]["prompt"]
    settings.ai_analysis_prompt = prompt_text
    return {
        "message": f"已回滚到版本 #{idx + 1}",
        "prompt": prompt_text,
    }


@router.post("/prompt/save-version")
async def save_prompt_version():
    """将当前 prompt 保存为一个命名版本（用于后续回滚和对比）。"""
    versions = _load_prompt_versions()
    from datetime import datetime
    versions.append({
        "prompt": settings.ai_analysis_prompt,
        "saved_at": datetime.now().isoformat(),
        "length": len(settings.ai_analysis_prompt),
    })
    _save_prompt_versions(versions)
    return {"message": f"已保存版本 #{len(versions)}", "total_versions": len(versions)}


# ============ Prompt 管理 ============


@router.get("/prompt")
async def get_prompt():
    """获取当前 AI 分析使用的 prompt 文本。"""
    return {
        "prompt": settings.ai_analysis_prompt,
        "length": len(settings.ai_analysis_prompt),
    }


@router.put("/prompt")
async def update_prompt(
    body: dict,
):
    """更新 AI 分析 prompt（可选持久化到 backend/prompt.txt）。"""
    prompt = body.get("prompt", "")
    persist = body.get("persist", False)
    if not prompt:
        raise HTTPException(status_code=400, detail="请提供 prompt 文本")
    settings.ai_analysis_prompt = prompt

    if persist:
        try:
            prompt_file = Path(__file__).parent.parent.parent / "prompt.txt"

            def _write_prompt():
                prompt_file.write_text(prompt, encoding="utf-8")

            await asyncio.to_thread(_write_prompt)
            logger.info(f"Prompt 已持久化到 {prompt_file}")
        except Exception as e:
            logger.warning(f"持久化 prompt 失败: {e}")

    return {
        "message": "Prompt 已更新" + ("并持久化" if persist else "") + f"（{len(prompt)} 字符）",
    }



# ============ 参数调优 ============


@router.get("/settings")
async def get_ai_settings():
    """获取当前 AI 参数配置（超时按当前模型独立）。"""
    model_cfg = get_model_config(settings.ollama_vision_model)
    return {
        "active_model": settings.ollama_vision_model,
        "confidence_threshold": settings.ai_low_confidence_threshold,
        "analysis_timeout": model_cfg["timeout"],
        "outfit_summary_model": settings.outfit_summary_model,
        "ollama_base_url": settings.ollama_base_url,
    }


@router.put("/settings")
async def update_ai_settings(
    confidence_threshold: float | None = Query(None, ge=0, le=1),
    analysis_timeout: int | None = Query(None, ge=10, le=300),
    outfit_summary_model: str | None = Query(None, description="大标签总结模型"),
    persist: bool = Query(False, description="是否持久化写入 .env 文件"),
):
    """更新 AI 参数。

    超时按当前活跃模型独立保存到 model_configs.json；置信度阈值与大标签总结模型为全局设置。
    ``persist`` 参数已废弃（配置始终持久化），保留仅为兼容前端。
    """
    if confidence_threshold is not None:
        settings.ai_low_confidence_threshold = confidence_threshold
        await _update_env_file({"AI_LOW_CONFIDENCE_THRESHOLD": str(confidence_threshold)})

    if outfit_summary_model is not None:
        settings.outfit_summary_model = outfit_summary_model
        await _update_env_file({"OUTFIT_SUMMARY_MODEL": outfit_summary_model})

    timeout = get_model_config(settings.ollama_vision_model)["timeout"]
    if analysis_timeout is not None:
        cfg = await update_model_config(
            settings.ollama_vision_model, {"timeout": analysis_timeout}
        )
        timeout = cfg["timeout"]

    return {
        "message": "参数已更新",
        "confidence_threshold": settings.ai_low_confidence_threshold,
        "analysis_timeout": timeout,
        "outfit_summary_model": settings.outfit_summary_model,
    }


@router.get("/sampling-params")
async def get_sampling_params():
    """获取当前模型的 AI 采样参数（temperature, top_p, top_k, num_predict, think）。"""
    cfg = get_model_config(settings.ollama_vision_model)
    return {
        "temperature": cfg["temperature"],
        "top_p": cfg["top_p"],
        "top_k": cfg["top_k"],
        "num_predict": cfg["num_predict"],
        "think": cfg["think"],
    }


@router.put("/sampling-params")
async def update_sampling_params(
    temperature: float | None = Query(None, ge=0, le=2),
    top_p: float | None = Query(None, ge=0, le=1),
    top_k: int | None = Query(None, ge=1, le=100),
    num_predict: int | None = Query(None, ge=64, le=8192),
    think: bool | None = Query(None, description="是否开启思考模式（思考模型适用）"),
    persist: bool = Query(False),
):
    """更新当前模型的 AI 采样参数（按模型独立持久化）。"""
    updates = {}
    if temperature is not None:
        updates["temperature"] = temperature
    if top_p is not None:
        updates["top_p"] = top_p
    if top_k is not None:
        updates["top_k"] = top_k
    if num_predict is not None:
        updates["num_predict"] = num_predict
    if think is not None:
        updates["think"] = think

    cfg = get_model_config(settings.ollama_vision_model)
    if updates:
        cfg = await update_model_config(settings.ollama_vision_model, updates)

    return {
        "message": "采样参数已更新（按模型持久化）",
        "temperature": cfg["temperature"],
        "top_p": cfg["top_p"],
        "top_k": cfg["top_k"],
        "num_predict": cfg["num_predict"],
        "think": cfg["think"],
    }
