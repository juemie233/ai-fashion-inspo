"""出图任务执行器（``image_gen`` 类型，由 worker 通过 TASK_HANDLERS 调度）。

一次任务 = 一个 ``GenerationRun``（同参数、多张图）。执行顺序：

1. **显存守门**：等 ComfyUI 的 ``vram_free`` 够用（决策 3，见 ``comfyui/vram.py``）；
2. 逐张：注入模板 → ``/prompt`` → 轮询 → 取图 → 落盘 → 更新进度并 commit；
3. 收尾：``run.status`` 置终态，失败时写入**人话**错误（前端直接展示）。

三个必须守住的约定：

- **每张落盘后立刻 commit**：中途崩溃/取消时已出的图仍在候选区可用（用户不会白等）；
- **候选图绝不自动入库**：入库必须由用户点「确认入库」（``generation_service.confirm_import``）；
- **失败要给可操作的原因**：ComfyUI 未启动 / 模型缺失 / 显存不足 / 超时，四类文案分开。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.generation import GenerationRun
from app.models.task import TaskQueue
from app.services.comfyui import workflow as wf
from app.services.comfyui.client import ComfyUIClient, ComfyUIError
from app.services.comfyui.probe import MODEL_SPECS
from app.services.comfyui.vram import wait_for_vram
from app.services.task_runners.common import PermanentTaskError

logger = logging.getLogger(__name__)

"""单张图的文件名模板：序号 + seed（seed 编进文件名 = 无需 per-image 表也能复现）。"""
_CANDIDATE_NAME = "{idx:02d}_seed-{seed}.png"


def _candidate_dir(run: GenerationRun) -> Path:
    """候选图目录（``storage/generated/<run_id>``）。"""
    return settings.storage_root / run.batch_dir


def _friendly_error(exc: ComfyUIError) -> str:
    """把客户端异常翻译成用户能照做的一句话。"""
    if exc.kind == "not_running":
        return "ComfyUI 未启动：双击 D:\\AI\\ComfyUI\\run_nvidia_gpu.bat 后重试"
    if exc.kind == "oom":
        return "显存不足：请关闭浏览器标签/其它占显存的程序，或先执行 ollama stop <模型>"
    if exc.kind == "vram_timeout":
        return str(exc)
    if exc.kind == "timeout":
        return f"生成超时：{exc}（可降低分辨率或张数后重试）"
    if exc.kind == "invalid":
        return f"工作流与当前 ComfyUI/模型不匹配：{exc}"
    return f"生成失败：{exc}"


async def _load_run(db: AsyncSession, task: TaskQueue) -> GenerationRun:
    """取任务关联的批次；缺失说明数据被外部清理，任务直接判永久失败。"""
    result = task.result if isinstance(task.result, dict) else {}
    run_id = int(result.get("run_id") or 0)
    run = await db.get(GenerationRun, run_id) if run_id else None
    if run is None:
        raise PermanentTaskError("出图批次不存在（可能已被清理），请重新提交")
    return run


def _params_of(run: GenerationRun) -> dict:
    try:
        return json.loads(run.params_json or "{}")
    except json.JSONDecodeError:
        return {}


async def _mark_stage(
    db: AsyncSession, task: TaskQueue, run: GenerationRun, stage: str, **extra
) -> None:
    """把阶段写进 ``task.result``（前端据此显示「等待显存」等），并同步 run 状态。"""
    result = dict(task.result or {})
    result["stage"] = stage
    result.update(extra)
    task.result = result
    if stage == "waiting_vram":
        run.status = "waiting_vram"
    await db.commit()


async def _is_cancelled(db: AsyncSession, task: TaskQueue) -> bool:
    """轮询期间复查任务状态（取消/暂停）。

    只读 ``status``：暂停在「当前这张图结束后」生效（中断半张图没有意义，
    反而会浪费已经跑掉的步数）。
    """
    await db.refresh(task)
    return task.status in ("cancelled", "paused")


def _pick_image(entry: dict) -> dict | None:
    """从 history 的 outputs 里挑出第一张生成图。"""
    for output in (entry.get("outputs") or {}).values():
        images = output.get("images") or []
        if images:
            return images[0]
    return None


async def execute_image_gen(db: AsyncSession, task: TaskQueue) -> None:
    """执行一次出图任务（worker 调用）。"""
    run = await _load_run(db, task)
    spec = MODEL_SPECS.get(run.model_key)
    if spec is None:
        raise PermanentTaskError(f"未知出图模型档位: {run.model_key}")

    params = _params_of(run)
    seeds = list(params.get("seeds") or [])
    if not seeds:
        raise PermanentTaskError("出图参数缺少 seed 列表，请重新提交")

    client = ComfyUIClient(
        settings.comfyui_base_url,
        timeout=settings.comfyui_timeout,
        poll_interval=settings.image_gen_poll_interval,
    )
    try:
        template, _digest = wf.load_template(run.workflow_name)
    except FileNotFoundError as exc:
        raise PermanentTaskError(f"工作流模板缺失: {run.workflow_name}") from exc

    out_dir = _candidate_dir(run)
    out_dir.mkdir(parents=True, exist_ok=True)

    run.status = "running"
    run.error = None
    await _mark_stage(db, task, run, "checking_vram")

    # ── 1) 显存守门 ──
    async def _on_wait(waited: int, free: float) -> None:
        await _mark_stage(
            db, task, run, "waiting_vram", waited_seconds=waited, vram_free_gib=free
        )

    try:
        await wait_for_vram(
            client,
            spec["vram_gib"],
            wait_seconds=settings.image_gen_wait_vram_seconds,
            on_wait=_on_wait,
        )
    except ComfyUIError as exc:
        run.status = "failed"
        run.error = _friendly_error(exc)
        await db.commit()
        raise PermanentTaskError(run.error) from exc

    # ── 2) 逐张生成 ──
    for idx, seed in enumerate(seeds, start=1):
        if await _is_cancelled(db, task):
            run.status = "cancelled"
            run.error = "已取消"
            await db.commit()
            return

        await _mark_stage(db, task, run, "generating", current_index=idx, current_seed=seed)
        try:
            workflow = wf.inject(
                template,
                PROMPT=run.prompt,
                SEED=seed,
                WIDTH=int(params.get("width") or 1024),
                HEIGHT=int(params.get("height") or 1024),
                STEPS=int(params.get("steps") or 8),
                CFG=float(params.get("cfg") or 1.0),
                SAMPLER=str(params.get("sampler") or "res_multistep"),
                SCHEDULER=str(params.get("scheduler") or "simple"),
                SHIFT=float(params.get("shift") or 3.0),
                PREFIX=f"gen_{run.id}_{idx:02d}",
            )
            prompt_id = await client.submit(workflow)

            async def _tick() -> bool:
                return await _is_cancelled(db, task)

            entry = await client.wait_for_prompt(
                prompt_id,
                timeout=settings.image_gen_timeout,
                on_tick=_tick,
            )
            image = _pick_image(entry)
            if image is None:
                raise ComfyUIError("ComfyUI 未返回生成结果", kind="invalid")
            data = await client.fetch_image(
                image.get("filename") or "", image.get("subfolder") or ""
            )
            target = out_dir / _CANDIDATE_NAME.format(idx=idx, seed=seed)
            target.write_bytes(data)
        except ComfyUIError as exc:
            run.failed += 1
            run.error = _friendly_error(exc)
            logger.warning(f"出图失败（第 {idx} 张）: {run.error} / {exc.detail}")
            # 已出的图保留，任务判失败（用户可只重试失败的这张）
            run.status = "failed"
            await db.commit()
            raise PermanentTaskError(run.error) from exc

        run.done += 1
        task.done = run.done
        task.progress = int(run.done * 100 / max(1, run.total))
        await _mark_stage(db, task, run, "generating", current_index=idx)

    run.status = "done"
    task.progress = 100
    await _mark_stage(db, task, run, "done", batch_dir=run.batch_dir)
    logger.info(f"出图完成: run #{run.id}，{run.done}/{run.total} 张")
