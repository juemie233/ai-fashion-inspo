"""本地出图（ComfyUI）的 REST 接口。

设计约束（见 docs/ComfyUI出图接入方案.md 第 6 节）：

- 前端**不直连** ComfyUI（8188 无鉴权），所有交互经这里；
- 提交立即返回 ``run_id + task_id``，跑图在 worker 里异步执行；
- 候选图读取走本路由的 `/candidates/{filename}`，文件名做白名单校验（防路径穿越）；
- 不做任何不可恢复删除：`DELETE /runs/{id}` 只删本批次自己生成的候选文件，
  已入库素材不受影响（所以不进破坏性接口清单）。
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.generation import GenerationRun
from app.schemas.generation import (
    GenerationConfirm,
    GenerationRunCreate,
    PromptPreviewRequest,
)
from app.services import generation_service
from app.services.comfyui import probe as probe_service
from app.services.comfyui import prompt_builder

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/generate", tags=["generate"])


@router.get("/status")
async def generate_status() -> dict:
    """就绪状态：ComfyUI 是否可用、哪些模型就位、Ollama 占了显存。

    前端据此决定「运行」按钮是否可点，并在不可用时给出中文引导（不是点了才失败）。
    """
    status = await probe_service.probe()
    data = status.to_dict()
    data["models_meta"] = {
        key: {
            "label": spec["label"],
            "kind": spec["kind"],
            "defaults": spec["defaults"],
        }
        for key, spec in probe_service.MODEL_SPECS.items()
    }
    return data


@router.get("/models")
async def generate_models() -> dict:
    """可用模型档位与默认参数（前端填默认值、显示预期耗时）。"""
    return {
        "items": [
            {
                "key": key,
                "label": spec["label"],
                "kind": spec["kind"],
                "defaults": spec["defaults"],
                "max_pixels": spec.get("max_pixels"),
                "vram_gib": spec["vram_gib"],
            }
            for key, spec in probe_service.MODEL_SPECS.items()
        ],
        "limits": {
            "max_count": generation_service.MAX_COUNT,
            "max_pixels": generation_service.MAX_PIXELS,
            "max_steps": generation_service.MAX_STEPS,
        },
    }


@router.post("/runs")
async def create_run(
    payload: GenerationRunCreate, db: AsyncSession = Depends(get_db)
) -> dict:
    """提交出图批次（立即返回，任务在 worker 里执行）。"""
    return await generation_service.create_run(db, payload.model_dump())


@router.get("/runs")
async def list_runs(
    limit: int = Query(20, ge=1, le=100), db: AsyncSession = Depends(get_db)
) -> dict:
    """最近的出图批次。"""
    return {"items": await generation_service.recent_runs(db, limit)}


@router.get("/runs/{run_id}")
async def get_run(run_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """批次详情 + 候选图列表（含是否已入库）。"""
    return await generation_service.list_candidates(db, run_id)


@router.get("/runs/{run_id}/candidates/{filename}")
async def get_candidate(run_id: int, filename: str, db: AsyncSession = Depends(get_db)) -> FileResponse:
    """读取候选图文件（缩略图/预览用）。"""
    run = await db.get(GenerationRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="出图批次不存在")
    # 白名单：只允许纯文件名（拒绝 ../ 与绝对路径）
    safe_name = Path(filename).name
    if safe_name != filename or not safe_name.endswith(".png"):
        raise HTTPException(status_code=400, detail="非法的文件名")
    path = settings.storage_root / run.batch_dir / safe_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="候选图不存在")
    return FileResponse(path, media_type="image/png")


@router.post("/runs/{run_id}/confirm")
async def confirm_import(
    run_id: int, payload: GenerationConfirm, db: AsyncSession = Depends(get_db)
) -> dict:
    """把选中的候选图入库（生成图来源=generated，标记疑似 AI 生成，默认待审核）。"""
    return await generation_service.confirm_import(
        db,
        run_id,
        payload.filenames,
        tag_ids=payload.tag_ids,
        caption_from_prompt=payload.caption_from_prompt,
    )


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """取消批次：把队列任务标记为 cancelled（已出的图保留在候选区）。"""
    run = await db.get(GenerationRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="出图批次不存在")
    if run.task_id:
        from app.models.task import TaskQueue

        task = await db.get(TaskQueue, run.task_id)
        if task is not None and task.status in ("pending", "running", "paused"):
            task.status = "cancelled"
    run.status = "cancelled"
    await db.commit()
    return {"run_id": run.id, "status": run.status}


@router.delete("/runs/{run_id}")
async def discard_run(run_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """丢弃批次：删除候选目录（不影响已入库素材）。"""
    return await generation_service.discard_run(db, run_id)


@router.post("/prompt/preview")
async def preview_prompt(
    payload: PromptPreviewRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    """从素材的标签/正文/主色反推出图 prompt（分段返回，前端可逐段删改）。"""
    return await prompt_builder.build_prompt(db, payload.inspiration_ids)
