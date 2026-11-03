"""云端 Seedream 接入：provider 分支校验、提示词、状态与执行器（不调用真实 Ark）。"""

from __future__ import annotations

import json
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image

from app.config import settings
from app.database import async_session
from app.models.generation import GenerationRun
from app.models.inspiration import Inspiration
from app.models.task import TaskQueue
from app.services import generation_service, pose_gallery
from app.services.task_runners import image_gen as runner
from app.services.task_runners.common import PermanentTaskError, RecoverableTaskError

POSE_ID = "full_std_P001_front"
POSE = {
    "id": POSE_ID,
    "name": "S形站姿",
    "angle": "正面",
    "system": "全身单人・标准系",
    "category": "站姿",
}


def _png_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (64, 96), "beige").save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def fake_cloud(monkeypatch, tmp_path):
    """打开云端开关 + 假姿势目录；不发起任何真实请求。"""
    monkeypatch.setattr(settings, "ark_enabled", True)
    monkeypatch.setattr(settings, "ark_api_key", "test-key")
    monkeypatch.setattr(settings, "seedream_daily_budget_cny", 0)
    pose_file = tmp_path / f"{POSE_ID}.png"
    pose_file.write_bytes(_png_bytes())

    async def _get_pose(pose_id: str) -> dict:
        if pose_id != POSE_ID:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="姿势不存在")
        return dict(POSE)

    async def _ensure_pose_image(pose_id: str):
        return pose_file

    monkeypatch.setattr(pose_gallery, "get_pose", _get_pose)
    monkeypatch.setattr(pose_gallery, "ensure_pose_image", _ensure_pose_image)
    return pose_file


def _payload(insp_id: str, **extra) -> dict:
    return {
        "kind": "i2i",
        "model_key": "seedream_5_0_flash",
        "prompt": "x",
        "reference_ids": [insp_id],
        "pose_id": POSE_ID,
        "count": 1,
        **extra,
    }


async def test_create_seedream_run_uses_ark_branch(client, upload, fake_cloud):
    insp = upload().json()["id"]
    r = client.post("/api/generate/runs", json=_payload(insp, prompt="换成红色背景"))
    assert r.status_code == 200, r.text
    body = r.json()
    async with async_session() as db:
        run = await db.get(GenerationRun, body["run_id"])
        params = json.loads(run.params_json)
    assert run.model_key == "seedream_5_0_flash"
    assert run.kind == "i2i"
    assert run.workflow_name == "ark_seedream"
    assert run.workflow_hash.startswith("ark:")
    assert "图1" in run.prompt and "图2" in run.prompt
    assert "姿势细节" in run.prompt
    # P0-1：用户在描述框写的要求必须进入云端提示词（历史实现整段丢弃）
    assert "换成红色背景" in run.prompt
    assert params["provider"] == "ark"
    assert params["size"] == "1536x2048"
    assert params["watermark"] is False
    # P1-3：云端没有 seed，不能写假的 seed_base/seeds；接口返回 seed=None
    assert "seed_base" not in params and "seeds" not in params
    assert body["seed"] is None
    assert run.total == 1


async def test_create_seedream_rejects_bad_params(client, upload, fake_cloud):
    insp = upload().json()["id"]
    cases = [
        (_payload(insp, count=2), "每次只生成 1 张"),
        (_payload(insp, reference_ids=[]), "参考"),
        (_payload(insp, pose_id=None), "姿态"),
        (_payload(insp, size="512x512"), "像素"),
    ]
    for payload, keyword in cases:
        r = client.post("/api/generate/runs", json=payload)
        assert r.status_code == 400, (keyword, r.text)
        assert keyword in r.text, (keyword, r.text)


async def test_cloud_disabled_hides_model_and_rejects_run(client, upload, monkeypatch):
    # 本机 .env 可能已配置 ARK_ENABLED=true；这个用例专门验证关闭态
    monkeypatch.setattr(settings, "ark_enabled", False)
    monkeypatch.setattr(settings, "ark_api_key", "")
    insp = upload().json()["id"]
    models = client.get("/api/generate/models").json()["items"]
    assert all(item["key"] != "seedream_5_0_flash" for item in models)

    monkeypatch.setattr(settings, "ark_enabled", False)
    r = client.post("/api/generate/runs", json=_payload(insp))
    assert r.status_code == 400
    assert "未启用" in r.text


async def test_cloud_enabled_exposes_model_metadata(client, monkeypatch):
    monkeypatch.setattr(settings, "ark_enabled", True)
    monkeypatch.setattr(settings, "ark_api_key", "test-key")
    monkeypatch.setattr(settings, "seedream_max_poses", 3)
    models = client.get("/api/generate/models").json()["items"]
    cloud = next(item for item in models if item["key"] == "seedream_5_0_flash")
    assert cloud["provider"] == "ark"
    assert cloud["cost_per_image_cny"] == 0.12
    assert cloud["requires_upload"] is True
    assert cloud["supports_seed"] is False
    assert cloud["supports_negative"] is False
    # P2-12：云端只接受 1 张素材图；姿态图由 pose_id 另算
    assert cloud["max_refs"] == 1
    # P3：多姿态上限来自配置（而不是前端硬编码 10）
    assert cloud["max_poses"] == 3


async def test_execute_seedream_writes_candidate(client, upload, fake_cloud, monkeypatch):
    insp = upload().json()["id"]
    r = client.post("/api/generate/runs", json=_payload(insp))
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args

        async def generate(self, **kwargs):
            assert len(kwargs["images"]) == 2
            return SimpleNamespace(
                image=_png_bytes(), request_id="req-test", usage={}, elapsed_s=1.2
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(runner, "SeedreamClient", _FakeClient)
    async with async_session() as db:
        run = await db.get(GenerationRun, run_id)
        task = await db.get(TaskQueue, run.task_id)
        await runner.execute_image_gen(db, task)
        await db.refresh(run)
        target = settings.storage_root / run.batch_dir / "01_seed-0.png"
        assert target.exists()
        assert run.status == "done"
        assert run.done == 1
        assert task.progress == 100
        params = json.loads(run.params_json)
        assert params["ark_request_id"] == "req-test"
        assert "ark_elapsed_s" in params


async def test_cloud_daily_budget_rejects_second_run(client, upload, fake_cloud, monkeypatch):
    """日预算软上限：当天已建 1 个云端批次后，再提交超过预算直接 400。"""
    # 先不限预算建第 1 个批次，再把预算压到 0.12：第 2 个必然超额
    monkeypatch.setattr(settings, "seedream_daily_budget_cny", 0)
    insp = upload().json()["id"]
    first = client.post("/api/generate/runs", json=_payload(insp))
    assert first.status_code == 200, first.text
    monkeypatch.setattr(settings, "seedream_daily_budget_cny", 0.12)
    second = client.post("/api/generate/runs", json=_payload(insp))
    assert second.status_code == 400
    assert "预算" in second.text


async def test_cloud_status_reports_ark_ready(client, monkeypatch):
    monkeypatch.setattr(settings, "ark_enabled", True)
    monkeypatch.setattr(settings, "ark_api_key", "test-key")
    status = client.get("/api/generate/status").json()
    assert status["ark"]["ready"] is True
    assert status["ark"]["model"] == settings.seedream_model


# ── 2026-11-03 评审修复：P0-1/P0-2/P1-3/P1-4/P1-5/P2-2/P2-3/P2-4 ──


class _FakeSeedream:
    """记录构造/调用参数的假客户端（不发起真实请求）。"""

    instances: list[_FakeSeedream] = []

    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs
        self.generate_kwargs: dict = {}
        _FakeSeedream.instances.append(self)

    async def generate(self, **kwargs):
        self.generate_kwargs = kwargs
        return SimpleNamespace(
            image=_png_bytes(),
            size=str(kwargs.get("size") or ""),
            request_id="req-test",
            usage={"generated_images": 1},
            elapsed_s=1.2,
        )

    async def aclose(self) -> None:
        return None


def test_ark_prompt_prioritizes_user_request_and_truncates():
    """P0-1 + P2-10：用户要求进入提示词；超长时保留用户要求、截断姿势细节。"""
    text = generation_service._ark_prompt_text(
        POSE, "原始姿势细节", "模糊", "换成红色背景"
    )
    assert "换成红色背景" in text
    assert len(text) <= generation_service._ARK_PROMPT_LIMIT

    long_user = "换成红色背景" * 80
    long_text = generation_service._ark_prompt_text(POSE, "姿" * 100, None, long_user)
    assert "换成红色背景" in long_text
    assert len(long_text) <= generation_service._ARK_PROMPT_LIMIT


def test_ark_budget_day_start_is_local_midnight():
    """P2-1：预算日按本地零点切，而不是 UTC 零点（中国用户 08:00 才重置）。"""
    start = generation_service._ark_budget_day_start()
    local_now = generation_service.datetime.now().astimezone()
    expected = (
        local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        .astimezone(generation_service.UTC)
        .replace(tzinfo=None)
    )
    assert abs((start - expected).total_seconds()) < 2


async def test_cloud_rejects_dual_person_pose(client, upload, fake_cloud, monkeypatch):
    """P1-5：双人姿态 + 单张素材图会被合成/截断，一期直接拒绝。"""

    async def _get_pose(pose_id: str) -> dict:
        pose = dict(POSE)
        pose["system"] = "双人合照（情侣）"
        return pose

    monkeypatch.setattr(pose_gallery, "get_pose", _get_pose)
    insp = upload().json()["id"]
    r = client.post("/api/generate/runs", json=_payload(insp))
    assert r.status_code == 400, r.text
    assert "双人" in r.text


async def test_retry_preserves_cloud_size_and_watermark(client, upload, fake_cloud):
    """P1-4：同参重跑不能把云端 size/watermark 悄悄退回默认值。"""
    insp = upload().json()["id"]
    first = client.post(
        "/api/generate/runs", json=_payload(insp, size="1K", watermark=True)
    )
    assert first.status_code == 200, first.text

    async with async_session() as db:
        out = await generation_service.retry_run(db, first.json()["run_id"])
        await db.commit()
        new_run = await db.get(GenerationRun, out["run_id"])
        params = json.loads(new_run.params_json)

    assert out["seed"] is None
    assert params["size"] == "1K"
    assert params["watermark"] is True


@pytest.mark.parametrize("failure", ["missing_material", "pose_503", "corrupt_material"])
async def test_prep_failure_marks_run_failed(
    client, upload, fake_cloud, monkeypatch, failure: str
):
    """P0-2：准备阶段失败必须把批次写成 failed，而不是永久停在 running。"""
    insp = upload().json()["id"]
    created = client.post("/api/generate/runs", json=_payload(insp))
    assert created.status_code == 200, created.text
    run_id = created.json()["run_id"]

    async with async_session() as db:
        run = await db.get(GenerationRun, run_id)
        task = await db.get(TaskQueue, run.task_id)
        ref_ids = json.loads(run.reference_ids)
        insp_row = await db.get(Inspiration, ref_ids[0])
        material_path = settings.storage_root / insp_row.file_path

        if failure == "missing_material":
            material_path.unlink()
            with pytest.raises(PermanentTaskError):
                await runner.execute_image_gen(db, task)
        elif failure == "pose_503":

            async def _ensure_pose_unavailable(pose_id: str):
                raise HTTPException(status_code=503, detail="姿势图库暂不可用")

            monkeypatch.setattr(
                pose_gallery, "ensure_pose_image", _ensure_pose_unavailable
            )
            with pytest.raises(RecoverableTaskError):
                await runner.execute_image_gen(db, task)
        else:
            material_path.write_bytes(b"not-an-image")
            with pytest.raises(PermanentTaskError):
                await runner.execute_image_gen(db, task)

        await db.refresh(run)
        assert run.status == "failed", run.status
        assert run.error, "失败必须给出可读原因"


async def test_execute_seedream_writes_back_actual_size(
    client, upload, fake_cloud, monkeypatch
):
    """P2-3：响应的真实尺寸要回写参数快照（档位名 ≠ 实际输出像素）。"""
    insp = upload().json()["id"]
    run_id = client.post("/api/generate/runs", json=_payload(insp)).json()["run_id"]
    monkeypatch.setattr(runner, "SeedreamClient", _FakeSeedream)
    _FakeSeedream.instances.clear()

    async with async_session() as db:
        run = await db.get(GenerationRun, run_id)
        task = await db.get(TaskQueue, run.task_id)
        await runner.execute_image_gen(db, task)
        await db.refresh(run)
        params = json.loads(run.params_json)

    assert params["size"] == "1536x2048"
    assert params["width"] == 1536
    assert params["height"] == 2048
    assert params["ark_model_id"] == settings.seedream_model


async def test_execute_seedream_uses_run_model_id(
    client, upload, fake_cloud, monkeypatch
):
    """P2-4：执行时用批次参数里的 model_id，不受提交后改全局配置影响。"""
    insp = upload().json()["id"]
    run_id = client.post("/api/generate/runs", json=_payload(insp)).json()["run_id"]
    original_model = settings.seedream_model
    monkeypatch.setattr(settings, "seedream_model", "changed-after-submit")
    monkeypatch.setattr(runner, "SeedreamClient", _FakeSeedream)
    _FakeSeedream.instances.clear()

    async with async_session() as db:
        run = await db.get(GenerationRun, run_id)
        task = await db.get(TaskQueue, run.task_id)
        await runner.execute_image_gen(db, task)

    assert _FakeSeedream.instances[0].kwargs["model"] == original_model


async def test_execute_seedream_second_budget_check(
    client, upload, fake_cloud, monkeypatch
):
    """P2-2：排队期间预算被用满 → 执行前二次检查拦下，不再调用云端。"""
    insp = upload().json()["id"]
    run_id = client.post("/api/generate/runs", json=_payload(insp)).json()["run_id"]
    monkeypatch.setattr(settings, "seedream_daily_budget_cny", 0.05)
    monkeypatch.setattr(runner, "SeedreamClient", _FakeSeedream)
    _FakeSeedream.instances.clear()

    async with async_session() as db:
        run = await db.get(GenerationRun, run_id)
        task = await db.get(TaskQueue, run.task_id)
        with pytest.raises(PermanentTaskError):
            await runner.execute_image_gen(db, task)
        await db.refresh(run)

    assert run.status == "failed"
    assert "预算" in (run.error or "")
    assert _FakeSeedream.instances == [], "超预算时不应发起云端请求"


async def test_execute_seedream_marks_run_cancelled(
    client, upload, fake_cloud, monkeypatch
):
    """取消语义：任务已取消 → 批次置 cancelled，不发起云端请求。"""
    insp = upload().json()["id"]
    run_id = client.post("/api/generate/runs", json=_payload(insp)).json()["run_id"]
    monkeypatch.setattr(runner, "SeedreamClient", _FakeSeedream)
    _FakeSeedream.instances.clear()

    async with async_session() as db:
        run = await db.get(GenerationRun, run_id)
        task = await db.get(TaskQueue, run.task_id)
        task.status = "cancelled"
        await db.commit()
        await runner.execute_image_gen(db, task)
        await db.refresh(run)

    assert run.status == "cancelled"
    assert _FakeSeedream.instances == [], "取消后不应发起云端请求"
