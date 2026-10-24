"""博主头像批量补全测试：素材裁脸 / 已匹配人脸优先 / 多脸不明确跳过 / 服务不可用。

头像来源是**博主自己的素材**：优先用已匹配到 TA 的人脸检测（bbox），否则扫最近的素材
送人脸子服务检测、取主体脸裁成方形人像。用例把人脸子服务的 embed/health 换成假实现，
图片由测试夹具真实生成——裁剪走真实 PIL，不联网。
"""

import json
from io import BytesIO

import pytest
from PIL import Image
from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.models.face import InspirationFaceDetection
from app.models.person import Blogger
from app.models.task import TaskQueue
from app.services.face_crop import FACE_CROP_SIZE, crop_face
from app.services.task_runners import blogger_avatar_fill as fill
from app.services.task_runners.common import PermanentTaskError


class _FakeFaceClient:
    """假人脸子服务：health 可控，embed 返回预设人脸。"""

    def __init__(self, faces: list[dict] | None = None, health_error: Exception | None = None):
        self.faces = faces or []
        self.health_error = health_error

    async def health(self, timeout: float | None = None) -> dict:
        if self.health_error is not None:
            raise self.health_error
        return {"status": "ok"}

    async def embed(self, image_bytes: bytes, filename: str = "image.jpg") -> dict:
        return {"face_count": len(self.faces), "faces": self.faces}


def _patch_face(monkeypatch, client: _FakeFaceClient) -> None:
    """把任务里用的人脸客户端换成假实现。"""
    monkeypatch.setattr(fill, "face_client", client)


def _upload_material(client, make_image, size=(400, 300)) -> str:
    """上传一张指定尺寸的素材，返回素材 id。"""
    data, ctype = make_image(size=size)
    resp = client.post("/api/inspirations", files={"file": ("m.jpg", data, ctype)})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _link(client, inspiration_id: str, blogger_id: int) -> None:
    """把素材绑到博主（素材归属）。"""
    resp = client.post(f"/api/inspirations/{inspiration_id}/bloggers", json={"person_ids": [blogger_id]})
    assert resp.status_code == 200, resp.text


async def _add_detection(inspiration_id: str, blogger_id: int, bbox: list[float]) -> None:
    """插入一条「已匹配到该博主」的人脸检测（带 bbox）。"""
    async with async_session() as db:
        db.add(
            InspirationFaceDetection(
                inspiration_id=inspiration_id,
                face_index=0,
                embedding=b"\x00" * 2048,
                bbox=json.dumps(bbox),
                det_score=0.95,
                matched_blogger_id=blogger_id,
            )
        )
        await db.commit()


def _avatar_path(blogger_id: int) -> str:
    """头像相对路径（与产物约定一致）。"""
    return f"avatars/avatar_{blogger_id}.jpg"


async def _run_task(db, overwrite: bool = False):
    """建任务并执行（返回执行后的任务行）。"""
    task, total = await fill.create_avatar_fill_task(db, overwrite=overwrite)
    assert task is not None and total >= 1
    await fill.execute_avatar_fill(db, task)
    return task


async def test_targets_need_materials_and_empty_avatar(client, create_blogger, make_image):
    """目标 = 有素材 + 没头像；没素材的不补，已有头像的默认不覆盖。"""
    with_material = create_blogger(name="有素材博")
    _link(client, _upload_material(client, make_image), with_material["id"])
    no_material = create_blogger(name="没素材博")
    with_avatar = create_blogger(name="已有头像博")
    _link(client, _upload_material(client, make_image), with_avatar["id"])

    async with async_session() as db:
        row = await db.get(Blogger, with_avatar["id"])
        row.avatar_path = "avatars/avatar_manual.jpg"
        await db.commit()

        ids = {b.id for b in await fill.list_avatar_fill_targets(db)}
        assert with_material["id"] in ids
        assert no_material["id"] not in ids
        assert with_avatar["id"] not in ids
        # overwrite=True 才把已有头像的纳入
        all_ids = {b.id for b in await fill.list_avatar_fill_targets(db, overwrite=True)}
        assert with_avatar["id"] in all_ids


async def test_execute_uses_matched_detection(client, create_blogger, make_image, monkeypatch):
    """有「已匹配到该博主」的检测：直接按它的 bbox 裁，不调人脸服务。"""
    blogger = create_blogger(name="有匹配检测博")
    insp_id = _upload_material(client, make_image, size=(400, 300))
    _link(client, insp_id, blogger["id"])
    await _add_detection(insp_id, blogger["id"], [100, 50, 250, 200])
    _patch_face(monkeypatch, _FakeFaceClient(faces=[]))  # embed 不该被调用

    async with async_session() as db:
        task = await _run_task(db)

    assert task.result["updated"] == 1
    detail = task.result["results"][0]
    assert detail["status"] == "updated"
    assert "已匹配的人脸" in detail["source"]

    path = settings.storage_root / _avatar_path(blogger["id"])
    assert path.is_file()
    with Image.open(path) as avatar:
        assert avatar.size == (FACE_CROP_SIZE, FACE_CROP_SIZE)


async def test_execute_scans_recent_materials(client, create_blogger, make_image, monkeypatch):
    """没有检测记录时：扫素材送人脸服务，取单张脸裁头像。"""
    blogger = create_blogger(name="扫素材博")
    insp_id = _upload_material(client, make_image, size=(400, 300))
    _link(client, insp_id, blogger["id"])
    _patch_face(
        monkeypatch,
        _FakeFaceClient(faces=[{"bbox": [100, 50, 250, 200], "det_score": 0.9}]),
    )

    async with async_session() as db:
        task = await _run_task(db)

    assert task.result["updated"] == 1
    assert "从素材" in task.result["results"][0]["source"]
    assert (settings.storage_root / _avatar_path(blogger["id"])).is_file()


async def test_execute_skips_ambiguous_multiface(client, create_blogger, make_image, monkeypatch):
    """多张脸且主次不分明（合照/路人抢镜）：不裁，记跳过原因。"""
    blogger = create_blogger(name="多脸博")
    insp_id = _upload_material(client, make_image, size=(400, 300))
    _link(client, insp_id, blogger["id"])
    _patch_face(
        monkeypatch,
        _FakeFaceClient(
            faces=[
                {"bbox": [100, 50, 250, 200], "det_score": 0.9},
                {"bbox": [0, 0, 140, 140], "det_score": 0.9},  # 面积接近，主体不明确
            ]
        ),
    )

    async with async_session() as db:
        task = await _run_task(db)

    assert task.result["updated"] == 0
    assert task.result["skipped"] == 1
    assert "没检出可用的主体脸" in task.result["results"][0]["reason"]
    assert not (settings.storage_root / _avatar_path(blogger["id"])).exists()


async def test_execute_accepts_small_face(client, create_blogger, make_image, monkeypatch):
    """人脸只占画面 0.25% 也采用（穿搭素材多是全身街拍，脸本来就小）。

    回归：门槛曾是 1.5%，把 452 位里的 314 位全判成「没检出明确的人脸」，而实际
    每张素材都检出了干净的单张脸——只是小。列表头像只显示 40~72px，0.2% 够用。
    """
    blogger = create_blogger(name="小脸博")
    insp_id = _upload_material(client, make_image, size=(400, 300))
    _link(client, insp_id, blogger["id"])
    # 20x15 = 300 px²，画面 120000 px² → 0.25%
    _patch_face(
        monkeypatch,
        _FakeFaceClient(faces=[{"bbox": [10, 10, 30, 25], "det_score": 0.99}]),
    )

    async with async_session() as db:
        task = await _run_task(db)

    assert task.result["updated"] == 1
    assert (settings.storage_root / _avatar_path(blogger["id"])).is_file()


async def test_execute_skips_when_face_too_small(client, create_blogger, make_image, monkeypatch):
    """人脸不到画面 0.2%（这张是 0.05%）：放大成头像只会是糊块，跳过。"""
    blogger = create_blogger(name="针眼脸博")
    insp_id = _upload_material(client, make_image, size=(400, 300))
    _link(client, insp_id, blogger["id"])
    # 5x4 = 20 px² / 120000 px² ≈ 0.017%
    _patch_face(
        monkeypatch,
        _FakeFaceClient(faces=[{"bbox": [10, 10, 15, 14], "det_score": 0.99}]),
    )

    async with async_session() as db:
        task = await _run_task(db)

    assert task.result["updated"] == 0
    assert task.result["skipped"] == 1


async def test_execute_prefers_biggest_face_among_materials(client, create_blogger, make_image, monkeypatch):
    """多张素材都有脸时挑**脸最大**的那张（不是最早/最新的一张）。"""
    blogger = create_blogger(name="多素材挑脸博")
    # 先上传的（created_at 更早）是大脸，后上传的是小脸
    big_id = _upload_material(client, make_image, size=(400, 300))
    small_id = _upload_material(client, make_image, size=(400, 300))
    _link(client, big_id, blogger["id"])
    _link(client, small_id, blogger["id"])

    faces_by_call = [
        [{"bbox": [100, 50, 250, 200], "det_score": 0.9}],  # 22500/120000 = 18.8%
        [{"bbox": [10, 10, 30, 25], "det_score": 0.9}],  # 0.25%
    ]

    class _SeqClient(_FakeFaceClient):
        def __init__(self):
            super().__init__(faces=[])
            self.calls = 0

        async def embed(self, image_bytes: bytes, filename: str = "image.jpg") -> dict:
            faces = faces_by_call[min(self.calls, len(faces_by_call) - 1)]
            self.calls += 1
            return {"face_count": len(faces), "faces": faces}

    _patch_face(monkeypatch, _SeqClient())

    async with async_session() as db:
        task = await _run_task(db)

    assert task.result["updated"] == 1
    # 第一张就够清楚（18.8% ≥ 0.8% 早停），来源说明里带着占比
    assert "占画面" in task.result["results"][0]["source"]


async def test_execute_fails_fast_when_face_service_down(client, create_blogger, make_image, monkeypatch):
    """人脸服务不可用：整个任务失败并说明原因，不能把几百位记成「没检出人脸」。"""
    blogger = create_blogger(name="服务挂了博")
    _link(client, _upload_material(client, make_image), blogger["id"])
    _patch_face(monkeypatch, _FakeFaceClient(health_error=RuntimeError("connect refused")))

    async with async_session() as db:
        task, _total = await fill.create_avatar_fill_task(db)
        assert task is not None
        with pytest.raises(PermanentTaskError) as exc:
            await fill.execute_avatar_fill(db, task)
    assert "人脸识别子服务不可用" in str(exc.value)


async def test_execute_cancelled_marks_status(client, create_blogger, make_image, monkeypatch):
    """取消：状态变 cancelled（worker 不会覆盖成 success），一个都不写。"""
    blogger = create_blogger(name="取消博")
    _link(client, _upload_material(client, make_image), blogger["id"])
    _patch_face(monkeypatch, _FakeFaceClient(faces=[{"bbox": [10, 10, 55, 55], "det_score": 0.9}]))

    async def fake_cancelled(_db, _task) -> bool:
        return True

    monkeypatch.setattr(fill, "is_cancelled", fake_cancelled)

    async with async_session() as db:
        task, _total = await fill.create_avatar_fill_task(db)
        assert task is not None
        await fill.execute_avatar_fill(db, task)
        assert task.status == "cancelled"
        assert task.result["cancelled"] is True
        assert task.result["updated"] == 0

    async with async_session() as db:
        row = await db.get(Blogger, blogger["id"])
    assert row is not None and not (row.avatar_path or "").strip()


async def test_task_registered_in_handlers():
    """新任务类型必须在 TASK_HANDLERS 里登记，否则 worker 认领后报「未知任务类型」。"""
    from app.services.task_runner import TASK_HANDLERS

    assert TASK_HANDLERS["blogger_avatar_fill"] is fill.execute_avatar_fill


async def test_api_creates_task_or_400(client, create_blogger, make_image):
    """接口：有素材 → 201 返回 task_id/total；没素材 → 400 且提示人话。"""
    create_blogger(name="接口没素材博")
    resp = client.post("/api/bloggers/avatars/fill-missing", json={})
    assert resp.status_code == 400
    assert "素材" in resp.json()["detail"]

    blogger = create_blogger(name="接口有素材博")
    _link(client, _upload_material(client, make_image), blogger["id"])
    resp = client.post("/api/bloggers/avatars/fill-missing", json={})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["total"] >= 1
    assert body["task_id"] > 0

    async with async_session() as db:
        rows = list(
            (await db.execute(select(TaskQueue).where(TaskQueue.type == "blogger_avatar_fill")))
            .scalars()
            .all()
        )
    assert any(r.id == body["task_id"] for r in rows)


def test_crop_avatar_is_square_jpeg():
    """裁出来的头像必须是正方形 JPEG（前端按方形容器显示，非方图会被拉伸）。"""
    source = Image.new("RGB", (400, 300), (120, 120, 120))
    buf = BytesIO()
    source.save(buf, format="JPEG")
    avatar = crop_face(buf.getvalue(), [100, 50, 250, 200], FACE_CROP_SIZE)
    with Image.open(BytesIO(avatar)) as image:
        assert image.format == "JPEG"
        assert image.size == (FACE_CROP_SIZE, FACE_CROP_SIZE)
