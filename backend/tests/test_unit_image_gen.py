"""出图执行器与业务层的测试（假 ComfyUI，不碰真实 GPU）。

覆盖三件最容易回归的事：

1. **已出的图不能丢**：第 2 张失败时第 1 张必须还在候选区（用户不该白等）；
2. **取消要生效**：任务被取消后不得再提交新的一张；
3. **入库幂等**：重复确认同一张图 → 「跳过」而不是报错。
"""

import json
from io import BytesIO

import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.models.generation import GenerationRun
from app.models.inspiration import Inspiration
from app.models.task import TaskQueue
from app.services import generation_service
from app.services.comfyui.client import ComfyUIError
from app.services.task_runners import image_gen as runner
from app.services.task_runners.common import PermanentTaskError


def _png_bytes(color: str = "beige") -> bytes:
    buf = BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, format="PNG")
    return buf.getvalue()


class _FakeClient:
    """最小假客户端：显存充足、提交即完成、返回固定 PNG。"""

    fail_on_call: int | None = None
    calls = 0

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def system_stats(self) -> dict:
        # 必须给足：否则真实 wait_for_vram 会按「Ollama 占显存」开始等 900 秒
        return {"devices": [{"vram_total": 20 * 2**30, "vram_free": 19 * 2**30}]}

    async def submit(self, workflow: dict, client_id: str = "x") -> str:
        _FakeClient.calls += 1
        if _FakeClient.fail_on_call == _FakeClient.calls:
            raise ComfyUIError("Allocation on device", kind="oom")
        # 注入的 seed 应写进 prefix，顺便断言注入生效
        assert "gen_" in workflow["10"]["inputs"]["filename_prefix"]
        return f"pid-{_FakeClient.calls}"

    async def wait_for_prompt(self, prompt_id, *, timeout, on_tick=None, tick_interval=2.0):
        return {"outputs": {"10": {"images": [{"filename": "out.png", "subfolder": ""}]}}}

    async def fetch_image(self, filename: str, subfolder: str = "", folder_type: str = "output") -> bytes:
        return _png_bytes()

    async def interrupt(self) -> None:
        return None


async def _make_run(seeds: list[int], *, status: str = "queued") -> tuple[int, int]:
    """造一条批次 + 任务，返回 (run_id, task_id)。"""
    async with async_session() as db:
        run = GenerationRun(
            kind="t2i",
            status=status,
            prompt="米色针织开衫，白色阔腿裤",
            params_json=json.dumps(
                {
                    "width": 1024, "height": 1024, "steps": 8, "cfg": 1.0,
                    "sampler": "res_multistep", "scheduler": "simple", "shift": 3.0,
                    "seeds": seeds, "seed_base": seeds[0],
                }
            ),
            model_key="z_image_int8",
            workflow_name="z_image_t2i",
            workflow_hash="deadbeefdeadbeef",
            batch_dir="",
            total=len(seeds),
        )
        db.add(run)
        await db.flush()
        run.batch_dir = f"generated/{run.id}"
        task = TaskQueue(
            type="image_gen", status="running", progress=0,
            total=len(seeds), done=0, result={"run_id": run.id, "seeds": seeds},
        )
        db.add(task)
        await db.flush()
        run.task_id = task.id
        await db.commit()
        return run.id, task.id


@pytest.fixture(autouse=True)
def _reset_fake(monkeypatch) -> None:
    _FakeClient.fail_on_call = None
    _FakeClient.calls = 0
    # 显存守门在测试里必须「立刻放行」：真实实现会去问 Ollama，并可能等 900 秒
    from app.services.comfyui import vram as vram_mod

    async def _no_resident() -> list[dict]:
        return []

    monkeypatch.setattr(vram_mod, "ollama_resident", _no_resident)
    monkeypatch.setattr(vram_mod, "CHECK_INTERVAL", 0)


async def test_execute_writes_candidates_and_marks_done(client, monkeypatch, tmp_path):
    """正常路径：2 张都落盘、文件名带 seed、进度 100、状态 done。"""
    monkeypatch.setattr(runner, "ComfyUIClient", _FakeClient)
    run_id, task_id = await _make_run([111, 112])

    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
        await runner.execute_image_gen(db, task)
        run = await db.get(GenerationRun, run_id)
        assert run.status == "done"
        assert run.done == 2
        assert task.progress == 100

    folder = settings.storage_root / f"generated/{run_id}"
    names = sorted(p.name for p in folder.glob("*.png"))
    assert names == ["01_seed-111.png", "02_seed-112.png"]


async def test_failed_second_image_keeps_first(client, monkeypatch):
    """第 2 张失败：第 1 张仍在候选区，任务判失败并给人话原因。"""
    monkeypatch.setattr(runner, "ComfyUIClient", _FakeClient)
    _FakeClient.fail_on_call = 2
    run_id, task_id = await _make_run([201, 202])

    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
        with pytest.raises(PermanentTaskError) as exc:
            await runner.execute_image_gen(db, task)
        run = await db.get(GenerationRun, run_id)

    assert "显存不足" in str(exc.value)
    assert run.status == "failed"
    assert run.done == 1
    assert (settings.storage_root / f"generated/{run_id}" / "01_seed-201.png").exists()


async def test_cancelled_before_start_submits_nothing(client, monkeypatch):
    """任务已取消：不提交任何一张（而不是忽略状态继续烧 GPU）。"""
    monkeypatch.setattr(runner, "ComfyUIClient", _FakeClient)
    run_id, task_id = await _make_run([301, 302])

    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
        run = await db.get(GenerationRun, run_id)
        run.status = "running"  # 模拟 worker 已把任务置 running 后又收到取消
        task.status = "cancelled"
        await db.commit()

        await runner.execute_image_gen(db, task)
        run = await db.get(GenerationRun, run_id)

    assert _FakeClient.calls == 0
    assert run.status == "cancelled"


async def test_run_missing_is_permanent_error(client):
    """批次被清理后任务不能空转：直接永久失败并提示重提。"""
    async with async_session() as db:
        task = TaskQueue(
            type="image_gen", status="running", progress=0, total=1, done=0,
            result={"run_id": 999999, "seeds": [1]},
        )
        db.add(task)
        await db.commit()
        with pytest.raises(PermanentTaskError) as exc:
            await runner.execute_image_gen(db, task)
    assert "批次不存在" in str(exc.value)


# ── 业务层 ──


async def test_create_run_rejects_oversized_and_invalid(client):
    """参数硬上限：尺寸/张数/prompt/图生图 各自给出具体原因。"""
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await generation_service.create_run(
                db, {"prompt": "x", "width": 2048, "height": 2048}
            )
        assert "尺寸过大" in str(exc.value.detail)

        with pytest.raises(HTTPException) as exc:
            await generation_service.create_run(db, {"prompt": "x", "count": 9})
        assert "张数" in str(exc.value.detail)

        with pytest.raises(HTTPException) as exc:
            await generation_service.create_run(db, {"prompt": "  "})
        assert "不能为空" in str(exc.value.detail)

        with pytest.raises(HTTPException) as exc:
            await generation_service.create_run(db, {"prompt": "x", "kind": "i2i"})
        assert "图生图" in str(exc.value.detail)


async def test_create_run_returns_seed_and_batch_dir(client):
    async with async_session() as db:
        out = await generation_service.create_run(db, {"prompt": "米色针织", "count": 2})
        assert out["total"] == 2
        assert out["batch_dir"] == f"generated/{out['run_id']}"
        assert out["seed"] > 0
        run = await db.get(GenerationRun, out["run_id"])
        assert run.workflow_hash  # 模板指纹已记录
        task = await db.get(TaskQueue, out["task_id"])
        assert task.type == "image_gen"


async def test_confirm_import_then_idempotent(client):
    """确认入库：第 1 次真入库并标记来源；第 2 次「已存在」跳过而不报错。"""
    async with async_session() as db:
        out = await generation_service.create_run(db, {"prompt": "测试入库"})
        run_id = out["run_id"]
        folder = settings.storage_root / f"generated/{run_id}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "01_seed-1.png").write_bytes(_png_bytes())

        first = await generation_service.confirm_import(db, run_id, ["01_seed-1.png"])
        assert first["imported"] == 1

        rows = (
            await db.execute(
                select(Inspiration).where(Inspiration.generation_run_id == run_id)
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].source_type == "generated"
        assert rows[0].is_ai_generated is True
        assert rows[0].quality_status == "pending"
        assert rows[0].caption == "测试入库"

        second = await generation_service.confirm_import(db, run_id, ["01_seed-1.png"])
        assert second["imported"] == 0
        assert second["skipped"] == 1


async def test_list_candidates_marks_imported(client):
    async with async_session() as db:
        out = await generation_service.create_run(db, {"prompt": "候选"})
        run_id = out["run_id"]
        folder = settings.storage_root / f"generated/{run_id}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "01_seed-7.png").write_bytes(_png_bytes("red"))

        data = await generation_service.list_candidates(db, run_id)
        assert data["items"][0]["seed"] == 7
        assert data["items"][0]["imported"] is False

        await generation_service.confirm_import(db, run_id, ["01_seed-7.png"])
        data = await generation_service.list_candidates(db, run_id)
        assert data["items"][0]["imported"] is True


async def test_discard_run_removes_candidates_only(client):
    """丢弃只删候选文件，不动已入库素材。"""
    async with async_session() as db:
        out = await generation_service.create_run(db, {"prompt": "丢弃"})
        run_id = out["run_id"]
        folder = settings.storage_root / f"generated/{run_id}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "01_seed-1.png").write_bytes(_png_bytes())
        await generation_service.confirm_import(db, run_id, ["01_seed-1.png"])

        result = await generation_service.discard_run(db, run_id)
        assert result["removed_files"] == 1
        assert not folder.exists()
        # 已入库素材仍在
        remaining = (
            await db.execute(
                select(Inspiration.id).where(Inspiration.generation_run_id == run_id)
            )
        ).scalars().all()
        assert len(remaining) == 1


# ── 决策 2：负样本排除 AI 生成图 ──


async def test_negative_samples_exclude_ai_generated(client):
    """AI 生成图进垃圾桶后**不得**进负样本（否则初筛器把 AI 特征学成垃圾）。"""
    from datetime import datetime

    from app.services.quality_learner import collect_samples

    async with async_session() as db:
        db.add_all([
            Inspiration(
                id="neg-ai", source_type="generated", file_path="images/x/ai.png",
                media_type="image", quality_status="rejected", is_ai_generated=True,
                deleted_at=datetime(2026, 10, 21), trash_reason="质量差",
            ),
            Inspiration(
                id="neg-real", source_type="douyin", file_path="images/x/real.png",
                media_type="image", quality_status="rejected", is_ai_generated=False,
                deleted_at=datetime(2026, 10, 21), trash_reason="质量差",
            ),
        ])
        await db.commit()

        _X, _y, stats = await collect_samples(db)

    assert stats["negative_total"] == 1  # 只有采集的那张
