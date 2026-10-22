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

    async def aclose(self) -> None:
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
    """删除只清候选文件：已入库素材保留，但与本批次解绑（generation_run_id 置 NULL）。"""
    async with async_session() as db:
        out = await generation_service.create_run(db, {"prompt": "丢弃"})
        run_id = out["run_id"]
        folder = settings.storage_root / f"generated/{run_id}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "01_seed-1.png").write_bytes(_png_bytes())
        await generation_service.confirm_import(db, run_id, ["01_seed-1.png"])
        # 丢弃要求批次已终态（执行中丢目录会让 runner 崩）——先把状态推到 done
        run = await db.get(GenerationRun, run_id)
        run.status = "done"
        task = await db.get(TaskQueue, run.task_id) if run.task_id else None
        if task is not None:
            task.status = "success"
        await db.commit()

        result = await generation_service.discard_run(db, run_id)
        assert result["removed_files"] == 1
        assert not folder.exists()
        # 已入库素材仍在（只是与本批次解绑：generation_run_id 被置 NULL）
        remaining = (
            await db.execute(select(Inspiration.id).where(Inspiration.id.is_not(None)))
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


# ── P1：同参重跑 / 过期清理 ──


async def test_retry_creates_new_run_with_same_params(client):
    """重跑新建批次（历史保留），参数与参考图沿用原批次。"""
    async with async_session() as db:
        first = await generation_service.create_run(
            db, {"prompt": "重跑测试", "count": 2, "width": 1024, "height": 1024}
        )
        second = await generation_service.retry_run(db, first["run_id"], seed=8888)

        assert second["run_id"] != first["run_id"]
        assert second["seed"] == 8888
        run2 = await db.get(GenerationRun, second["run_id"])
        params2 = json.loads(run2.params_json)
        assert params2["seeds"] == [8888, 8889]
        assert run2.prompt == "重跑测试"
        assert run2.workflow_name == "z_image_t2i"


async def test_retry_randomises_seed_by_default(client):
    async with async_session() as db:
        first = await generation_service.create_run(db, {"prompt": "重跑随机", "count": 1})
        second = await generation_service.retry_run(db, first["run_id"])
        assert second["seed"] > 0
        assert second["run_id"] != first["run_id"]


async def test_cleanup_removes_only_expired_terminal_runs(client):
    """清理只删「终态 + 超期」批次的候选目录；已入库素材与历史行不动。"""
    from datetime import timedelta

    from app.utils.time import utcnow

    async with async_session() as db:
        old = await generation_service.create_run(db, {"prompt": "过期批次"})
        fresh = await generation_service.create_run(db, {"prompt": "新批次"})
        for info in (old, fresh):
            folder = settings.storage_root / f"generated/{info['run_id']}"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "01_seed-1.png").write_bytes(_png_bytes())

        # 把「过期批次」改成终态 + 30 天前
        run_old = await db.get(GenerationRun, old["run_id"])
        run_old.status = "done"
        run_old.created_at = utcnow() - timedelta(days=30)
        run_fresh = await db.get(GenerationRun, fresh["run_id"])
        run_fresh.status = "done"
        await db.commit()

        result = await generation_service.cleanup_expired_runs(db, days=7)

        assert result["removed"] == 1
        assert not (settings.storage_root / f"generated/{old['run_id']}").exists()
        assert (settings.storage_root / f"generated/{fresh['run_id']}").exists()
        # 历史行仍在（清理只动磁盘）
        assert await db.get(GenerationRun, old["run_id"]) is not None


# ── i2i：参考图校验与执行 ──


async def _add_reference_image(insp_id: str, *, media: str = "image", deleted: bool = False) -> str:
    """造一张「真实存在」的参考素材（文件落到 storage，行写进库）。"""
    rel = f"images/2026-10/{insp_id}.png"
    path = settings.storage_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_png_bytes())
    async with async_session() as db:
        db.add(
            Inspiration(
                id=insp_id,
                source_type="douyin",
                file_path=rel,
                media_type=media,
                quality_status="approved",
                deleted_at=None if not deleted else __import__("datetime").datetime(2026, 10, 1),
            )
        )
        await db.commit()
    return rel


async def test_i2i_requires_reference_images(client):
    """图生图没给参考图 → 提交前就拒（别让用户等几十秒才失败）。"""
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await generation_service.create_run(db, {"kind": "i2i", "prompt": "改款"})
        assert "参考素材" in str(exc.value.detail)


async def test_i2i_rejects_missing_or_wrong_type_reference(client):
    await _add_reference_image("ref-video", media="video")
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await generation_service.create_run(
                db, {"kind": "i2i", "prompt": "x", "reference_ids": ["not-exist"]}
            )
        assert "不存在" in str(exc.value.detail)

        with pytest.raises(HTTPException) as exc:
            await generation_service.create_run(
                db, {"kind": "i2i", "prompt": "x", "reference_ids": ["ref-video"]}
            )
        assert "必须是图片" in str(exc.value.detail)


async def test_i2i_create_run_records_references(client):
    await _add_reference_image("ref-ok-1")
    async with async_session() as db:
        out = await generation_service.create_run(
            db, {"kind": "i2i", "prompt": "把上衣换成米白针织开衫", "reference_ids": ["ref-ok-1"]}
        )
        run = await db.get(GenerationRun, out["run_id"])
        assert run.kind == "i2i"
        assert run.model_key == "qwen_2_1_i2i"
        assert run.workflow_name == "qwen_edit"
        assert json.loads(run.reference_ids) == ["ref-ok-1"]


async def test_execute_i2i_uploads_references_then_generates(client, monkeypatch):
    """i2i 执行：先把参考图上传到 ComfyUI，再逐张走编辑模板（latent 来自条件节点）。"""
    uploaded: list[str] = []
    submitted: list[dict] = []

    class _EditClient(_FakeClient):
        async def upload_image(self, data: bytes, filename: str) -> str:
            uploaded.append(filename)
            return f"up_{filename}"

        async def submit(self, workflow: dict, client_id: str = "x") -> str:
            submitted.append(workflow)
            return "pid-edit"

    monkeypatch.setattr(runner, "ComfyUIClient", _EditClient)
    await _add_reference_image("ref-exec")

    async with async_session() as db:
        out = await generation_service.create_run(
            db, {"kind": "i2i", "prompt": "换成米白开衫", "reference_ids": ["ref-exec"], "count": 1}
        )
        task = await db.get(TaskQueue, out["task_id"])
        await runner.execute_image_gen(db, task)
        run = await db.get(GenerationRun, out["run_id"])

    # 文件名带 run id（会被其他用例推进），只校验形态
    assert uploaded == [f"gen_ref_{out['run_id']}_1.png"]
    assert len(submitted) == 1
    workflow = submitted[0]
    # 参考图接进 images.image_1（扁平点号 key，嵌套 dict 会被 ComfyUI 静默丢弃），
    # latent 用条件节点的第三个输出
    assert workflow["6"]["inputs"]["images.image_1"] == ["4", 0]
    assert "images" not in workflow["6"]["inputs"]
    assert workflow["7"]["inputs"]["latent_image"] == ["6", 2]
    assert run.status == "done"


# ── 审查修复回归（2026-10-21）──


async def test_detail_returns_task_stage(client):
    """详情接口要带任务阶段：否则前端「等待显存已等 N 秒」永远是 0。"""
    async with async_session() as db:
        out = await generation_service.create_run(db, {"prompt": "阶段信息"})
        task = await db.get(TaskQueue, out["task_id"])
        task.status = "running"
        task.progress = 40
        task.result = {"run_id": out["run_id"], "stage": "waiting_vram", "waited_seconds": 30}
        await db.commit()

        detail = await generation_service.list_candidates(db, out["run_id"])

    assert detail["task"]["status"] == "running"
    assert detail["task"]["progress"] == 40
    assert detail["task"]["result"]["waited_seconds"] == 30


async def test_discard_rejected_while_running(client):
    """执行中的批次不能丢弃：删目录会让 runner 写文件失败。"""
    async with async_session() as db:
        out = await generation_service.create_run(db, {"prompt": "在跑"})
        task = await db.get(TaskQueue, out["task_id"])
        task.status = "running"
        run = await db.get(GenerationRun, out["run_id"])
        run.status = "running"
        await db.commit()

        with pytest.raises(HTTPException) as exc:
            await generation_service.discard_run(db, out["run_id"])
        assert "仍在生成中" in str(exc.value.detail)


async def test_discard_rejected_while_task_pending(client):
    """批次已终态但任务还排队（数据不一致）也不能删目录。"""
    async with async_session() as db:
        out = await generation_service.create_run(db, {"prompt": "排队"})
        task = await db.get(TaskQueue, out["task_id"])
        task.status = "pending"
        run = await db.get(GenerationRun, out["run_id"])
        run.status = "done"
        await db.commit()

        with pytest.raises(HTTPException) as exc:
            await generation_service.discard_run(db, out["run_id"])
        assert "请先取消" in str(exc.value.detail)


async def test_confirm_rejects_unknown_tag(client):
    """无效标签 id 要在提交前拦成 400，而不是 flush 时撞外键变 500。"""
    async with async_session() as db:
        out = await generation_service.create_run(db, {"prompt": "标签"})
        folder = settings.storage_root / f"generated/{out['run_id']}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "01_seed-1.png").write_bytes(_png_bytes())

        with pytest.raises(HTTPException) as exc:
            await generation_service.confirm_import(
                db, out["run_id"], ["01_seed-1.png"], tag_ids=[999999]
            )
        assert "标签不存在" in str(exc.value.detail)


async def test_confirm_partial_failure_leaves_no_orphan_files(client, monkeypatch):
    """第 2 张失败时，第 1 张已复制的文件要一起清掉（否则磁盘留孤儿）。"""
    from app.services import generation_service as svc

    async with async_session() as db:
        out = await generation_service.create_run(db, {"prompt": "部分失败", "count": 2})
        folder = settings.storage_root / f"generated/{out['run_id']}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "01_seed-1.png").write_bytes(_png_bytes("red"))
        (folder / "02_seed-2.png").write_bytes(_png_bytes("blue"))

        real = svc.create_inspiration_from_file
        calls = {"n": 0}

        async def _boom(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise HTTPException(status_code=500, detail="模拟缩略图失败")
            return await real(*args, **kwargs)

        monkeypatch.setattr(svc, "create_inspiration_from_file", _boom)

        with pytest.raises(HTTPException):
            await generation_service.confirm_import(
                db, out["run_id"], ["01_seed-1.png", "02_seed-2.png"]
            )

    # 第 1 张复制进 storage 的文件应被清理，且库里没有残留行
    async with async_session() as db:
        rows = (
            await db.execute(
                select(Inspiration.id).where(Inspiration.generation_run_id == out["run_id"])
            )
        ).scalars().all()
    assert rows == []
    images_dir = settings.images_dir
    leftovers = [p for p in images_dir.rglob("*.png") if p.stat().st_size > 0]
    assert leftovers == []


async def test_i2i_size_limit_message_matches_spec(client):
    """i2i 上限是 1024²，报错文案不能拿 1328 说事。"""
    await _add_reference_image("ref-size")
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await generation_service.create_run(
                db,
                {"kind": "i2i", "prompt": "x", "reference_ids": ["ref-size"],
                 "width": 1328, "height": 1328},
            )
        detail = str(exc.value.detail)
        assert "1048576" in detail
        assert "1328×1328" not in detail


async def test_t2i_rejects_reference_images(client):
    """文生图带参考图必须显式报错：静默丢弃会让用户以为「按这张图生成」了。"""
    await _add_reference_image("ref-stray")
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await generation_service.create_run(
                db,
                {"kind": "t2i", "prompt": "米色针织", "reference_ids": ["ref-stray"]},
            )
        detail = str(exc.value.detail)
        assert "文生图不接受参考素材" in detail
        assert "i2i" in detail


async def test_model_kind_must_match_generation_kind(client):
    """档位与生成方式不匹配要拦（否则会拿编辑模板跑文生图，缺参考图直接 500）。"""
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await generation_service.create_run(
                db, {"kind": "t2i", "prompt": "x", "model_key": "qwen_2_1_i2i"}
            )
        assert "参考图改款" in str(exc.value.detail)

        await _add_reference_image("ref-kind")
        with pytest.raises(HTTPException) as exc:
            await generation_service.create_run(
                db,
                {"kind": "i2i", "prompt": "x", "model_key": "z_image_int8",
                 "reference_ids": ["ref-kind"]},
            )
        assert "文生图" in str(exc.value.detail)


async def test_regenerate_single_candidate_creates_one_image_run(client):
    """单张重出：新建只含 1 张的批次，且不能沿用原 seed（否则出同一张）。"""
    async with async_session() as db:
        out = await generation_service.create_run(db, {"prompt": "单张重出", "count": 2, "seed": 500})
        folder = settings.storage_root / f"generated/{out['run_id']}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "02_seed-501.png").write_bytes(_png_bytes())

        new = await generation_service.regenerate_candidate(
            db, out["run_id"], "02_seed-501.png"
        )
        assert new["run_id"] != out["run_id"]
        assert new["total"] == 1
        assert new["original_seed"] == 501
        assert new["source_run_id"] == out["run_id"]
        assert new["seed"] != 501  # 随机换了 seed

        run = await db.get(GenerationRun, new["run_id"])
        assert json.loads(run.params_json)["seeds"] == [new["seed"]]

        # 指定 seed 时按指定值走
        fixed = await generation_service.regenerate_candidate(
            db, out["run_id"], "02_seed-501.png", seed=777
        )
        assert fixed["seed"] == 777

        # 原批次不受影响
        old = await db.get(GenerationRun, out["run_id"])
        assert old.total == 2
