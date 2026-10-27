"""批量分析执行器（batch_analyze / multi_analyze）的测试。

这是「打标」的批处理入口，两个最贵的坑都在这里：

1. **重复分析**：崩溃恢复要跳过已成功素材；并发任务之间也要互相豁免对方认领的素材。
   漏了就会对同一张图反复调 Ollama（12.5 秒/张），日志也会重复堆积。
2. **暂停/取消的响应点**：只在批次边界检查状态，所以检查条件必须覆盖
   `paused` / `cancelled` / `pending`（pause→resume 竞态会变回 pending）。
   只认 paused 会让「暂停后立刻恢复」的任务卡死——界面显示 pending、没有暂停按钮、
   执行器却还在跑。

AI 分析本身（`analyze_image`/`analyze_video`）在单测里用替身，这里只验编排与状态机。
"""

import asyncio

import pytest

from app.database import async_session
from app.models.inspiration import AIAnalysisLog, Inspiration
from app.models.task import TaskQueue
from app.services.task_runners import batch_analyze as ba
from app.services.task_runners.common import PermanentTaskError, RecoverableTaskError


# ── 造数据 ──


async def _insp(insp_id: str, *, media_type: str = "image") -> str:
    async with async_session() as db:
        db.add(
            Inspiration(
                id=insp_id,
                source_type="douyin",
                file_path=f"images/x/{insp_id}.jpg" if media_type == "image" else f"videos/{insp_id}.mp4",
                media_type=media_type,
                quality_status="approved",
            )
        )
        await db.commit()
    return insp_id


async def _log(insp_id: str, *, error: str | None = None, model: str = "m1") -> int:
    async with async_session() as db:
        log = AIAnalysisLog(
            inspiration_id=insp_id, model_name=model, log_type="analysis", error=error
        )
        db.add(log)
        await db.commit()
        return log.id


async def _task(
    *, type_: str = "batch_analyze", status: str = "running", ids: list[str] | None = None
) -> int:
    """只返回任务 id：执行时必须用「执行会话」重新加载对象，否则 commit/refresh 不生效。"""
    async with async_session() as db:
        task = TaskQueue(
            type=type_,
            status=status,
            progress=0,
            total=len(ids or []),
            done=0,
            result={"inspiration_ids": ids or []},
        )
        db.add(task)
        await db.commit()
        return task.id


@pytest.fixture(autouse=True)
def _fast_runner(monkeypatch):
    """并发 1、广播静默、日志不动真格地刷屏。"""

    async def _noop_broadcast(task, event, **kwargs):
        return None

    monkeypatch.setattr(ba, "_broadcast_task_event", _noop_broadcast)
    monkeypatch.setattr(ba, "_analyze_concurrency", lambda: 1)
    return None


# ── 建任务 ──


async def test_create_batch_task_payload_and_retry_flag():
    async with async_session() as db:
        task = await ba.create_batch_analyze_task(
            db, ["a", "b"], skipped=3, force_retry=True
        )

    assert task.type == "batch_analyze"
    assert task.status == "pending"
    assert task.total == 2
    assert task.max_retries == 2
    assert task.result["inspiration_ids"] == ["a", "b"]
    assert task.result["skipped"] == 3
    assert task.result["retry"] is True


async def test_create_multi_task_total_is_product_of_items_and_combos():
    """组合分析的总量 = 素材数 × 组合数（进度分母不能只算素材数）。"""
    combos = [{"model": "m1", "prompt": None}, {"model": "m2", "prompt": "p"}]
    async with async_session() as db:
        task = await ba.create_multi_analyze_task(db, ["a", "b", "c"], combos)

    assert task.type == "multi_analyze"
    assert task.total == 6
    assert task.result["mode"] == "multi"
    assert task.result["apply_tags"] is False  # 组合分析默认不动素材标签


# ── 加载待分析素材 ──


async def test_load_pending_skips_analyzed_and_marks_video_lazy():
    await _insp("i1")
    await _insp("i2")
    await _insp("v1", media_type="video")
    await _log("i1")  # 已有成功日志 → 跳过

    async with async_session() as db:
        items, already, unavailable = await ba._load_pending_items(db, ["i1", "i2", "v1"])

    ids = [iid for iid, _ in items]
    assert ids == ["i2", "v1"]
    assert already == 1
    assert unavailable == 0
    # 视频在加载阶段只登记（frames=None），帧在进入批次时才抽
    assert dict(items)["v1"] is None
    assert dict(items)["i2"] == ["images/x/i2.jpg"]


async def test_load_pending_keeps_analyzed_when_retry():
    """失败重试任务：用户显式要求重跑，不能跳过已成功的素材。"""
    await _insp("r1")
    await _log("r1")

    async with async_session() as db:
        items, already, _ = await ba._load_pending_items(
            db, ["r1"], skip_analyzed=False
        )

    assert [iid for iid, _ in items] == ["r1"]
    assert already == 0


async def test_load_pending_excludes_inflight_and_unknown():
    await _insp("x1")
    async with async_session() as db:
        items, _, _ = await ba._load_pending_items(
            db, ["x1", "不存在"], exclude_inflight={"x1"}
        )
    assert items == []


async def test_load_items_does_not_skip_analyzed():
    """组合分析允许对已分析素材重跑（对比不同模型/提示词正是诉求）。"""
    await _insp("m1")
    await _log("m1")

    async with async_session() as db:
        items = await ba._load_items(db, ["m1"])
    assert [iid for iid, _ in items] == ["m1"]


async def test_load_done_ids_matches_model_and_prompt_version():
    await _insp("d1")
    async with async_session() as db:
        db.add(
            AIAnalysisLog(
                inspiration_id="d1",
                model_name="m1",
                prompt_version="abc12345",
                log_type="analysis",
            )
        )
        await db.commit()

    async with async_session() as db:
        hit = await ba._load_done_ids(db, ["d1"], "m1", "abc12345")
        other_model = await ba._load_done_ids(db, ["d1"], "m2", "abc12345")
        other_prompt = await ba._load_done_ids(db, ["d1"], "m1", "ffffffff")

    assert hit == {"d1"}
    assert other_model == set()
    assert other_prompt == set()


async def test_inflight_excluding_reads_other_unfinished_tasks():
    """并发任务互斥：别的 pending/running 任务认领的素材要豁免。"""
    mine_id = await _task(ids=["a"])
    await _task(ids=["b", "c"])
    await _task(status="success", ids=["d"])  # 已完成的不管

    async with async_session() as db:
        inflight = await ba._inflight_excluding(db, mine_id)

    assert inflight == {"b", "c"}
    assert "a" not in inflight  # 自己认领的不算
    assert "d" not in inflight


# ── 全失败判定 ──


def test_all_failed_noop_when_any_success_or_empty():
    ba._raise_if_all_failed(3, 1, [("a", "err")], ["a"])  # 有成功 → 不抛
    ba._raise_if_all_failed(0, 0, [], [])  # 空批次 → 不抛
    ba._raise_if_all_failed(2, 0, [], [])  # 没有失败样本（如视频帧不可用）→ 不抛


def test_all_failed_recoverable_raises_recoverable():
    """Ollama 宕机这类可恢复错误要让 worker 自动重试，不能判永久失败。"""
    with pytest.raises(RecoverableTaskError) as exc:
        ba._raise_if_all_failed(2, 0, [("a", "无法连接 Ollama 服务")], ["a"])
    assert "可恢复" in str(exc.value)


def test_all_failed_permanent_raises_permanent():
    with pytest.raises(PermanentTaskError) as exc:
        ba._raise_if_all_failed(2, 0, [("a", "图片不存在")], [])
    assert "永久错误" in str(exc.value)


# ── 执行 ──


async def test_execute_empty_task_marks_done(monkeypatch):
    task_id = await _task(ids=[])
    async with async_session() as db:
        await ba.execute_batch_analyze(db, await db.get(TaskQueue, task_id))
    async with async_session() as db:
        refreshed = await db.get(TaskQueue, task_id)

    assert refreshed.progress == 100
    assert refreshed.total == 0


async def test_execute_happy_path_records_counts(monkeypatch):
    await _insp("e1")
    await _insp("e2")
    task_id = await _task(ids=["e1", "e2"])

    async def _ok(sem, iid, frames):
        return iid, True, None

    monkeypatch.setattr(ba, "_analyze_one", _ok)

    async with async_session() as db:
        await ba.execute_batch_analyze(db, await db.get(TaskQueue, task_id))
    async with async_session() as db:
        refreshed = await db.get(TaskQueue, task_id)

    assert refreshed.total == 2
    assert refreshed.done == 2
    assert refreshed.progress == 100
    assert refreshed.result["success_count"] == 2
    assert refreshed.result["failed_count"] == 0
    assert refreshed.result["already_analyzed"] == 0


async def test_execute_partial_failure_keeps_task_successful(monkeypatch):
    """部分失败不算任务失败（失败详情写进 result，供前端列出）。"""
    await _insp("p1")
    await _insp("p2")
    task_id = await _task(ids=["p1", "p2"])

    async def _mixed(sem, iid, frames):
        if iid == "p1":
            return iid, False, "图片损坏"
        return iid, True, None

    monkeypatch.setattr(ba, "_analyze_one", _mixed)

    async with async_session() as db:
        await ba.execute_batch_analyze(db, await db.get(TaskQueue, task_id))
    async with async_session() as db:
        refreshed = await db.get(TaskQueue, task_id)

    assert refreshed.result["success_count"] == 1
    assert refreshed.result["failed_count"] == 1
    assert refreshed.result["failed_ids"] == ["p1"]
    assert refreshed.result["failed_errors"]["p1"] == "图片损坏"


async def test_execute_all_recoverable_failure_raises(monkeypatch):
    await _insp("f1")
    task_id = await _task(ids=["f1"])

    async def _fail(sem, iid, frames):
        return iid, False, "无法连接 Ollama 服务"

    monkeypatch.setattr(ba, "_analyze_one", _fail)

    async with async_session() as db:
        with pytest.raises(RecoverableTaskError):
            await ba.execute_batch_analyze(db, await db.get(TaskQueue, task_id))


async def test_execute_video_frame_failure_counts_unavailable(monkeypatch):
    """视频抽帧失败算「分析源不可用」，不算分析失败（ffmpeg 故障不该整体重试）。"""
    await _insp("v1", media_type="video")
    task_id = await _task(ids=["v1"])

    async def _no_frames(db, iid):
        return []

    async def _never(sem, iid, frames):  # pragma: no cover - 不该被调用
        raise AssertionError("帧不可用的素材不应进入分析")

    monkeypatch.setattr(ba, "_resolve_frames_for", _no_frames)
    monkeypatch.setattr(ba, "_analyze_one", _never)

    async with async_session() as db:
        await ba.execute_batch_analyze(db, await db.get(TaskQueue, task_id))
    async with async_session() as db:
        refreshed = await db.get(TaskQueue, task_id)

    assert refreshed.result["unavailable"] == 1
    assert refreshed.total == 1  # 仍计入总量（否则进度条分母不对）


@pytest.mark.parametrize("new_status", ["paused", "cancelled", "pending"])
async def test_execute_stops_when_status_changes(monkeypatch, new_status):
    """批次边界检查：只要不再是 running 就保存进度退出。

    pending 也要停——pause→resume 竞态会把状态改回 pending，若继续跑就会出现
    「任务在跑但界面显示 pending、没有暂停按钮」的卡死。
    """
    await _insp("s1")
    await _insp("s2")
    task_id = await _task(ids=["s1", "s2"])
    calls: list[str] = []

    async def _analyze(sem, iid, frames):
        calls.append(iid)
        # 第一个素材分析完后把任务状态改掉，模拟用户操作
        async with async_session() as db:
            row = await db.get(TaskQueue, task_id)
            row.status = new_status
            await db.commit()
        return iid, True, None

    monkeypatch.setattr(ba, "_analyze_one", _analyze)

    async with async_session() as db:
        await ba.execute_batch_analyze(db, await db.get(TaskQueue, task_id))
    async with async_session() as db:
        refreshed = await db.get(TaskQueue, task_id)

    assert calls == ["s1"]  # 第二批没跑
    assert refreshed.status == new_status  # 执行器没有覆盖外部状态
    assert refreshed.done == 1


# ── 多模型 × 多提示词组合分析（execute_multi_analyze）──


async def _multi_task(ids: list[str], combos: list[dict], *, apply_tags: bool = False) -> int:
    async with async_session() as db:
        task = TaskQueue(
            type="multi_analyze",
            status="running",
            progress=0,
            total=len(ids) * len(combos),
            done=0,
            result={
                "mode": "multi",
                "inspiration_ids": ids,
                "apply_tags": apply_tags,
                "combinations": combos,
                "skipped": 0,
            },
        )
        db.add(task)
        await db.commit()
        return task.id


async def test_multi_empty_task_marks_done():
    task_id = await _multi_task([], [{"model": "m1", "prompt": None}])
    async with async_session() as db:
        await ba.execute_multi_analyze(db, await db.get(TaskQueue, task_id))
    async with async_session() as db:
        refreshed = await db.get(TaskQueue, task_id)
    assert refreshed.progress == 100
    assert refreshed.total == 0


async def test_multi_runs_every_combination(monkeypatch):
    """组合数是进度分母：2 素材 × 2 组合 = 4 项。"""
    await _insp("mm1")
    await _insp("mm2")
    combos = [
        {"model": "m1", "prompt": None, "prompt_label": "当前默认提示词"},
        {"model": "m2", "prompt": "自定义提示词", "prompt_label": "版本 #1"},
    ]
    task_id = await _multi_task(["mm1", "mm2"], combos)
    seen: list[tuple[str, str]] = []

    async def _ok(sem, iid, frames, model_name, prompt, apply_tags):
        seen.append((iid, model_name))
        return iid, model_name, True, None

    monkeypatch.setattr(ba, "_analyze_one_multi", _ok)

    async with async_session() as db:
        await ba.execute_multi_analyze(db, await db.get(TaskQueue, task_id))
    async with async_session() as db:
        refreshed = await db.get(TaskQueue, task_id)

    assert len(seen) == 4
    assert {m for _, m in seen} == {"m1", "m2"}
    assert refreshed.total == 4
    assert refreshed.progress == 100
    assert refreshed.result["success_count"] == 4
    assert refreshed.result["mode"] == "multi"


async def test_multi_skips_done_combinations_on_retry(monkeypatch):
    """崩溃重试幂等：某组合已成功的素材不再重复分析（避免日志重复堆积）。"""
    await _insp("md1")
    prompt = "固定提示词"
    version = ba._prompt_version(prompt)
    async with async_session() as db:
        db.add(
            AIAnalysisLog(
                inspiration_id="md1",
                model_name="m1",
                prompt_version=version,
                log_type="analysis",
            )
        )
        await db.commit()

    combos = [{"model": "m1", "prompt": prompt, "prompt_label": "版本 #1"}]
    task_id = await _multi_task(["md1"], combos)
    called: list[str] = []

    async def _never(sem, iid, frames, model_name, prompt, apply_tags):  # pragma: no cover
        called.append(iid)
        return iid, model_name, True, None

    monkeypatch.setattr(ba, "_analyze_one_multi", _never)

    async with async_session() as db:
        await ba.execute_multi_analyze(db, await db.get(TaskQueue, task_id))
    async with async_session() as db:
        refreshed = await db.get(TaskQueue, task_id)

    assert called == []  # 已完成项被跳过
    assert refreshed.progress == 100


async def test_multi_records_failures_without_failing_task(monkeypatch):
    await _insp("mf1")
    await _insp("mf2")
    combos = [{"model": "m1", "prompt": None, "prompt_label": "当前默认提示词"}]
    task_id = await _multi_task(["mf1", "mf2"], combos)

    async def _mixed(sem, iid, frames, model_name, prompt, apply_tags):
        if iid == "mf1":
            return iid, model_name, False, "模型未安装"
        return iid, model_name, True, None

    monkeypatch.setattr(ba, "_analyze_one_multi", _mixed)

    async with async_session() as db:
        await ba.execute_multi_analyze(db, await db.get(TaskQueue, task_id))
    async with async_session() as db:
        refreshed = await db.get(TaskQueue, task_id)

    assert refreshed.result["success_count"] == 1
    assert refreshed.result["failed_count"] == 1
    assert refreshed.result["failed"][0]["inspiration_id"] == "mf1"
    assert refreshed.result["failed"][0]["model_name"] == "m1"


async def test_multi_all_recoverable_raises():
    await _insp("mr1")
    combos = [{"model": "m1", "prompt": None, "prompt_label": "当前默认提示词"}]
    task_id = await _multi_task(["mr1"], combos)

    async def _fail(sem, iid, frames, model_name, prompt, apply_tags):
        return iid, model_name, False, "无法连接 Ollama 服务"

    ba_orig = ba._analyze_one_multi
    ba._analyze_one_multi = _fail
    try:
        async with async_session() as db:
            with pytest.raises(RecoverableTaskError):
                await ba.execute_multi_analyze(db, await db.get(TaskQueue, task_id))
    finally:
        ba._analyze_one_multi = ba_orig


async def test_multi_all_permanent_raises():
    await _insp("mp1")
    combos = [{"model": "m1", "prompt": None, "prompt_label": "当前默认提示词"}]
    task_id = await _multi_task(["mp1"], combos)

    async def _fail(sem, iid, frames, model_name, prompt, apply_tags):
        return iid, model_name, False, "图片不存在"

    ba_orig = ba._analyze_one_multi
    ba._analyze_one_multi = _fail
    try:
        async with async_session() as db:
            with pytest.raises(PermanentTaskError):
                await ba.execute_multi_analyze(db, await db.get(TaskQueue, task_id))
    finally:
        ba._analyze_one_multi = ba_orig


async def test_multi_stops_when_status_changes(monkeypatch):
    """组合分析的批次边界同样要能响应暂停/取消（否则停不下来）。"""
    await _insp("ms1")
    await _insp("ms2")
    combos = [{"model": "m1", "prompt": None, "prompt_label": "当前默认提示词"}]
    task_id = await _multi_task(["ms1", "ms2"], combos)
    calls: list[str] = []

    async def _analyze(sem, iid, frames, model_name, prompt, apply_tags):
        calls.append(iid)
        async with async_session() as db:
            row = await db.get(TaskQueue, task_id)
            row.status = "paused"
            await db.commit()
        return iid, model_name, True, None

    monkeypatch.setattr(ba, "_analyze_one_multi", _analyze)

    async with async_session() as db:
        await ba.execute_multi_analyze(db, await db.get(TaskQueue, task_id))
    async with async_session() as db:
        refreshed = await db.get(TaskQueue, task_id)

    assert calls == ["ms1"]
    assert refreshed.status == "paused"


async def test_analyze_one_multi_dispatches_by_frame_count(monkeypatch):
    """单帧走 analyze_image、多帧走 analyze_video（视频融合分析）。"""
    await _insp("df1")
    calls: list[str] = []

    async def _image(db, insp_id, path, **kwargs):
        calls.append("image")
        return True

    async def _video(db, insp_id, frames, **kwargs):
        calls.append("video")
        return True

    monkeypatch.setattr(ba, "analyze_image", _image)
    monkeypatch.setattr(ba, "analyze_video", _video)
    sem = asyncio.Semaphore(1)

    await ba._analyze_one_multi(sem, "df1", ["a.jpg"], "m1", None, False)
    await ba._analyze_one_multi(sem, "df1", ["a.jpg", "b.jpg"], "m1", None, False)

    assert calls == ["image", "video"]
