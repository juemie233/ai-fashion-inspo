"""出图批次的业务层：建批次、列候选、确认入库、丢弃。

与任务执行器的分工：

- 本模块管**用户可见的动作**（提交/查看/入库/丢弃），全部在请求内完成，快；
- 真正跑图在 ``task_runners/image_gen.py``，通过任务队列异步执行。

「候选区」是这里的核心概念：图先生成到 ``storage/generated/<run_id>/``，
用户在界面上勾选后才复制进素材库。这样废图不会占库容、不会被统计、也不进打标队列。
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import shutil
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.generation import GenerationRun
from app.models.inspiration import Inspiration
from app.models.task import TaskQueue
from app.services.comfyui import workflow as wf
from app.services.comfyui.probe import MODEL_SPECS
from app.services.inspiration_create import create_inspiration_from_file
from app.utils.file_hash import file_sha256

logger = logging.getLogger(__name__)

"""参数硬上限（服务端校验，不信任前端）。

依据是 16 GB 显存的实测：Z-Image int8 在 2560×1440 峰值已达 15.2 GiB，
再往上必然 OOM；张数限制 4 是为了别让一个任务长时间占着 GPU 互斥组。
"""

MAX_PIXELS = 1328 * 1328
MAX_COUNT = 4
MAX_STEPS = 30
MIN_STEPS = 1

"""候选图文件名形态（与执行器保持一致）。"""
CANDIDATE_SUFFIX = ".png"


def _int_or(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_or(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _validate_params(payload: dict, spec: dict) -> dict:
    """校验并归一化出图参数。

    Raises:
        HTTPException(400): 任一参数越界（消息直接给用户看，必须具体）。
    """
    width = _int_or(payload.get("width"), spec["defaults"]["width"])
    height = _int_or(payload.get("height"), spec["defaults"]["height"])
    if width < 256 or height < 256:
        raise HTTPException(status_code=400, detail="尺寸过小：宽高最小 256")
    if width * height > min(MAX_PIXELS, int(spec.get("max_pixels") or MAX_PIXELS)):
        raise HTTPException(
            status_code=400,
            detail=f"尺寸过大：宽×高不能超过 {MAX_PIXELS}（约 1328×1328），否则会显存不足",
        )

    count = _int_or(payload.get("count"), 1)
    if not 1 <= count <= MAX_COUNT:
        raise HTTPException(status_code=400, detail=f"单次生成张数需在 1~{MAX_COUNT} 之间")

    steps = _int_or(payload.get("steps"), spec["defaults"]["steps"])
    if not MIN_STEPS <= steps <= MAX_STEPS:
        raise HTTPException(
            status_code=400, detail=f"步数需在 {MIN_STEPS}~{MAX_STEPS} 之间"
        )

    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt 不能为空")
    if len(prompt) > 2000:
        raise HTTPException(status_code=400, detail="prompt 过长（上限 2000 字）")

    seed_in = _int_or(payload.get("seed"), 0)
    seed_base = seed_in if seed_in > 0 else random.randint(1, 2**31 - 1)

    return {
        "width": width,
        "height": height,
        "steps": steps,
        "cfg": _float_or(payload.get("cfg"), spec["defaults"]["cfg"]),
        "sampler": str(payload.get("sampler") or spec["defaults"]["sampler"]),
        "scheduler": str(payload.get("scheduler") or spec["defaults"]["scheduler"]),
        "shift": _float_or(payload.get("shift"), spec["defaults"]["shift"]),
        "count": count,
        "seed_base": seed_base,
        "seeds": [seed_base + i for i in range(count)],
        "prompt": prompt,
        "negative": str(payload.get("negative") or "").strip() or None,
        "reference_ids": list(payload.get("reference_ids") or []),
    }


async def create_run(
    db: AsyncSession, payload: dict, *, task_factory=None
) -> dict:
    """创建出图批次并提交任务，返回 ``{run_id, task_id, seed, batch_dir}``。

    Args:
        payload: 前端提交的表单（见 API 契约）。
        task_factory: 建任务的可调用对象（测试注入用，缺省用任务队列的服务函数）。
    """
    model_key = str(payload.get("model_key") or "z_image_int8")
    spec = MODEL_SPECS.get(model_key)
    if spec is None:
        raise HTTPException(status_code=400, detail=f"未知模型档位: {model_key}")

    kind = str(payload.get("kind") or "t2i")
    if kind not in ("t2i", "i2i"):
        raise HTTPException(status_code=400, detail="kind 只能是 t2i 或 i2i")
    if kind == "i2i":
        # 图生图需要 Qwen-Image 档位；一期尚未接入，明确拒绝而不是静默降级成文生图
        raise HTTPException(
            status_code=400,
            detail="图生图（参考图）尚未开放：需先安装并接入 Qwen-Image 2.1 档位",
        )

    params = _validate_params(payload, spec)
    template, digest = wf.load_template(spec["workflow"])

    run = GenerationRun(
        kind=kind,
        status="queued",
        prompt=params["prompt"],
        negative=params["negative"],
        params_json=json.dumps(params, ensure_ascii=False),
        model_key=model_key,
        workflow_name=spec["workflow"],
        workflow_hash=digest,
        reference_ids=None,
        batch_dir="",
        total=params["count"],
        done=0,
        failed=0,
    )
    db.add(run)
    await db.flush()  # 拿到自增 id，batch_dir 才能定
    run.batch_dir = f"generated/{run.id}"
    await db.flush()

    factory = task_factory or _default_task_factory
    task = await factory(db, run, params)
    run.task_id = task.id
    await db.commit()

    return {
        "run_id": run.id,
        "task_id": task.id,
        "seed": params["seed_base"],
        "batch_dir": run.batch_dir,
        "total": params["count"],
    }


async def _default_task_factory(
    db: AsyncSession, run: GenerationRun, params: dict
) -> TaskQueue:
    """建 ``image_gen`` 任务（总张数、run_id、seeds 进 task.result）。"""
    task = TaskQueue(
        type="image_gen",
        status="pending",
        progress=0,
        total=params["count"],
        done=0,
        result={"run_id": run.id, "seeds": params["seeds"]},
        max_retries=1,  # 出图幂等（重复生成只是多花时间），允许重试一次
    )
    db.add(task)
    await db.flush()
    return task


def _run_dir(run: GenerationRun) -> Path:
    return settings.storage_root / run.batch_dir


async def _imported_hashes(db: AsyncSession, run_id: int) -> set[str]:
    """该批次里已入库素材的内容哈希集合（判断候选图是否已入库）。"""
    rows = await db.execute(
        select(Inspiration.content_hash).where(
            Inspiration.generation_run_id == run_id,
            Inspiration.content_hash.is_not(None),
        )
    )
    return {h for h in rows.scalars().all() if h}


async def list_candidates(db: AsyncSession, run_id: int) -> dict:
    """列出批次里的候选图（含是否已入库）。

    已入库判定用**内容哈希**而不是文件名：入库时文件会被复制并改成随机名，
    文件名信息会丢；哈希是唯一可靠的对应关系（也与入库去重同一口径）。
    """
    run = await db.get(GenerationRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="出图批次不存在")

    folder = _run_dir(run)
    imported = await _imported_hashes(db, run_id)
    items: list[dict] = []
    if folder.exists():
        for path in sorted(folder.glob(f"*{CANDIDATE_SUFFIX}")):
            digest = await asyncio.to_thread(file_sha256, path)
            # 文件名形如 01_seed-123456.png：解析出序号与 seed 供「单张重生成」
            stem = path.stem
            idx_str, _, seed_str = stem.partition("_seed-")
            items.append(
                {
                    "filename": path.name,
                    "index": _int_or(idx_str, 0),
                    "seed": _int_or(seed_str, 0),
                    "size_bytes": path.stat().st_size,
                    "imported": bool(digest and digest in imported),
                    "url": f"/api/generate/runs/{run_id}/candidates/{path.name}",
                }
            )

    return {
        "run": {
            "id": run.id,
            "kind": run.kind,
            "status": run.status,
            "prompt": run.prompt,
            "negative": run.negative,
            "params": json.loads(run.params_json or "{}"),
            "model_key": run.model_key,
            "workflow_name": run.workflow_name,
            "workflow_hash": run.workflow_hash,
            "total": run.total,
            "done": run.done,
            "failed": run.failed,
            "error": run.error,
            "task_id": run.task_id,
            "batch_dir": run.batch_dir,
            "created_at": run.created_at.isoformat() if run.created_at else None,
        },
        "items": items,
    }


async def confirm_import(
    db: AsyncSession,
    run_id: int,
    filenames: list[str],
    *,
    tag_ids: list[int] | None = None,
    caption_from_prompt: bool = True,
) -> dict:
    """把选中的候选图入库（幂等：已入库的按「跳过」返回，不报错）。"""
    run = await db.get(GenerationRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="出图批次不存在")
    if not filenames:
        raise HTTPException(status_code=400, detail="请至少选择一张图")

    folder = _run_dir(run)
    imported_ids: list[str] = []
    skipped: list[dict] = []
    for name in filenames:
        # 只允许批次目录内的文件名，防路径穿越（../ 之类）
        path = folder / Path(name).name
        if not path.exists():
            skipped.append({"filename": name, "reason": "文件不存在"})
            continue
        try:
            insp = await create_inspiration_from_file(
                db,
                src_path=path,
                source_type="generated",
                generation_run_id=run.id,
                is_ai_generated=True,
                tag_ids=tag_ids,
                caption=run.prompt if caption_from_prompt else None,
            )
            imported_ids.append(insp.id)
        except HTTPException as exc:
            if exc.status_code == 409:
                skipped.append({"filename": name, "reason": "已存在（内容重复）"})
                continue
            raise

    await db.commit()
    return {
        "imported": len(imported_ids),
        "skipped": len(skipped),
        "ids": imported_ids,
        "skipped_items": skipped,
    }


async def discard_run(db: AsyncSession, run_id: int) -> dict:
    """丢弃批次：删除候选目录（**不删已入库素材**），并标记批次为 cancelled。"""
    run = await db.get(GenerationRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="出图批次不存在")

    folder = _run_dir(run)
    removed = 0
    if folder.exists():
        removed = len(list(folder.glob(f"*{CANDIDATE_SUFFIX}")))
        # 目录是我们自己按 run_id 建的，删前再确认一次前缀（防御性：batch_dir 可被写坏）
        if str(folder.resolve()).startswith(str(settings.storage_root.resolve())):
            await asyncio.to_thread(shutil.rmtree, folder, True)

    run.status = "cancelled"
    await db.commit()
    return {"removed_files": removed, "status": run.status}


async def recent_runs(db: AsyncSession, limit: int = 20) -> list[dict]:
    """最近的出图批次（生成历史列表）。"""
    rows = await db.execute(
        select(GenerationRun).order_by(GenerationRun.created_at.desc()).limit(limit)
    )
    out: list[dict] = []
    for run in rows.scalars().all():
        out.append(
            {
                "id": run.id,
                "kind": run.kind,
                "status": run.status,
                "prompt": run.prompt[:120],
                "model_key": run.model_key,
                "total": run.total,
                "done": run.done,
                "failed": run.failed,
                "error": run.error,
                "created_at": run.created_at.isoformat() if run.created_at else None,
            }
        )
    return out
