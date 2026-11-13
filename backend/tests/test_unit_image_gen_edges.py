"""出图执行器（`task_runners/image_gen.py`）的错误分支与纯函数测试。

`test_unit_image_gen.py` 已覆盖主链路（等显存 → 逐张生成 → 落盘 → 终态），本文件补
**失败路径**——这些分支平时不跑，跑起来就是「用户等半天、历史里却一直显示生成中」：

- `_friendly_error`：ComfyUI 未启动 / 显存不足 / 超时 / 工作流不匹配要给不同的、
  用户能照做的一句话；分错类会让用户重复无效操作。
- `execute_image_gen`：未知档位、缺 seed、工作流模板缺失、档位与模板不符
  **都必须是永久错误**（重试不会变好），不能退回模板默认权重静默出「另一版」的图。
- 云端 Seedream：准备阶段失败（缺 pose_id / 参考图数量不对 / 素材已删 / 文件缺失）
  必须把批次写成 failed 再抛，否则批次永久停在 running。
- `_prepare_ark_image`：EXIF 方向校正、透明背景贴白底、长边压到 2048、PNG/JPEG 输出。
"""

import json

import pytest

from app.database import async_session
from app.models.generation import GenerationRun
from app.models.inspiration import Inspiration
from app.models.task import TaskQueue
from app.services.comfyui.client import ComfyUIError
from app.services.task_runners import image_gen as gen
from app.services.task_runners.common import PermanentTaskError


async def _run(**overrides) -> int:
    payload = {
        "kind": "t2i",
        "status": "queued",
        "prompt": "法式通勤",
        "model_key": "z_image_int8",
        "workflow_name": "z_image_t2i",
        "total": 1,
        "done": 0,
        "params_json": json.dumps({"seeds": [42]}),
        "batch_dir": "generated/1",
    }
    payload.update(overrides)
    async with async_session() as db:
        run = GenerationRun(**payload)
        db.add(run)
        await db.commit()
        return run.id


async def _task(run_id: int, status: str = "running") -> int:
    async with async_session() as db:
        task = TaskQueue(
            type="image_gen",
            status=status,
            total=1,
            done=0,
            result={"run_id": run_id} if run_id else {},
        )
        db.add(task)
        await db.commit()
        return task.id


class TestFriendlyError:
    @pytest.mark.parametrize(
        ("kind", "expected"),
        [
            ("cancelled", "已取消"),
            ("not_running", "ComfyUI 未启动"),
            ("oom", "显存不足"),
            ("vram_timeout", "boom"),  # 该类型原样透出异常信息（含等待进度）
            ("timeout", "生成超时"),
            ("invalid", "工作流与当前 ComfyUI/模型不匹配"),
            ("http_timeout", "ComfyUI 响应超时"),
            ("unknown-kind", "生成失败"),
        ],
    )
    def test_each_kind_gets_actionable_message(self, kind, expected):
        message = gen._friendly_error(ComfyUIError("boom", kind=kind))
        assert expected in message

    def test_timeout_suggests_reducing_workload(self):
        message = gen._friendly_error(ComfyUIError("90 秒无响应", kind="timeout"))
        assert "降低分辨率或张数" in message


class TestSmallHelpers:
    def test_params_of_handles_bad_json(self):
        assert gen._params_of(GenerationRun(params_json=json.dumps({"seeds": [1]}))) == {
            "seeds": [1]
        }
        assert gen._params_of(GenerationRun(params_json="不是 JSON")) == {}
        assert gen._params_of(GenerationRun(params_json="")) == {}

    def test_pick_image_variants(self):
        assert gen._pick_image({"outputs": {}}) is None
        assert gen._pick_image({"outputs": {"9": {"images": []}}, "other": 1}) is None
        picked = gen._pick_image(
            {"outputs": {"1": {"images": []}, "2": {"images": [{"filename": "a.png"}]}}}
        )
        assert picked == {"filename": "a.png"}

    def test_candidate_dir_uses_storage_root(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gen.settings, "storage_root", tmp_path)
        run = GenerationRun(batch_dir="generated/7")
        assert gen._candidate_dir(run) == tmp_path / "generated/7"

    async def test_load_run_missing_run_or_run_id_is_permanent_error(self, client):
        async with async_session() as db:
            with pytest.raises(PermanentTaskError):
                await gen._load_run(db, TaskQueue(result={}))
            with pytest.raises(PermanentTaskError):
                await gen._load_run(db, TaskQueue(result={"run_id": 999999}))


class TestStageAndCancel:
    async def test_mark_stage_writes_task_result_and_syncs_run(self, client):
        run_id = await _run()
        task_id = await _task(run_id)
        async with async_session() as db:
            task = await db.get(TaskQueue, task_id)
            run = await db.get(GenerationRun, run_id)

            await gen._mark_stage(db, task, run, "waiting_vram", queue_pos=3)

            assert run.status == "waiting_vram"  # 前端据此显示「等待显存」
            assert task.result["stage"] == "waiting_vram"
            assert task.result["queue_pos"] == 3

    async def test_mark_stage_normal_stage_does_not_change_run_status(self, client):
        run_id = await _run(status="running")
        task_id = await _task(run_id)
        async with async_session() as db:
            task = await db.get(TaskQueue, task_id)
            run = await db.get(GenerationRun, run_id)
            await gen._mark_stage(db, task, run, "generating")
            assert run.status == "running"

    async def test_is_cancelled_reads_status_only(self, client):
        run_id = await _run()
        running_id = await _task(run_id, status="running")
        paused_id = await _task(run_id, status="paused")
        cancelled_id = await _task(run_id, status="cancelled")
        async with async_session() as db:
            assert await gen._is_cancelled(db, await db.get(TaskQueue, running_id)) is False
            assert await gen._is_cancelled(db, await db.get(TaskQueue, paused_id)) is True
            assert await gen._is_cancelled(db, await db.get(TaskQueue, cancelled_id)) is True


class TestExecuteImageGenRejections:
    """这些失败都必须是永久错误：重试不会变好，且不能退回默认值继续跑。"""

    async def test_unknown_model_spec(self, client):
        run_id = await _run(model_key="不存在的档位")
        task_id = await _task(run_id)
        async with async_session() as db:
            with pytest.raises(PermanentTaskError) as exc_info:
                await gen.execute_image_gen(db, await db.get(TaskQueue, task_id))
        assert "未知出图模型档位" in str(exc_info.value)

    async def test_missing_seed_list(self, client):
        run_id = await _run(params_json=json.dumps({}))
        task_id = await _task(run_id)
        async with async_session() as db:
            with pytest.raises(PermanentTaskError) as exc_info:
                await gen.execute_image_gen(db, await db.get(TaskQueue, task_id))
        assert "缺少 seed" in str(exc_info.value)

    async def test_missing_workflow_template(self, client, monkeypatch):
        run_id = await _run()
        task_id = await _task(run_id)
        monkeypatch.setattr(
            gen.wf, "load_template", lambda *_: (_ for _ in ()).throw(FileNotFoundError("无模板"))
        )
        async with async_session() as db:
            with pytest.raises(PermanentTaskError) as exc_info:
                await gen.execute_image_gen(db, await db.get(TaskQueue, task_id))
        assert "工作流模板缺失" in str(exc_info.value)

    async def test_template_file_override_mismatch(self, client, monkeypatch):
        """档位覆盖不到模板里的权重文件名 → 永久失败（不能退回模板默认权重）。"""
        run_id = await _run()
        task_id = await _task(run_id)
        monkeypatch.setattr(gen.wf, "load_template", lambda *_: ({}, "digest"))
        monkeypatch.setattr(
            gen.wf,
            "apply_file_overrides",
            lambda *_a, **_k: (_ for _ in ()).throw(KeyError("checkpoint")),
        )
        async with async_session() as db:
            with pytest.raises(PermanentTaskError) as exc_info:
                await gen.execute_image_gen(db, await db.get(TaskQueue, task_id))
        assert "不符" in str(exc_info.value)

    async def test_ark_provider_delegates_to_seedream(self, client, monkeypatch):
        run_id = await _run(model_key="seedream_test")
        task_id = await _task(run_id)
        called: list[int] = []

        async def _fake_seedream(db, task, run):
            called.append(run.id)

        monkeypatch.setitem(gen.MODEL_SPECS, "seedream_test", {"provider": "ark"})
        monkeypatch.setattr(gen, "_execute_seedream", _fake_seedream)

        async with async_session() as db:
            await gen.execute_image_gen(db, await db.get(TaskQueue, task_id))

        assert called == [run_id]


class TestSeedreamPreparationFailures:
    """云端链路准备阶段的校验：失败必须写 failed，否则批次永久 running。"""

    async def _failed_run(self, run_id: int) -> GenerationRun:
        async with async_session() as db:
            run = await db.get(GenerationRun, run_id)
            return run

    @pytest.mark.parametrize(
        ("params", "ref_ids", "expected"),
        [
            ({}, "[]", "缺少 pose_id"),
            ({"pose_id": "p1"}, "[]", "只支持 1 张素材图"),
            ({"pose_id": "p1"}, '["a","b"]', "只支持 1 张素材图"),
            ({"pose_id": "p1"}, '["不存在的素材"]', "素材不存在或被删除"),
        ],
    )
    async def test_bad_preparation_marks_run_failed(
        self, client, params, ref_ids, expected
    ):
        run_id = await _run(params_json=json.dumps(params), reference_ids=ref_ids)
        task_id = await _task(run_id)

        async with async_session() as db:
            task = await db.get(TaskQueue, task_id)
            run = await db.get(GenerationRun, run_id)
            with pytest.raises(PermanentTaskError) as exc_info:
                await gen._execute_seedream(db, task, run)

        assert expected in str(exc_info.value)
        run = await self._failed_run(run_id)
        assert run.status == "failed"  # 不能永久停在 running
        assert run.error and expected in run.error

    async def test_missing_reference_file_marks_failed(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(gen.settings, "storage_root", tmp_path)
        inspiration_id = "insp-missing-file"
        async with async_session() as db:
            db.add(
                Inspiration(
                    id=inspiration_id,
                    source_type="manual_upload",
                    file_path="images/不存在.png",
                    media_type="image",
                )
            )
            await db.commit()
        run_id = await _run(
            params_json=json.dumps({"pose_id": "p1"}), reference_ids=json.dumps([inspiration_id])
        )
        task_id = await _task(run_id)

        async with async_session() as db:
            task = await db.get(TaskQueue, task_id)
            run = await db.get(GenerationRun, run_id)
            with pytest.raises(PermanentTaskError) as exc_info:
                await gen._execute_seedream(db, task, run)

        assert "素材文件缺失" in str(exc_info.value)
        run = await self._failed_run(run_id)
        assert run.status == "failed"

    async def test_fail_ark_run_truncates_error(self, client):
        run_id = await _run(status="running")
        async with async_session() as db:
            run = await db.get(GenerationRun, run_id)
            await gen._fail_ark_run(db, run, "x" * 5000)

        run = await self._failed_run(run_id)
        assert run.status == "failed"
        assert len(run.error) == 2000
        assert run.failed >= 1


class TestPrepareArkImage:
    def _png_bytes(self, size=(10, 10), mode="RGB", color=(255, 0, 0, 255)):
        import io

        from PIL import Image

        image = Image.new("RGBA" if mode == "RGBA" else "RGB", size, color)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def test_transparent_background_becomes_white(self):
        from PIL import Image
        import io

        # 中间透明：直接 convert("RGB") 会变黑，正确实现应贴到白色画布
        source = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
        buffer = io.BytesIO()
        source.save(buffer, format="PNG")

        out = gen._prepare_ark_image(buffer.getvalue(), output_format="png")
        with Image.open(io.BytesIO(out)) as image:
            assert image.mode == "RGB"
            assert image.getpixel((5, 5)) == (255, 255, 255)

    def test_large_image_is_downscaled_to_max_side(self):
        from PIL import Image
        import io

        out = gen._prepare_ark_image(
            self._png_bytes(size=(3000, 1000)), output_format="jpeg", max_side=2048
        )
        with Image.open(io.BytesIO(out)) as image:
            assert max(image.size) == 2048  # 长边压到 2048
            assert image.size[0] / image.size[1] == pytest.approx(3.0, rel=0.01)

    def test_small_image_keeps_size_and_jpeg_output(self):
        from PIL import Image
        import io

        out = gen._prepare_ark_image(self._png_bytes(size=(64, 32)), output_format="jpeg")
        assert out[:2] == b"\xff\xd8"  # JPEG 魔数
        with Image.open(io.BytesIO(out)) as image:
            assert image.size == (64, 32)

    def test_png_output_format(self):
        out = gen._prepare_ark_image(self._png_bytes(size=(8, 8)), output_format="png")
        assert out[:8] == b"\x89PNG\r\n\x1a\n"
