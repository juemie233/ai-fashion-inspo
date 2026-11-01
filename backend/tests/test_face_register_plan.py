"""批量注册计划 / 批量注册接口（方案阶段 1）的服务与接口用例。"""

from __future__ import annotations

import numpy as np

from app.database import async_session
from app.models.face import InspirationFaceDetection
from app.services.person.face_register_plan import build_register_plan, register_bloggers_batch


def _upload(client, make_image, size=(64, 64)) -> str:
    data, ctype = make_image(size=size)
    resp = client.post("/api/inspirations", files={"file": ("m.jpg", data, ctype)})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _link(client, inspiration_id: str, blogger_id: int) -> None:
    resp = client.post(
        f"/api/inspirations/{inspiration_id}/bloggers", json={"person_ids": [blogger_id]}
    )
    assert resp.status_code == 200, resp.text


def _create_blogger(client, name: str) -> dict:
    resp = client.post("/api/bloggers", json={"name": name, "platform": "douyin"})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _add_detection(db, inspiration_id: str, embedding: np.ndarray) -> None:
    db.add(
        InspirationFaceDetection(
            inspiration_id=inspiration_id,
            face_index=0,
            embedding=embedding.astype(np.float32).tobytes(),
            det_score=0.9,
        )
    )


async def test_build_register_plan_finds_cluster(client, make_image):
    """未注册博主有 ≥3 次未匹配单脸检测 → 计划里出现该博主及建议素材。"""
    blogger = _create_blogger(client, "批量计划博主")
    base = np.random.default_rng(0).normal(size=512).astype(np.float32)
    base /= np.linalg.norm(base)
    inspiration_ids = []
    for _ in range(3):
        iid = _upload(client, make_image)
        _link(client, iid, blogger["id"])
        inspiration_ids.append(iid)
    async with async_session() as db:
        for iid in inspiration_ids:
            await _add_detection(db, iid, base)
        await db.commit()

    async with async_session() as db:
        plan = await build_register_plan(db, limit=50)

    item = next(i for i in plan["items"] if i["person_id"] == blogger["id"])
    assert item["registered"] is False
    assert item["inspiration_count"] == 3
    assert len(item["clusters"]) == 1
    cluster = item["clusters"][0]
    assert cluster["size"] == 3
    assert set(cluster["suggested_inspiration_ids"]) == set(inspiration_ids)
    assert len(cluster["cover_detection_ids"]) == 3
    assert plan["summary"]["with_candidates"] >= 1


async def test_register_plan_endpoint_and_batch_validation(client, make_image):
    """接口可用：计划返回结构；批量注册拒绝不属于该博主的素材。"""
    blogger = _create_blogger(client, "批量注册博主")
    resp = client.post("/api/face-scan/register-plan", json={"limit": 20})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {"items", "summary"} <= set(body)
    assert {"total", "with_candidates", "no_candidates"} <= set(body["summary"])

    # 素材未关联该博主：批量注册应逐条失败，而不是静默注册
    data, ctype = make_image()
    up = client.post("/api/inspirations", files={"file": ("x.jpg", data, ctype)})
    orphan_id = up.json()["id"]
    batch = client.post(
        "/api/face-scan/register-batch",
        json={
            "items": [
                {
                    "person_type": "blogger",
                    "person_id": blogger["id"],
                    "inspiration_ids": [orphan_id],
                }
            ]
        },
    )
    assert batch.status_code == 200, batch.text
    row = batch.json()["items"][0]
    assert row["ok"] is False
    assert "不属于该博主" in row["error"]


async def test_register_batch_serial_success(client, make_image, monkeypatch):
    """批量注册逐条调用现有 register_blogger_face；成功返回 face_count。"""
    blogger = _create_blogger(client, "批量成功博主")
    iid = _upload(client, make_image)
    _link(client, iid, blogger["id"])

    async def fake_register(db, blogger_id, image_bytes_list, inspiration_ids=None):
        assert blogger_id == blogger["id"]
        assert inspiration_ids == [iid]
        return {"photos_used": 1, "warnings": []}

    monkeypatch.setattr(
        "app.services.person.face_register_plan.register_blogger_face", fake_register
    )
    async with async_session() as db:
        out = await register_bloggers_batch(
            db,
            [
                type(
                    "Item",
                    (),
                    {
                        "person_type": "blogger",
                        "person_id": blogger["id"],
                        "inspiration_ids": [iid],
                    },
                )()
            ],
        )
    assert out["succeeded"] == 1
    assert out["items"][0]["ok"] is True
    assert out["items"][0]["face_count"] == 1
