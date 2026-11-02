"""云端 Seedream 接入：provider 分支校验、提示词、状态与执行器（不调用真实 Ark）。"""

from __future__ import annotations

import json
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from app.config import settings
from app.database import async_session
from app.models.generation import GenerationRun
from app.models.task import TaskQueue
from app.services import pose_gallery
from app.services.task_runners import image_gen as runner

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
    r = client.post("/api/generate/runs", json=_payload(insp))
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
    assert params["provider"] == "ark"
    assert params["size"] == "1536x2048"
    assert params["watermark"] is False
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
    models = client.get("/api/generate/models").json()["items"]
    cloud = next(item for item in models if item["key"] == "seedream_5_0_flash")
    assert cloud["provider"] == "ark"
    assert cloud["cost_per_image_cny"] == 0.12
    assert cloud["requires_upload"] is True
    assert cloud["supports_seed"] is False
    assert cloud["supports_negative"] is False


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
