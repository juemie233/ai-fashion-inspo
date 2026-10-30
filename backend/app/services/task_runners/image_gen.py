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

import asyncio
import json
import logging
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.generation import GenerationRun
from app.models.inspiration import Inspiration
from app.models.task import TaskQueue
from app.services.comfyui import workflow as wf
from app.services.comfyui.client import ComfyUIClient, ComfyUIError
from app.services.comfyui.probe import MODEL_SPECS
from app.services.comfyui.vram import wait_for_vram
from app.services import pose_gallery
from app.services.task_runners.common import PermanentTaskError

logger = logging.getLogger(__name__)

"""单张图的文件名模板：序号 + seed（seed 编进文件名 = 无需 per-image 表也能复现）。"""
_CANDIDATE_NAME = "{idx:02d}_seed-{seed}.png"


def _candidate_dir(run: GenerationRun) -> Path:
    """候选图目录（``storage/generated/<run_id>``）。"""
    return settings.storage_root / run.batch_dir


def _friendly_error(exc: ComfyUIError) -> str:
    """把客户端异常翻译成用户能照做的一句话。"""
    if exc.kind == "cancelled":
        return "已取消"
    if exc.kind == "http_timeout":
        return f"ComfyUI 响应超时（连接正常，但接口没在超时时间内返回）：{exc}"
    if exc.kind == "not_running":
        return "ComfyUI 未启动：请先启动 ComfyUI（运行 run_nvidia_gpu.bat）后重试"
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

    # 同一份模板可能服务多个档位（标准版 / 未审查版）：权重文件名由档位覆盖，
    # 覆盖失败按永久错误处理 —— 绝不能退回模板默认权重继续跑，那会静默出「另一版」的图
    try:
        template = wf.apply_file_overrides(template, spec.get("files"))
    except KeyError as exc:
        raise PermanentTaskError(f"档位 {run.model_key} 与模板 {run.workflow_name} 不符：{exc}") from exc

    out_dir = _candidate_dir(run)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        await _execute_with_client(db, task, run, spec, params, seeds, template, client, out_dir)
    finally:
        await client.aclose()


async def _execute_with_client(
    db: AsyncSession,
    task: TaskQueue,
    run: GenerationRun,
    spec: dict,
    params: dict,
    seeds: list[int],
    template: dict,
    client: ComfyUIClient,
    out_dir: Path,
) -> None:
    """真正的执行体（拆出来只为让 :func:`execute_image_gen` 能 finally 关连接池）。"""
    run.status = "running"
    run.error = None
    await _mark_stage(db, task, run, "checking_vram")

    # ── 1) 显存守门 ──
    # 取消必须能穿透「等待显存」：默认最长等 15 分钟，期间若无人检查状态，
    # 用户点了取消却要等满 15 分钟才有反应。vram.wait_for_vram 提供 should_stop，
    # 但它是同步回调 —— 用 on_wait（异步）更新的标志位喂给它。
    stop_requested = False

    async def _on_wait(waited: int, free: float) -> None:
        nonlocal stop_requested
        stop_requested = await _is_cancelled(db, task)
        await _mark_stage(
            db, task, run, "waiting_vram", waited_seconds=waited, vram_free_gib=free
        )

    try:
        await wait_for_vram(
            client,
            spec["vram_gib"],
            wait_seconds=settings.image_gen_wait_vram_seconds,
            on_wait=_on_wait,
            should_stop=lambda: stop_requested,
        )
    except ComfyUIError as exc:
        # 「等待期间被取消」不是失败：记成 cancelled，历史里才不会显示一条 failed
        if stop_requested or "已取消" in str(exc):
            run.status = "cancelled"
            run.error = "已取消（等待显存期间）"
            await db.commit()
            return
        run.status = "failed"
        run.error = _friendly_error(exc)
        await db.commit()
        raise PermanentTaskError(run.error) from exc

    # ── 1.5) 图生图：把参考图先上传到 ComfyUI（每批只传一次）──
    reference_names: list[str] = []
    if run.kind == "i2i":
        ref_ids = json.loads(run.reference_ids or "[]")
        if not ref_ids:
            raise PermanentTaskError("图生图缺少参考素材，请重新提交")
        for idx, insp_id in enumerate(ref_ids, start=1):
            insp = await db.get(Inspiration, insp_id)
            if insp is None or insp.deleted_at is not None:
                raise PermanentTaskError(f"参考素材已不存在或被删除：{insp_id}")
            source = settings.storage_root / insp.file_path
            if not source.exists():
                raise PermanentTaskError(f"参考素材文件缺失：{insp.file_path}")
            data = await asyncio.to_thread(source.read_bytes)
            uploaded = await client.upload_image(
                data, f"gen_ref_{run.id}_{idx}{source.suffix.lower() or '.png'}"
            )
            reference_names.append(uploaded)
        logger.info(f"参考图已上传 {len(reference_names)} 张（run #{run.id}）")

    # ── 1.6) 换姿势：姿势图作为**最后一张**参考图（提示词约定「第 1 张是主体、最后一张是姿势」）──
    # 姿势图不属于素材库（不是 Inspiration），按需从姿势图库下载到 storage 缓存再上传；
    # 下载失败按永久错误处理（源站不可用重试也不会好），并给用户可执行的原因。
    pose_id = str(params.get("pose_id") or "").strip()
    if pose_id:
        try:
            pose_path = await pose_gallery.ensure_pose_image(pose_id)
        except HTTPException as exc:
            raise PermanentTaskError(str(exc.detail)) from exc
        pose_data = await asyncio.to_thread(pose_path.read_bytes)
        uploaded_pose = await client.upload_image(pose_data, f"gen_pose_{run.id}.png")
        reference_names.append(uploaded_pose)
        logger.info(f"姿势参考已上传（run #{run.id}，pose={pose_id}）")

    # ── 2) 逐张生成 ──
    # 任务允许重试一次（max_retries=1）。若重试时从头重跑全部 seed，`run.done` 会继续累加、
    # 最终超过 `total`（进度 >100%），已成功的图也会被再生成一遍（纯浪费）。
    # 这里按「候选目录里已有第 N 张」判断：跳过已出的，计数器据实重建。
    done_before = 0
    for idx in range(1, len(seeds) + 1):
        if (out_dir / _CANDIDATE_NAME.format(idx=idx, seed=seeds[idx - 1])).exists():
            done_before += 1
    run.done = done_before
    run.failed = 0

    for idx, seed in enumerate(seeds, start=1):
        if idx <= done_before:
            logger.info(f"跳过已出的第 {idx} 张（run #{run.id}，重试续跑）")
            continue
        if await _is_cancelled(db, task):
            run.status = "cancelled"
            run.error = "已取消"
            await db.commit()
            return

        await _mark_stage(db, task, run, "generating", current_index=idx, current_seed=seed)
        try:
            common = dict(
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
            if run.kind == "i2i":
                workflow = wf.build_edit_workflow(
                    template,
                    reference_names=reference_names,
                    NEGATIVE=run.negative or "",
                    RESOLUTION=int(params.get("width") or 1024),
                    **{k: v for k, v in common.items() if k not in ("WIDTH", "HEIGHT")},
                )
            else:
                workflow = wf.inject(template, **common)
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
            try:
                target.write_bytes(data)
            except OSError as exc:
                # 目录可能在生成期间被「丢弃」删掉 → 给出可操作的原因，
                # 而不是让 worker 记一条看不出所以然的未知异常
                raise ComfyUIError(
                    f"写入候选图失败（候选目录可能已被丢弃）：{exc}", kind="http"
                ) from exc
        except ComfyUIError as exc:
            # 用户取消 ≠ 失败：记成 cancelled，生成历史里才不会出现一条假的 failed
            if exc.kind == "cancelled":
                run.status = "cancelled"
                run.error = "已取消"
                await db.commit()
                return
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
