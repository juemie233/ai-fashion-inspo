"""AI 子路由。"""

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.routers.ai_shared import _update_env_file
from app.services.model_config import (
    copy_model_config,
    get_all_model_configs,
    get_model_config,
    reset_model_config,
    update_model_config,
)
from app.services.model_prompt import (
    copy_model_prompt,
    get_all_model_prompts,
    get_model_prompt,
    set_model_prompt,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ============ Prompt 版本管理 ============

# prompt 版本历史文件路径
_prompt_versions_file = Path(__file__).parent.parent.parent / "prompt_versions.json"


def _load_prompt_versions(model_name: str) -> list[dict]:
    """加载指定模型的 prompt 版本历史。"""
    if not _prompt_versions_file.exists():
        return []
    try:
        data = json.loads(_prompt_versions_file.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data.get(model_name, [])
        return data if isinstance(data, list) else []  # 兼容旧的平铺格式
    except Exception:
        return []


def _save_prompt_versions(model_name: str, versions: list[dict]) -> None:
    """保存指定模型的 prompt 版本历史（保留最近 50 条）。"""
    data = {}
    if _prompt_versions_file.exists():
        try:
            data = json.loads(_prompt_versions_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
    data[model_name] = versions[-50:]
    _prompt_versions_file.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _active_model() -> str:
    return settings.ollama_vision_model


@router.get("/prompt/versions")
async def prompt_versions() -> dict:
    """获取当前模型的 prompt 版本历史列表。"""
    versions = _load_prompt_versions(_active_model())
    current = get_model_prompt(_active_model())
    return {
        "versions": versions[::-1],  # 最新的在前
        "current": current[:100] + "...",
    }


@router.post("/prompt/rollback")
async def rollback_prompt(payload: dict) -> dict[str, str]:
    """回滚当前模型的 prompt 到指定版本。请求体: {"index": 0} 其中 index 0 = 最新版本"""
    versions = _load_prompt_versions(_active_model())
    idx = payload.get("index", 0)
    real_idx = len(versions) - 1 - idx
    if real_idx < 0 or real_idx >= len(versions):
        raise HTTPException(status_code=400, detail="无效的版本索引")
    prompt_text = versions[real_idx]["prompt"]
    await set_model_prompt(_active_model(), prompt_text)
    return {
        "message": f"已回滚到版本 #{idx + 1}",
        "prompt": prompt_text,
    }


@router.post("/prompt/save-version")
async def save_prompt_version() -> dict[str, str | int]:
    """将当前模型的 prompt 保存为一个版本（用于回滚和对比）。"""
    versions = _load_prompt_versions(_active_model())
    from datetime import datetime
    prompt_text = get_model_prompt(_active_model())
    versions.append({
        "prompt": prompt_text,
        "saved_at": datetime.now().isoformat(),
        "length": len(prompt_text),
    })
    _save_prompt_versions(_active_model(), versions)
    return {"message": f"已保存版本 #{len(versions)}", "total_versions": len(versions)}


# ============ Prompt 管理 ============


@router.get("/prompt")
async def get_prompt() -> dict[str, str | int]:
    """获取当前模型 AI 分析使用的 prompt 文本。"""
    prompt = get_model_prompt(_active_model())
    return {
        "prompt": prompt,
        "length": len(prompt),
        "model": _active_model(),
    }


@router.put("/prompt")
async def update_prompt(
    body: dict,
) -> dict[str, str]:
    """更新当前模型的 AI 分析 prompt（按模型持久化到 prompt_configs.json）。"""
    prompt = body.get("prompt", "")
    if not prompt:
        raise HTTPException(status_code=400, detail="请提供 prompt 文本")
    await set_model_prompt(_active_model(), prompt)
    return {
        "message": f"Prompt 已更新（模型 {_active_model()}，{len(prompt)} 字符）",
    }



# ============ 参数调优 ============


@router.get("/settings")
async def get_ai_settings() -> dict:
    """获取当前 AI 参数配置（超时按当前模型独立），附全局默认值供前端「恢复默认」。"""
    model_cfg = get_model_config(settings.ollama_vision_model)
    return {
        "active_model": settings.ollama_vision_model,
        "confidence_threshold": settings.ai_low_confidence_threshold,
        "analysis_timeout": model_cfg["timeout"],
        "ollama_base_url": settings.ollama_base_url,
        "defaults": {
            "confidence_threshold": settings.ai_low_confidence_threshold,
            "analysis_timeout": settings.ai_analysis_timeout,
        },
    }


@router.put("/settings")
async def update_ai_settings(
    confidence_threshold: float | None = Query(None, ge=0, le=1),
    analysis_timeout: int | None = Query(None, ge=10, le=300),
) -> dict:
    """更新 AI 参数。

    超时按当前活跃模型独立保存到 model_configs.json；置信度阈值为全局设置
    （写入 .env）。配置始终持久化，无需前端「持久化」开关。
    """
    if confidence_threshold is not None:
        settings.ai_low_confidence_threshold = confidence_threshold
        await _update_env_file({"AI_LOW_CONFIDENCE_THRESHOLD": str(confidence_threshold)})

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
    }


@router.get("/sampling-params")
async def get_sampling_params() -> dict:
    """获取当前模型的 AI 采样参数（temperature, top_p, top_k, num_predict, num_ctx, think）。

    响应附 ``defaults``（.env 全局默认值），供前端「恢复默认值」与「清除覆盖」使用。
    """
    cfg = get_model_config(settings.ollama_vision_model)
    return {
        "temperature": cfg["temperature"],
        "top_p": cfg["top_p"],
        "top_k": cfg["top_k"],
        "num_predict": cfg["num_predict"],
        "num_ctx": cfg["num_ctx"],
        "think": cfg["think"],
        "defaults": {
            "temperature": settings.ai_temperature,
            "top_p": settings.ai_top_p,
            "top_k": settings.ai_top_k,
            "num_predict": settings.ai_num_predict,
            "num_ctx": settings.ai_num_ctx,
            "think": False,
        },
    }


@router.put("/sampling-params")
async def update_sampling_params(
    temperature: float | None = Query(None, ge=0, le=2),
    top_p: float | None = Query(None, ge=0, le=1),
    top_k: int | None = Query(None, ge=1, le=100),
    num_predict: int | None = Query(None, ge=64, le=8192),
    num_ctx: int | None = Query(None, ge=1024, le=131072, description="上下文窗口大小（视觉模型图片 token 消耗大）"),
    think: bool | None = Query(None, description="是否开启思考模式（思考模型适用）"),
) -> dict:
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
    if num_ctx is not None:
        updates["num_ctx"] = num_ctx
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
        "num_ctx": cfg["num_ctx"],
        "think": cfg["think"],
    }


@router.delete("/model-config")
async def reset_model_config_endpoint() -> dict:
    """清除当前活跃模型的自定义配置（model_configs.json 中的覆盖项）。

    回退到 .env 全局默认值。用于「该模型改乱后想恢复默认」的场景，
    与「恢复默认值按钮只改表单」不同，本接口直接删除持久化覆盖。
    """
    cfg = await reset_model_config(settings.ollama_vision_model)
    return {
        "message": f"已清除模型 '{settings.ollama_vision_model}' 的自定义配置，恢复全局默认值",
        "config": cfg,
    }


@router.get("/model-config/overview")
async def model_config_overview() -> dict:
    """返回每模型的参数/Prompt 自定义配置总览（哪些模型有覆盖、覆盖哪些字段）。"""
    configs = get_all_model_configs()
    prompts = get_all_model_prompts()
    model_names = sorted(set(configs.keys()) | set(prompts.keys()))
    return {
        "models": [
            {
                "name": name,
                "has_config": name in configs,
                "config_fields": sorted(configs.get(name, {}).keys()),
                "has_prompt": name in prompts,
                "prompt_length": len(prompts.get(name, "")),
            }
            for name in model_names
        ]
    }


@router.post("/model-config/copy")
async def copy_model_config_endpoint(payload: dict) -> dict[str, str]:
    """把某模型的参数与 Prompt 复制到另一模型。请求体: {"source": ..., "destination": ...}"""
    source = payload.get("source", "")
    destination = payload.get("destination", "")
    if not source or not destination:
        raise HTTPException(status_code=400, detail="请提供 source 与 destination")
    if source == destination:
        raise HTTPException(status_code=400, detail="源模型与目标模型不能相同")

    await copy_model_config(source, destination)
    await copy_model_prompt(source, destination)
    return {"message": f"已将 '{source}' 的配置复制到 '{destination}'"}
