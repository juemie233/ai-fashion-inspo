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
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.generation import GenerationRun
from app.models.inspiration import Inspiration
from app.models.tag import Tag
from app.models.task import TaskQueue
from app.services.comfyui import workflow as wf
from app.services.comfyui.models import MAX_COUNT, MAX_PIXELS, MAX_STEPS, MIN_STEPS
from app.services.comfyui.probe import MODEL_SPECS
from app.services.file_service import delete_files
from app.services.inspiration_create import create_inspiration_from_file
from app.utils.file_hash import file_sha256
from app.utils.time import format_utc, utcnow

logger = logging.getLogger(__name__)

"""参数硬上限（服务端校验，不信任前端）见 ``app.services.comfyui.models``：
那里是纯数据模块，命令行独立出图（``scripts/generate_image.py``）也读同一份，
避免「网页能设到 X、命令行只能到 Y」的漂移。
"""

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


def _params_of(run: GenerationRun) -> dict:
    """安全解析 params_json 快照。

    脏数据（非法 JSON / ``null`` / 数组）返回空 dict：生成历史是列表页，不能让
    单条坏记录把整个接口变成 500（任务执行器 image_gen 的 ``_params_of`` 同样兜底）。
    """
    try:
        value = json.loads(run.params_json or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


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
            "params": _params_of(run),
            "model_key": run.model_key,
            "workflow_name": run.workflow_name,
            "workflow_hash": run.workflow_hash,
            "total": run.total,
            "done": run.done,
            "failed": run.failed,
            "error": run.error,
            "task_id": run.task_id,
            "batch_dir": run.batch_dir,
            # 必须带时区（Z）：DB 存的是 naive UTC，裸 isoformat() 前端会当成本地时间，
            # UTC+8 下整批时间早 8 小时
            "created_at": format_utc(run.created_at),
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
    """删除批次：清掉候选目录并**删除记录**（不删已入库素材）。

    为什么删行而不是标记 cancelled：用户点「删除」的预期就是它从生成历史里消失。
    已入库的素材不受影响 —— ``inspirations.generation_run_id`` 是 ``ON DELETE SET NULL``，
    删行后它们变成独立素材（与 ``models/generation.py`` 模块注释描述一致）。
    """
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

    # 删行而不是置 cancelled：置状态的话历史里仍留着一条记录，用户会认为「没删掉」。
    # 先把引用本批次的素材显式解绑（generation_run_id -> NULL），**不依赖** FK 的
    # ON DELETE SET NULL：本库 inspirations 上曾出现重复外键，SQLite 按最后一条生效，
    # 靠反射赌行为不可靠；显式解绑在哪种建表结果下都正确，且素材照样保活。
    from sqlalchemy import update

    await db.execute(
        update(Inspiration)
        .where(Inspiration.generation_run_id == run.id)
        .values(generation_run_id=None)
    )
    await db.delete(run)
    await db.commit()
    return {"removed_files": removed, "status": "deleted"}


"""列表里每批最多带几张候选图（缩略图条）：再多也只是复制候选区"""
LIST_CANDIDATES_LIMIT = 4

"""缩略图宽度（基准）：历史列表用 256px，约 20 KB/张"""
THUMB_WIDTH = 256


def ensure_thumbnail(source: Path, width: int = THUMB_WIDTH) -> Path | None:
    """返回候选图的缩略图路径（存在则直接复用；不存在则生成）。

    同步函数（Pillow 解码 + 编码），调用方用 ``asyncio.to_thread`` 包起来。

    缓存失效判据是 **mtime 比较**：候选图文件被重生成时（同文件名换内容）mtime 会变，
    缩略图自动重建，不需要额外的版本号。

    Args:
        source: 原图路径（PNG）。
        width: 目标宽度（等比缩放，最长边不超过它）。

    Returns:
        缩略图路径；Pillow 解不开（文件损坏/不是图片）时返回 None，由调用方退回原图。
    """
    from PIL import Image  # 延迟导入：列表/创建批次等热路径不需要 Pillow

    thumb_dir = source.parent / ".thumbs"
    target = thumb_dir / f"{source.stem}_w{width}.jpg"
    try:
        if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
            return target
        thumb_dir.mkdir(parents=True, exist_ok=True)
        with Image.open(source) as image:
            rgb = image.convert("RGB")
            rgb.thumbnail((width, width))
            # quality=82：256px 缩略图约 20 KB，肉眼与 UI 用途足够
            rgb.save(target, "JPEG", quality=82, optimize=True)
        return target
    except Exception as exc:  # noqa: BLE001 —— 缩略图失败不能影响看图，退回原图
        logger.warning(f"生成缩略图失败（退回原图）：{source} → {exc}")
        return None


def _list_candidates(folder: Path) -> tuple[bool, list[str]]:
    """列出批次目录里的候选图文件名，并回报目录是否存在。

    为什么要回报「存在」：只凭「没文件」判断「图已清理」会冤枉排队中/生成中的批次
    （目录还没建或还没出图）。纯同步 IO，调用方放线程池。

    Returns:
        (目录是否存在, 候选图文件名列表（按名排序，不含 .thumbs 子目录）)。
    """
    if not folder.is_dir():
        return False, []
    return True, sorted(p.name for p in folder.glob(f"*{CANDIDATE_SUFFIX}"))


def thumb_url(run_id: int, filename: str) -> str:
    """候选图的缩略图地址（走同一个文件接口，加 w 参数触发缩略图）。"""
    return f"/api/generate/runs/{run_id}/candidates/{filename}?w={THUMB_WIDTH}"


"""批次终态：只有终态才谈得上「候选图被清理」"""
TERMINAL_STATUSES = ("done", "failed", "cancelled")


def _serialize_run(
    run: GenerationRun,
    *,
    candidates: list[str],
    imported_count: int,
    folder_exists: bool,
) -> dict:
    """把一行批次序列化成列表项（保留原有字段，另加 P0 三件套）。"""
    params = _params_of(run)
    # prompt / negative 已单独返回，params 里的副本不再重复（列表最多 100 条）
    params.pop("prompt", None)
    params.pop("negative", None)
    return {
        "id": run.id,
        "kind": run.kind,
        "status": run.status,
        "prompt": run.prompt,
        "negative": run.negative,
        "params": params,
        "model_key": run.model_key,
        "workflow_name": run.workflow_name,
        "total": run.total,
        "done": run.done,
        "failed": run.failed,
        "error": run.error,
        "created_at": format_utc(run.created_at),
        "updated_at": format_utc(run.updated_at),
        # ── 列表增强（P0 A1）：缩略图条 + 已入库张数 + 目录是否还在 ──
        "candidates": [{"filename": name, "thumb_url": thumb_url(run.id, name)} for name in candidates],
        "imported_count": imported_count,
        # 终态 + 目录不在 = 真被清理过；排队/生成中目录还没建，不能显示「图已清理」
        "files_missing": (not folder_exists) and run.status in TERMINAL_STATUSES,
        # ── 用户标注与保留期（P1）──
        "favorite": bool(run.favorite),
        "title": run.title,
        "note": run.note,
        # 保留期截止时间：收藏批次不清理，给 None（前端显示「已收藏，不自动清理」）
        "expires_at": None
        if run.favorite
        else format_utc(run.created_at + timedelta(days=settings.image_gen_keep_candidates_days)),
    }


async def _imported_counts(db: AsyncSession, run_ids: list[int]) -> dict[int, int]:
    """按批次统计已入库素材数（一条聚合 SQL；列表**不**逐文件算哈希）。"""
    if not run_ids:
        return {}
    rows = await db.execute(
        select(Inspiration.generation_run_id, func.count())
        .where(
            Inspiration.generation_run_id.in_(run_ids),
            Inspiration.deleted_at.is_(None),
        )
        .group_by(Inspiration.generation_run_id)
    )
    return {int(run_id): int(count) for run_id, count in rows.all()}


def _apply_run_filters(stmt, filters: dict):
    """把筛选条件翻译成 where 子句（列表与计数共用，避免两边口径漂移）。"""
    if filters.get("kind"):
        stmt = stmt.where(GenerationRun.kind == filters["kind"])
    if filters.get("model_key"):
        stmt = stmt.where(GenerationRun.model_key == filters["model_key"])
    if filters.get("status"):
        stmt = stmt.where(GenerationRun.status == filters["status"])
    if filters.get("q"):
        # 关键字同时匹配描述与负面词：用户记不清写在哪一边
        like = f"%{filters['q']}%"
        stmt = stmt.where(
            or_(GenerationRun.prompt.like(like), GenerationRun.negative.like(like))
        )
    if filters.get("reference_id"):
        # reference_ids 是 JSON 数组字符串；用带引号的 LIKE 命中完整 id，避免前缀误伤
        stmt = stmt.where(
            GenerationRun.reference_ids.like(f'%"{filters["reference_id"]}"%')
        )
    if filters.get("since"):
        stmt = stmt.where(GenerationRun.created_at >= filters["since"])
    if filters.get("until"):
        stmt = stmt.where(GenerationRun.created_at < filters["until"])
    if filters.get("favorite") is not None:
        stmt = stmt.where(GenerationRun.favorite == filters["favorite"])
    if filters.get("has_imported") is not None:
        imported_subq = select(Inspiration.generation_run_id).where(
            Inspiration.generation_run_id.is_not(None),
            Inspiration.deleted_at.is_(None),
        )
        if filters["has_imported"]:
            stmt = stmt.where(GenerationRun.id.in_(imported_subq))
        else:
            stmt = stmt.where(GenerationRun.id.not_in(imported_subq))
    return stmt


async def list_runs(
    db: AsyncSession,
    *,
    page: int = 1,
    size: int = 20,
    kind: str | None = None,
    model_key: str | None = None,
    status: str | None = None,
    has_imported: bool | None = None,
    q: str | None = None,
    reference_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    favorite: bool | None = None,
) -> dict:
    """生成历史（分页 + 筛选），供出图页历史区使用。

    为什么不用旧的 ``recent_runs`` 直接改成带参：它是「最近 N 条」的语义，仍有调用方；
    这里返回 ``{items, total, page, size}``，前端据此做服务端分页。

    Args:
        page: 页码（从 1 开始）。
        size: 每页条数。
        kind: t2i / i2i。
        model_key: 档位 key。
        status: 批次状态。
        has_imported: True 只看有入库的批次；False 只看没入库的。
        q: 关键字（匹配描述或负面词）。
        reference_id: 参考素材 id（精确命中 JSON 数组里的那一项）。
        since / until: 创建时间区间（左闭右开）。

    Returns:
        ``{"items": [...], "total": n, "page": p, "size": s}``；items 每项含
        ``candidates``（最多 4 张 + 缩略图地址）、``imported_count``、``files_missing``。
    """
    filters = {
        "kind": kind,
        "model_key": model_key,
        "status": status,
        "has_imported": has_imported,
        "q": (q or "").strip() or None,
        "reference_id": (reference_id or "").strip() or None,
        "since": since,
        "until": until,
        "favorite": favorite,
    }

    base = _apply_run_filters(select(GenerationRun), filters)
    total = (
        await db.execute(select(func.count()).select_from(base.subquery()))
    ).scalar() or 0

    page = max(1, page)
    size = max(1, size)
    rows = (
        await db.execute(
            base.order_by(GenerationRun.created_at.desc(), GenerationRun.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
    ).scalars().all()

    run_ids = [run.id for run in rows]
    imported_counts = await _imported_counts(db, run_ids)
    # 目录列举放线程池：单批几十毫秒，但 20 批串行会明显拖慢列表
    listed = await asyncio.gather(
        *[asyncio.to_thread(_list_candidates, _run_dir(run)) for run in rows]
    )

    items = [
        _serialize_run(
            run,
            candidates=filenames[:LIST_CANDIDATES_LIMIT],
            imported_count=imported_counts.get(run.id, 0),
            folder_exists=exists,
        )
        for run, (exists, filenames) in zip(rows, listed, strict=True)
    ]
    return {
        "items": items,
        "total": total,
        "page": page,
        "size": size,
        # 保留期天数：前端用它显示「图还剩 N 天」（收藏批次不清理）
        "keep_days": settings.image_gen_keep_candidates_days,
    }


async def update_run_meta(
    db: AsyncSession,
    run_id: int,
    *,
    favorite: bool | None = None,
    title: str | None = None,
    note: str | None = None,
    clear_title: bool = False,
    clear_note: bool = False,
) -> dict:
    """更新批次的用户标注（收藏 / 命名 / 备注）。

    为什么用「显式 clear_*」而不是 `None` 表示清空：PATCH 语义里 `None` 通常表示
    「本次不改」，要清空得给一个明确信号（否则前端没法把已填的备注删掉）。

    Args:
        favorite: 是否收藏（收藏批次不参与过期清理）。
        title: 新命名（≤64 字，空白按清空处理）。
        note: 新备注（≤2000 字，空白按清空处理）。
        clear_title / clear_note: 显式清空对应字段。

    Returns:
        更新后的批次摘要（与列表项同形，便于前端原地替换）。

    Raises:
        HTTPException: 404 批次不存在 / 400 字段超长。
    """
    run = await db.get(GenerationRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="出图批次不存在")

    if favorite is not None:
        run.favorite = bool(favorite)
    if clear_title:
        run.title = None
    elif title is not None:
        text = title.strip()
        if len(text) > 64:
            raise HTTPException(status_code=400, detail="命名最多 64 个字")
        run.title = text or None
    if clear_note:
        run.note = None
    elif note is not None:
        text = note.strip()
        if len(text) > 2000:
            raise HTTPException(status_code=400, detail="备注最多 2000 个字")
        run.note = text or None

    run.updated_at = utcnow()
    await db.commit()
    await db.refresh(run)

    imported = await _imported_counts(db, [run.id])
    exists, filenames = await asyncio.to_thread(_list_candidates, _run_dir(run))
    return _serialize_run(
        run,
        candidates=filenames[:LIST_CANDIDATES_LIMIT],
        imported_count=imported.get(run.id, 0),
        folder_exists=exists,
    )


async def run_stats(db: AsyncSession, days: int = 30) -> dict:
    """近 N 天的出图统计（历史区顶部数字卡）。

    - ``import_rate`` = 已入库张数 / 出图张数（按 **入库素材数** 而非批次数，
      因为一批可能只入库其中一张）；
    - ``avg_duration_s`` 用 `updated_at - created_at`（含排队等待，文案要写「含排队」）；
    - 一个批次都没有时返回全 0，不报错（新库/清空历史后前端正常显示空卡）。

    Args:
        days: 统计窗口（天），`<=0` 时按全部历史统计。

    Returns:
        ``{runs, images, imported_images, import_rate, avg_duration_s, failure_rate,
        by_model: [{model_key, runs, images}], days}``。
    """
    cutoff = utcnow() - timedelta(days=days) if days > 0 else None
    conditions = [GenerationRun.created_at >= cutoff] if cutoff else []

    rows = (await db.execute(select(GenerationRun).where(*conditions))).scalars().all()
    run_ids = [r.id for r in rows]

    images = sum(int(r.total or 0) for r in rows)
    imported_images = 0
    if run_ids:
        imported_images = (
            await db.execute(
                select(func.count())
                .select_from(Inspiration)
                .where(
                    Inspiration.generation_run_id.in_(run_ids),
                    Inspiration.deleted_at.is_(None),
                )
            )
        ).scalar() or 0

    durations = [
        (r.updated_at - r.created_at).total_seconds()
        for r in rows
        if r.updated_at and r.created_at
    ]
    failed = sum(1 for r in rows if r.status == "failed")
    by_model: dict[str, dict] = {}
    for run in rows:
        item = by_model.setdefault(run.model_key or "unknown", {"runs": 0, "images": 0})
        item["runs"] += 1
        item["images"] += int(run.total or 0)

    return {
        "days": days,
        "runs": len(rows),
        "images": images,
        "imported_images": imported_images,
        "import_rate": round(imported_images / images, 4) if images else 0,
        "avg_duration_s": round(sum(durations) / len(durations), 1) if durations else 0,
        "failure_rate": round(failed / len(rows), 4) if rows else 0,
        "by_model": [
            {"model_key": key, **value}
            for key, value in sorted(by_model.items(), key=lambda kv: -kv[1]["runs"])
        ],
    }


async def recent_runs(db: AsyncSession, limit: int = 20) -> list[dict]:
    """最近的出图批次（兼容旧调用：等价于 ``list_runs(size=limit)["items"]``）。

    返回**完整** prompt 与参数快照：前端历史表可展开看全文，此前截到 120 字会让
    展开行也看不到后半段。时间统一走 ``format_utc``（带 Z），否则前端
    ``new Date()`` 会把 naive UTC 当本地时间，UTC+8 下整体早 8 小时。
    """
    return (await list_runs(db, page=1, size=limit))["items"]


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

    params = _params_of(run)
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

    params = _params_of(run)
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
        ``{"scanned": 批次数, "removed": 删掉的目录数, "freed_dirs": [...],
        "skipped_favorites": 因收藏被跳过的批次数}``
    """
    keep_days = days if days is not None else settings.image_gen_keep_candidates_days
    if keep_days <= 0:
        return {"scanned": 0, "removed": 0, "skipped_favorites": 0}

    cutoff = utcnow() - timedelta(days=keep_days)
    # 收藏批次不清理：用户点星标就是「这批我要留着」；清理返回里单独回报跳过数，
    # 避免「为什么我的图还在/没了」无从解释
    expired = await db.execute(
        select(GenerationRun).where(
            GenerationRun.status.in_(("done", "failed", "cancelled")),
            GenerationRun.created_at < cutoff,
        )
    )
    expired_runs = expired.scalars().all()
    # 收藏批次不清理：用户点星标就是「这批我要留着」；返回里单独回报跳过数，
    # 避免「为什么我的图还在/没了」无从解释
    runs = [run for run in expired_runs if not run.favorite]
    skipped_favorites = len(expired_runs) - len(runs)
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
    return {
        "scanned": len(runs),
        "removed": len(removed),
        "skipped_favorites": skipped_favorites,
    }
