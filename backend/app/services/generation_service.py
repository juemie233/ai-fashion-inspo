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
from datetime import timedelta
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.generation import GenerationRun
from app.models.inspiration import Inspiration
from app.models.tag import Tag
from app.models.task import TaskQueue
from app.services.comfyui import workflow as wf
from app.services.comfyui.probe import MODEL_SPECS
from app.services.file_service import delete_files
from app.services.inspiration_create import create_inspiration_from_file
from app.utils.file_hash import file_sha256
from app.utils.time import utcnow

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
    # 上限按档位算：Z-Image 可到 1328²，Qwen-Image 2.1 只有 1024²（权重更大）
    limit = min(MAX_PIXELS, int(spec.get("max_pixels") or MAX_PIXELS))
    if width * height > limit:
        side = int(limit**0.5)
        raise HTTPException(
            status_code=400,
            detail=f"尺寸过大：当前模型宽×高不能超过 {limit}（约 {side}×{side}），否则会显存不足",
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


async def _rollback_created(
    db: AsyncSession, created: list[tuple[str, str | None]]
) -> None:
    """回滚本次已创建的入库：先删磁盘文件，再回滚事务。

    顺序很关键——DB 行还在事务里（未提交），先回滚就不知道要删哪些文件了；
    而文件先删掉不会造成「有行没文件」的悬空素材（行随回滚一起消失）。
    """
    if not created:
        await db.rollback()
        return
    for file_path, thumb_path in created:
        try:
            delete_files(file_path, thumb_path)
        except Exception as exc:  # noqa: BLE001 —— 清理失败不能掩盖原始异常
            logger.warning(f"回滚入库时删除文件失败（忽略）{file_path}: {exc}")
    await db.rollback()


async def _assert_references_exist(db: AsyncSession, ids: list[str]) -> None:
    """校验参考素材存在、未删除、是图片、且文件还在。

    任何一条不满足都在**提交前**报错：跑到一半才发现缺图，用户已经等了几十秒。
    """
    rows = await db.execute(
        select(Inspiration.id, Inspiration.file_path, Inspiration.media_type, Inspiration.deleted_at)
        .where(Inspiration.id.in_(ids))
    )
    found = {r[0]: r for r in rows.all()}
    for insp_id in ids:
        row = found.get(insp_id)
        if row is None or row[3] is not None:
            raise HTTPException(status_code=400, detail=f"参考素材不存在或已删除：{insp_id}")
        if row[2] != "image":
            raise HTTPException(status_code=400, detail=f"参考素材必须是图片：{insp_id}")
        if not (settings.storage_root / row[1]).exists():
            raise HTTPException(status_code=400, detail=f"参考素材文件缺失：{insp_id}")


async def create_run(
    db: AsyncSession, payload: dict, *, task_factory=None
) -> dict:
    """创建出图批次并提交任务，返回 ``{run_id, task_id, seed, batch_dir}``。

    Args:
        payload: 前端提交的表单（见 API 契约）。
        task_factory: 建任务的可调用对象（测试注入用，缺省用任务队列的服务函数）。
    """
    kind_hint = str(payload.get("kind") or "t2i")
    default_model = "qwen_2_1_i2i" if kind_hint == "i2i" else "z_image_int8"
    model_key = str(payload.get("model_key") or default_model)
    spec = MODEL_SPECS.get(model_key)
    if spec is None:
        raise HTTPException(status_code=400, detail=f"未知模型档位: {model_key}")

    kind = str(payload.get("kind") or "t2i")
    if kind not in ("t2i", "i2i"):
        raise HTTPException(status_code=400, detail="kind 只能是 t2i 或 i2i")

    # 档位与生成方式必须匹配：否则会拿编辑模板去跑「文生图」（缺参考图 → 500）
    spec_kind = str(spec.get("kind") or "t2i")
    if spec_kind != kind:
        raise HTTPException(
            status_code=400,
            detail=(
                f"档位 {model_key} 是「{'参考图改款' if spec_kind == 'i2i' else '文生图'}」模型，"
                f"与当前生成方式不符：请把 kind 改为 {spec_kind}，或换用对应档位"
            ),
        )

    ref_ids = [str(x) for x in (payload.get("reference_ids") or []) if str(x).strip()]
    if kind == "i2i":
        max_refs = int(spec.get("max_refs") or 10)
        if not 1 <= len(ref_ids) <= max_refs:
            raise HTTPException(
                status_code=400, detail=f"图生图需要 1~{max_refs} 张参考素材，当前 {len(ref_ids)} 张"
            )
        await _assert_references_exist(db, ref_ids)
    elif ref_ids:
        # 显式报错而不是静默丢弃：静默丢弃会让用户以为「按这张图生成了」，
        # 结果却是纯文字从零画出来的图 —— 这类"看起来成功了"的错最坑
        raise HTTPException(
            status_code=400,
            detail=(
                "文生图不接受参考素材：如需按参考图生成，请把生成方式切换为"
                "「参考图改款」（kind=i2i）"
            ),
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
        reference_ids=json.dumps(ref_ids) if kind == "i2i" else None,
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

    # 任务侧信息：进度百分比与 result 里的 stage / waited_seconds。
    # 「等待显存」的秒数只存在于 task.result（run.params 是提交时的参数快照，不会有它），
    # 不返回这里前端就只能显示 0 秒（曾出现的问题）。
    task_info: dict | None = None
    if run.task_id:
        task = await db.get(TaskQueue, run.task_id)
        if task is not None:
            task_info = {
                "id": task.id,
                "status": task.status,
                "progress": task.progress,
                "result": task.result if isinstance(task.result, dict) else {},
            }

    return {
        "task": task_info,
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
    # 已丢弃的批次：候选目录已被删，逐张都会走「文件不存在」skipped 却不报错 ——
    # 用户以为入库成功。这里提前拦成 400（running/queued 的半批入库是既有用法，不在此改变）。
    if run.status == "cancelled":
        raise HTTPException(status_code=400, detail="本批已丢弃，候选图已清理，无法入库")

    if tag_ids:
        # 无效标签 id 会在 flush 时撞外键抛 IntegrityError（500），提前拦成 400
        rows = await db.execute(select(Tag.id).where(Tag.id.in_(tag_ids)))
        known = {r[0] for r in rows.all()}
        missing = [t for t in tag_ids if t not in known]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"标签不存在：{', '.join(str(t) for t in missing)}",
            )

    folder = _run_dir(run)
    imported_ids: list[str] = []
    # 本次已入库素材的 (file_path, thumbnail_path)：任一后续条目失败时要把它们的文件删掉，
    # 否则「文件已复制、DB 行随事务回滚」→ 磁盘上留孤儿图（找不到、也占空间）
    created_files: list[tuple[str, str | None]] = []
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
            created_files.append((insp.file_path, insp.thumbnail_path))
        except HTTPException as exc:
            if exc.status_code == 409:
                skipped.append({"filename": name, "reason": "已存在（内容重复）"})
                continue
            await _rollback_created(db, created_files)
            raise
        except Exception:
            await _rollback_created(db, created_files)
            raise

    # commit 自身也可能失败（约束冲突/磁盘/锁超时）：此时文件已复制、DB 行随回滚消失，
    # 必须同样走 _rollback_created，否则磁盘上留下找不到的孤儿图（循环内异常已单独处理）。
    try:
        await db.commit()
    except Exception:
        await _rollback_created(db, created_files)
        raise
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

    # 执行中的批次不能丢目录：runner 还在往这个目录写，删了会撞 FileNotFoundError
    # 把任务搞成「未知异常」，而批次状态已经是 cancelled，两边对不上。
    if run.status in ("queued", "running", "waiting_vram"):
        raise HTTPException(
            status_code=400,
            detail="本批仍在生成中：请先取消，等任务停止后再丢弃",
        )
    if run.task_id:
        task = await db.get(TaskQueue, run.task_id)
        if task is not None and task.status in ("pending", "running"):
            raise HTTPException(
                status_code=400, detail="任务尚未停止（排队中/执行中），请先取消再丢弃"
            )

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


async def retry_run(db: AsyncSession, run_id: int, seed: int = 0) -> dict:
    """同参数再生成一次（可指定 seed 复现/变体）。

    刻意**新建一个批次**而不是复用原批次：生成历史保留「哪次参数产出了哪张图」，
    复用会让原批次的 done/total 与候选文件互相覆盖，等于丢掉可复现性。

    Args:
        seed: 0 = 随机新 seed；>0 则用它（复现某张图）。
    """
    run = await db.get(GenerationRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="出图批次不存在")

    params = json.loads(run.params_json or "{}")
    payload = {
        "kind": run.kind,
        "prompt": run.prompt,
        "negative": run.negative,
        "width": params.get("width"),
        "height": params.get("height"),
        "steps": params.get("steps"),
        "cfg": params.get("cfg"),
        "count": params.get("count") or run.total or 1,
        "model_key": run.model_key,
        "reference_ids": json.loads(run.reference_ids or "[]"),
        "seed": seed,
    }
    return await create_run(db, payload)


async def regenerate_candidate(
    db: AsyncSession, run_id: int, filename: str, seed: int = 0
) -> dict:
    """对**单张**候选图换 seed 重出（新建一个只含 1 张的批次）。

    为什么单独一个入口而不是复用 ``retry_run``：整批重跑会重出全部 4 张，而用户通常
    只对其中一张不满意（构图差/手崩了）。单张重出把成本压到 6.8 s（Z-Image），
    也避免旧批次里已入库的那几张被重新生成一遍。

    Args:
        run_id: 原批次 id。
        filename: 候选图文件名（形如 ``02_seed-123456.png``）；用于取出原 seed。
        seed: 新 seed；0 = 随机（不能沿用原 seed，否则出同一张）。

    Returns:
        与 :func:`create_run` 同结构（新批次）。
    """
    run = await db.get(GenerationRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="出图批次不存在")

    # 原 seed 只用于提示，不参与生成（沿用就等于重出同一张图）
    original_seed = 0
    stem = Path(filename).stem
    _, _, seed_str = stem.partition("_seed-")
    original_seed = int(seed_str) if seed_str.isdigit() else 0

    params = json.loads(run.params_json or "{}")
    payload = {
        "kind": run.kind,
        "prompt": run.prompt,
        "negative": run.negative,
        "width": params.get("width"),
        "height": params.get("height"),
        "steps": params.get("steps"),
        "cfg": params.get("cfg"),
        "count": 1,
        "model_key": run.model_key,
        "reference_ids": json.loads(run.reference_ids or "[]"),
        "seed": seed,
    }
    out = await create_run(db, payload)
    out["original_seed"] = original_seed
    out["source_run_id"] = run_id
    return out


async def cleanup_expired_runs(db: AsyncSession, days: int | None = None) -> dict:
    """清理过期候选目录（不动已入库素材、不删历史记录）。

    规则：终态批次（done/failed/cancelled）且创建时间早于 ``days`` 天的，删掉它的
    候选目录；数据库行保留——生成历史仍可查，只是图不在地上了（反正用户没确认入库）。

    Args:
        days: 保留天数（缺省取 ``settings.image_gen_keep_candidates_days``）。

    Returns:
        ``{"scanned": 批次数, "removed": 删掉的目录数, "freed_dirs": [...]}``
    """
    keep_days = days if days is not None else settings.image_gen_keep_candidates_days
    if keep_days <= 0:
        return {"scanned": 0, "removed": 0}

    cutoff = utcnow() - timedelta(days=keep_days)
    rows = await db.execute(
        select(GenerationRun).where(
            GenerationRun.status.in_(("done", "failed", "cancelled")),
            GenerationRun.created_at < cutoff,
        )
    )
    runs = rows.scalars().all()
    removed: list[str] = []
    for run in runs:
        folder = _run_dir(run)
        if not folder.exists():
            continue
        # 删除前再确认目录落在 storage_root 内（batch_dir 是从 DB 读出来的，防写坏）
        try:
            if str(folder.resolve()).startswith(str(settings.storage_root.resolve())):
                await asyncio.to_thread(shutil.rmtree, folder, True)
                removed.append(run.batch_dir)
        except OSError as exc:
            logger.warning(f"清理候选目录失败（忽略）{folder}: {exc}")
    if removed:
        logger.info(f"[出图清理] 删除 {len(removed)} 个过期候选目录（保留 {keep_days} 天）")
    return {"scanned": len(runs), "removed": len(removed)}
