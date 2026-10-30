"""图生图换姿势：提交校验 + 执行器把姿势图作为**最后一张参考图**上传。

覆盖四件事：

1. 姿势只对图生图开放（文生图没有可替换的主体）；
2. 姿势必须存在（源站目录里有），且会占一张参考额度；
3. `pose_id` 写进参数快照，`同参重跑` 不丢姿势；
4. 执行器把姿势图接在参考图**末尾**（提示词约定「第 1 张是主体、最后一张是姿势」）。
"""

from __future__ import annotations

import json
from io import BytesIO

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

POSE_ID = "full_std_P001_front"

"""假目录：一条姿势就够用（换姿势链路不依赖姿势条数）。"""
RAW_POSE = {
    "id": POSE_ID,
    "体系": "全身单人・标准系",
    "分类": "站姿",
    "姿势名称": "S形站姿",
    "角度": "正面",
    "缩略图": "thumbs/a.jpg",
    "原图": "img/a.png",
    "姿势编号": "P001",
}


def _png_bytes(color: str = "beige") -> bytes:
    buf = BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, format="PNG")
    return buf.getvalue()


class _PoseClient:
    """最小假 ComfyUI：记录上传与提交，返回固定 PNG。"""

    def __init__(self, *args, **kwargs) -> None:
        self.uploaded: list[str] = []
        self.submitted: list[dict] = []

    async def system_stats(self) -> dict:
        return {"devices": [{"vram_total": 20 * 2**30, "vram_free": 19 * 2**30}]}

    async def upload_image(self, data: bytes, filename: str) -> str:
        self.uploaded.append(filename)
        return f"up_{filename}"

    async def submit(self, workflow: dict, client_id: str = "x") -> str:
        self.submitted.append(workflow)
        return "pid-pose"

    async def wait_for_prompt(self, prompt_id, *, timeout, on_tick=None, tick_interval=2.0):
        return {"outputs": {"10": {"images": [{"filename": "out.png", "subfolder": ""}]}}}

    async def fetch_image(self, filename: str, subfolder: str = "", folder_type: str = "output") -> bytes:
        return _png_bytes()

    async def interrupt(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


async def _add_reference_image(insp_id: str) -> None:
    """造一张真实存在的参考素材（文件落到测试 storage）。"""
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
                media_type="image",
                quality_status="approved",
            )
        )
        await db.commit()


@pytest.fixture
def fake_pose_gallery(monkeypatch, tmp_path):
    """假姿势目录 + 缓存路径改到 tmp（不联网、不污染 session 级 storage）。"""
    items = [pose_gallery._normalize(RAW_POSE)]
    items = [item for item in items if item]

    async def _fetch_catalog() -> list[dict]:
        return items

    monkeypatch.setattr(pose_gallery, "_fetch_catalog", _fetch_catalog)
    monkeypatch.setattr(pose_gallery, "cache_dir", lambda: tmp_path)
    monkeypatch.setattr(pose_gallery, "catalog_cache_path", lambda: tmp_path / "catalog.json")
    return items


@pytest.fixture(autouse=True)
def _fast_vram(monkeypatch) -> None:
    """显存守门必须立刻放行：真实实现会去问 Ollama，可能等 900 秒。"""
    from app.services.comfyui import vram as vram_mod

    async def _no_resident() -> list[dict]:
        return []

    monkeypatch.setattr(vram_mod, "ollama_resident", _no_resident)
    monkeypatch.setattr(vram_mod, "CHECK_INTERVAL", 0)


# ── 提交校验 ──


async def test_pose_rejected_for_t2i(client, fake_pose_gallery):
    """文生图没有可替换的主体 → 提交前直接拒。"""
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await generation_service.create_run(
                db, {"kind": "t2i", "prompt": "米色开衫", "pose_id": POSE_ID}
            )
        assert exc.value.status_code == 400
        assert "参考图改款" in str(exc.value.detail)


async def test_pose_rejected_when_unknown(client, fake_pose_gallery):
    await _add_reference_image("ref-pose-unknown")
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await generation_service.create_run(
                db,
                {
                    "kind": "i2i",
                    "prompt": "换姿势",
                    "reference_ids": ["ref-pose-unknown"],
                    "pose_id": "not_exist_pose",
                },
            )
        assert exc.value.status_code == 404


async def test_pose_counts_against_reference_limit(client, fake_pose_gallery):
    """姿势图占一张参考额度：10 张主体参考 + 姿势 → 超上限，提交前就报错。"""
    ids: list[str] = []
    for idx in range(10):
        insp_id = f"ref-pose-cap-{idx}"
        await _add_reference_image(insp_id)
        ids.append(insp_id)
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await generation_service.create_run(
                db,
                {
                    "kind": "i2i",
                    "prompt": "换姿势",
                    "reference_ids": ids,
                    "pose_id": POSE_ID,
                },
            )
        assert "最多 9 张" in str(exc.value.detail)


async def test_create_run_records_pose_in_params(client, fake_pose_gallery):
    await _add_reference_image("ref-pose-ok")
    async with async_session() as db:
        out = await generation_service.create_run(
            db,
            {
                "kind": "i2i",
                "prompt": "保持人物与服装，按参考姿势重新生成",
                "reference_ids": ["ref-pose-ok"],
                "pose_id": POSE_ID,
                "count": 1,
            },
        )
        run = await db.get(GenerationRun, out["run_id"])
        params = json.loads(run.params_json)

    assert run.kind == "i2i"
    assert params["pose_id"] == POSE_ID
    assert json.loads(run.reference_ids) == ["ref-pose-ok"]


async def test_retry_run_keeps_pose(client, fake_pose_gallery):
    """同参重跑不能丢姿势（丢了会静默出一张「姿势不对」的图）。"""
    await _add_reference_image("ref-pose-retry")
    async with async_session() as db:
        first = await generation_service.create_run(
            db,
            {
                "kind": "i2i",
                "prompt": "换姿势",
                "reference_ids": ["ref-pose-retry"],
                "pose_id": POSE_ID,
                "count": 1,
            },
        )
        second = await generation_service.retry_run(db, first["run_id"], seed=1234)
        run2 = await db.get(GenerationRun, second["run_id"])

    assert json.loads(run2.params_json)["pose_id"] == POSE_ID


# ── 执行器：姿势图作为最后一张参考上传 ──


async def test_runner_uploads_pose_as_last_reference(
    client, fake_pose_gallery, monkeypatch, tmp_path
):
    pose_file = tmp_path / f"{POSE_ID}.png"
    pose_file.write_bytes(_png_bytes("white"))

    async def _ensure(_pose_id: str):
        return pose_file

    monkeypatch.setattr(pose_gallery, "ensure_pose_image", _ensure)

    client_obj = _PoseClient()

    def _client_factory(*args, **kwargs):
        return client_obj

    monkeypatch.setattr(runner, "ComfyUIClient", _client_factory)
    await _add_reference_image("ref-pose-run")

    async with async_session() as db:
        out = await generation_service.create_run(
            db,
            {
                "kind": "i2i",
                "prompt": "保持人物长相与服装，按最后一张参考图的姿势生成",
                "reference_ids": ["ref-pose-run"],
                "pose_id": POSE_ID,
                "count": 1,
            },
        )
        task = await db.get(TaskQueue, out["task_id"])
        await runner.execute_image_gen(db, task)
        run = await db.get(GenerationRun, out["run_id"])

    run_id = out["run_id"]
    # 顺序即参考顺序：主体参考在前，姿势图在最后
    assert client_obj.uploaded == [f"gen_ref_{run_id}_1.png", f"gen_pose_{run_id}.png"]
    assert len(client_obj.submitted) == 1
    workflow = client_obj.submitted[0]
    assert workflow["6"]["inputs"]["images.image_1"] == ["4", 0]
    # 第 2 张参考（姿势图）动态追加了 LoadImage 节点并挂到 images.image_2
    assert workflow["6"]["inputs"]["images.image_2"] == ["21", 0]
    assert workflow["21"]["inputs"]["image"] == f"up_gen_pose_{run_id}.png"
    assert run.status == "done"
