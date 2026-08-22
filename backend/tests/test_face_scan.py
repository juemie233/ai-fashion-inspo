"""人脸库扫描功能测试：扫描任务（增量/全量）、候选匹配任务、结果查询、审核确认。

覆盖链路：
- 扫描任务创建（POST /api/face-scan/start）与执行（execute_face_scan）：
  有人脸素材写人脸明细、无脸素材写占位记录、auto_match 自动创建匹配任务；
- 匹配任务执行（execute_face_match）产出 pending 候选；
- 结果查询（聚合/明细/未匹配）；
- 审核确认（confirm 写关联表 / reject 清空 / undo 解除关联，均幂等）；
- 运行中取消（仅 face_scan/face_match 类型可取消 running）。
"""

import numpy as np
import pytest
from sqlalchemy import select

from app.database import async_session
from app.models.face import InspirationFaceDetection
from app.models.task import TaskQueue
from app.services.task_runners.face_scan import (
    execute_face_match,
    execute_face_scan,
)


def _unit(seed: int) -> list[float]:
    """生成 512 维单位向量。"""
    rng = np.random.default_rng(seed)
    emb = rng.standard_normal(512).astype(np.float32)
    return (emb / np.linalg.norm(emb)).tolist()


def _make_photo_bytes(color):
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (32, 32), color).save(buf, format="JPEG")
    buf.seek(0)
    return buf.getvalue(), "image/jpeg"


def _upload_inspiration(client, color):
    data, ctype = _make_photo_bytes(color)
    r = client.post(
        "/api/inspirations",
        files={"file": ("insp.jpg", data, ctype)},
        data={"source_type": "manual_upload"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _patch_embed(monkeypatch, embedding: list[float]):
    """face_client.embed 单图版（博主特征注册用）。"""

    async def fake_embed(image_bytes, filename="image.jpg"):
        return {
            "face_count": 1,
            "faces": [{"bbox": [0, 0, 10, 10], "det_score": 0.9, "embedding": embedding}],
        }

    monkeypatch.setattr("app.services.blogger_face.face_client.embed", fake_embed)


def _patch_embed_batch(monkeypatch, faces_by_index: list[list[dict]]):
    """face_client.embed_batch 按调用顺序返回配置的人脸（扫描任务用）。"""

    async def fake_embed_batch(images, filenames=None):
        items = []
        offset = 0
        for i in range(len(images)):
            faces = faces_by_index[offset + i] if offset + i < len(faces_by_index) else []
            items.append({"index": i, "face_count": len(faces), "faces": faces})
        return {"items": items, "failed": 0}

    monkeypatch.setattr(
        "app.services.task_runners.face_scan.face_client.embed_batch", fake_embed_batch
    )


def _patch_embed_batch_for_model_register(monkeypatch, embedding: list[float]):
    """模特特征注册用（单张照片 → 1 张脸）。"""

    async def fake_embed_batch(images, filenames=None):
        return {
            "items": [
                {
                    "index": 0,
                    "face_count": 1,
                    "faces": [{"bbox": [0, 0, 10, 10], "det_score": 0.9, "embedding": embedding}],
                }
            ],
            "failed": 0,
        }

    monkeypatch.setattr("app.services.model_face.face_client.embed_batch", fake_embed_batch)


def _setup_blogger(client, create_blogger, monkeypatch, embedding):
    blogger = create_blogger(name="扫描博主")
    _patch_embed(monkeypatch, embedding)
    r = client.post(
        f"/api/bloggers/{blogger['id']}/face",
        files=[("files", ("a.jpg", b"photo", "image/jpeg"))],
    )
    assert r.status_code == 200, r.text
    return blogger


def _setup_model(client, create_model, monkeypatch, embedding):
    model = create_model(name="扫描模特")
    r = client.post(f"/api/models/{model['id']}/photo-sets", json={"name": "写真"})
    set_id = r.json()["id"]
    data, ctype = _make_photo_bytes((5, 6, 7))
    r = client.post(
        f"/api/models/{model['id']}/photo-sets/{set_id}/photos",
        files={"file": ("a.jpg", data, ctype)},
        data={"sort_order": "0"},
    )
    assert r.status_code == 201, r.text
    _patch_embed_batch_for_model_register(monkeypatch, embedding)
    r = client.post(f"/api/models/{model['id']}/face")
    assert r.status_code == 200, r.text
    return model


async def _run_scan(client, scope="incremental", auto_match=True) -> int:
    """创建扫描任务并同步执行（worker 内逻辑直调），返回 task_id。"""
    r = client.post("/api/face-scan/start", json={"scope": scope, "auto_match": auto_match})
    assert r.status_code == 201, r.text
    task_id = r.json()["task_id"]
    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
        await execute_face_scan(db, task)
    return task_id


async def _fetch_detections(inspiration_id: str) -> list[InspirationFaceDetection]:
    async with async_session() as db:
        rows = await db.execute(
            select(InspirationFaceDetection).where(
                InspirationFaceDetection.inspiration_id == inspiration_id
            )
        )
        return list(rows.scalars().all())


# ═══════════════════════════════════════════════════════════════
#  扫描任务
# ═══════════════════════════════════════════════════════════════


async def test_scan_task_incremental_and_placeholder(
    client, create_blogger, monkeypatch
):
    """增量扫描：有人脸素材写人脸明细，无脸素材写占位；auto_match 创建匹配任务。"""
    emb = _unit(1)
    blogger = _setup_blogger(client, create_blogger, monkeypatch, emb)
    # 素材A（先传，扫描顺序靠前）含 1 张脸；素材B 无脸
    insp_a = _upload_inspiration(client, (200, 30, 40))
    insp_b = _upload_inspiration(client, (60, 70, 80))
    _patch_embed_batch(
        monkeypatch,
        [
            [{"bbox": [0, 0, 10, 10], "det_score": 0.9, "embedding": emb}],
            [],  # 素材B 无脸
        ],
    )

    task_id = await _run_scan(client)
    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
        # status 由 worker 在 handler 返回后统一置 success（此处直调 handler 不模拟）
        assert task.result["scanned"] == 2
        assert task.result["faces"] == 1
        assert task.result["match_task_id"] is not None

    # 素材A：1 条人脸明细（embedding 非空）
    dets_a = await _fetch_detections(insp_a)
    assert len(dets_a) == 1
    assert len(dets_a[0].embedding) == 512 * 4
    # 素材B：1 条占位记录（embedding 空 = 已扫标记）
    dets_b = await _fetch_detections(insp_b)
    assert len(dets_b) == 1
    assert dets_b[0].embedding == b""

    # 匹配任务被自动创建
    async with async_session() as db:
        match_task = (
            await db.execute(
                select(TaskQueue).where(TaskQueue.type == "face_match").order_by(TaskQueue.id.desc())
            )
        ).scalar_one_or_none()
        assert match_task is not None

    # 增量语义：再跑一次扫描 → 无可扫素材
    task_id2 = await _run_scan(client)
    async with async_session() as db:
        task2 = await db.get(TaskQueue, task_id2)
        assert task2.total == 0
        assert task2.progress == 100


async def test_scan_task_all_scope_clears(client, create_blogger, monkeypatch):
    """全量扫描：先清空全部检测记录再重扫。"""
    emb = _unit(2)
    _setup_blogger(client, create_blogger, monkeypatch, emb)
    insp = _upload_inspiration(client, (100, 110, 120))
    _patch_embed_batch(
        monkeypatch,
        [[{"bbox": [0, 0, 10, 10], "det_score": 0.9, "embedding": emb}]],
    )
    await _run_scan(client)

    # 再次全量扫描（同样的人脸结果）
    _patch_embed_batch(
        monkeypatch,
        [[{"bbox": [0, 0, 10, 10], "det_score": 0.9, "embedding": emb}]],
    )
    task_id = await _run_scan(client, scope="all")
    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
        assert task.result["scanned"] == 1
    dets = await _fetch_detections(insp)
    assert len(dets) == 1


# ═══════════════════════════════════════════════════════════════
#  候选匹配任务 + 结果查询 + 审核确认（端到端）
# ═══════════════════════════════════════════════════════════════


async def test_match_confirm_results_flow(
    client, create_blogger, create_model, monkeypatch
):
    """全链路：扫描 → 匹配 → 结果查询 → 确认写关联 → 撤销。"""
    emb_a = _unit(1)
    blogger = _setup_blogger(client, create_blogger, monkeypatch, emb_a)
    emb_c = _unit(3)
    _setup_model(client, create_model, monkeypatch, emb_c)
    insp = _upload_inspiration(client, (10, 200, 30))
    _patch_embed_batch(
        monkeypatch,
        [[{"bbox": [0, 0, 10, 10], "det_score": 0.9, "embedding": emb_a}]],
    )
    task_id = await _run_scan(client)
    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
        match_task_id = task.result["match_task_id"]
        match_task = await db.get(TaskQueue, match_task_id)
        await execute_face_match(db, match_task)
        await db.refresh(match_task)
        assert match_task.result["matched"] == 1

    # 结果查询：聚合含博主
    agg = client.get("/api/face-scan/results?status=pending").json()
    assert agg["mode"] == "persons"
    assert any(
        p["person_type"] == "blogger" and p["person_id"] == blogger["id"] and p["count"] == 1
        for p in agg["items"]
    )
    # 明细
    detail = client.get(
        f"/api/face-scan/results?status=pending&person_id={blogger['id']}"
    ).json()
    assert detail["mode"] == "detail"
    assert detail["total"] == 1
    assert detail["items"][0]["inspiration_id"] == insp
    # 未匹配：无（人脸命中了）
    unmatched = client.get("/api/face-scan/results?status=pending&unmatched=true").json()
    assert unmatched["total"] == 0

    # 确认：写关联表 + confirmed
    det_id = detail["items"][0]["detection_id"]
    r = client.post(
        "/api/face-scan/confirm",
        json={
            "action": "confirm",
            "items": [{"detection_id": det_id, "person_type": "blogger", "person_id": blogger["id"]}],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["confirmed"] == 1

    # 素材详情的人物关联可见（关联表写入）
    detail_insp = client.get(f"/api/inspirations/{insp}").json()
    assert any(b["id"] == blogger["id"] for b in detail_insp["bloggers"])
    # 已确认区可查
    confirmed = client.get(
        f"/api/face-scan/results?status=confirmed&person_id={blogger['id']}"
    ).json()
    assert confirmed["total"] == 1

    # 幂等：重复确认 → 全部 skipped
    r2 = client.post(
        "/api/face-scan/confirm",
        json={
            "action": "confirm",
            "items": [{"detection_id": det_id, "person_type": "blogger", "person_id": blogger["id"]}],
        },
    )
    assert r2.json()["confirmed"] == 0
    assert r2.json()["skipped"] == 1

    # 锁定单向：撤销不再解除关联（confirmed 保持，关联保留）
    r3 = client.post(
        "/api/face-scan/confirm",
        json={"action": "undo", "items": [{"detection_id": det_id}]},
    )
    assert r3.json()["undone"] == 0
    assert r3.json()["skipped"] == 1
    detail_insp2 = client.get(f"/api/inspirations/{insp}").json()
    assert any(b["id"] == blogger["id"] for b in detail_insp2["bloggers"])
    dets = await _fetch_detections(insp)
    assert dets[0].match_status == "confirmed"
    assert dets[0].matched_blogger_id == blogger["id"]


async def test_confirm_reject_pending(client, create_blogger, monkeypatch):
    """驳回（reject）：候选被清空回未匹配区。"""
    emb = _unit(1)
    blogger = _setup_blogger(client, create_blogger, monkeypatch, emb)
    insp = _upload_inspiration(client, (30, 40, 200))
    _patch_embed_batch(
        monkeypatch,
        [[{"bbox": [0, 0, 10, 10], "det_score": 0.9, "embedding": emb}]],
    )
    await _run_scan(client)

    # 手动执行匹配（不依赖 auto_match 顺序）
    async with async_session() as db:
        match_task = (
            await db.execute(
                select(TaskQueue).where(TaskQueue.type == "face_match").order_by(TaskQueue.id.desc())
            )
        ).scalar_one()
        await execute_face_match(db, match_task)

    detail = client.get(
        f"/api/face-scan/results?status=pending&person_id={blogger['id']}"
    ).json()
    assert detail["mode"] == "detail"
    assert detail["total"] == 1
    assert detail["items"][0]["inspiration_id"] == insp
    det_id = detail["items"][0]["detection_id"]
    r = client.post("/api/face-scan/confirm", json={"action": "reject", "items": [{"detection_id": det_id}]})
    assert r.json()["rejected"] == 1

    dets = await _fetch_detections(insp)
    assert dets[0].match_status is None
    assert dets[0].matched_blogger_id is None
    assert dets[0].confidence is None
    # 不再出现在 pending 聚合（未匹配区也不含占位/空记录）
    agg = client.get("/api/face-scan/results?status=pending").json()
    assert agg["total"] == 0


# ═══════════════════════════════════════════════════════════════
#  任务 API 与取消
# ═══════════════════════════════════════════════════════════════


async def test_match_run_api_and_scan_task_status(client):
    """POST /api/face-match/run 创建匹配任务；GET /api/face-scan/task 返回最近任务。"""
    r = client.post("/api/face-match/run", json={"scope": "all"})
    assert r.status_code == 201, r.text
    match_task_id = r.json()["task_id"]

    status = client.get("/api/face-scan/task").json()
    assert status["match_task"]["id"] == match_task_id
    assert status["match_task"]["type"] == "face_match"
    assert status["scan_task"] is None or status["scan_task"]["type"] == "face_scan"


async def test_cancel_running_face_scan_allowed(client):
    """运行中的人脸扫描任务可取消（区别于普通任务）。"""
    # 构造一个 running 的 face_scan 任务
    r = client.post("/api/face-scan/start", json={"scope": "incremental", "auto_match": False})
    task_id = r.json()["task_id"]

    # 直接置为 running（模拟 worker 已认领）
    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
        task.status = "running"
        await db.commit()

    r = client.post(f"/api/tasks/{task_id}/cancel")
    assert r.status_code == 200, r.text

    # 不存在的任务 → 404
    r2 = client.post("/api/tasks/999999/cancel")
    assert r2.status_code == 404


# ═══════════════════════════════════════════════════════════════
#  锁定（已确认记录） + 半增量扫描 + 自动匹配默认关闭
# ═══════════════════════════════════════════════════════════════


async def _match_and_get_pending_detail(client, blogger_id):
    """执行最近一次 face_match 任务，返回指定博主的 pending 明细（默认分页）。"""
    async with async_session() as db:
        match_task = (
            await db.execute(
                select(TaskQueue)
                .where(TaskQueue.type == "face_match")
                .order_by(TaskQueue.id.desc())
            )
        ).scalar_one()
        await execute_face_match(db, match_task)
    return client.get(
        f"/api/face-scan/results?status=pending&person_id={blogger_id}"
    ).json()


async def test_scan_semi_scope_skips_confirmed(client, create_blogger, monkeypatch):
    """半增量扫描：已有已确认（锁定）记录的素材整张跳过，仅 pending 或无记录素材参与。"""
    emb = _unit(10)
    blogger = _setup_blogger(client, create_blogger, monkeypatch, emb)
    insp_a = _upload_inspiration(client, (10, 20, 30))  # 将被确认 → 半增量跳过
    insp_b = _upload_inspiration(client, (40, 50, 60))  # 仅 pending → 半增量仍参与
    _patch_embed_batch(
        monkeypatch,
        [
            [{"bbox": [0, 0, 10, 10], "det_score": 0.9, "embedding": emb}],  # A
            [{"bbox": [0, 0, 10, 10], "det_score": 0.9, "embedding": emb}],  # B
        ],
    )
    await _run_scan(client)

    # 匹配 → 产出 A/B 的 pending 候选，确认 A
    detail = await _match_and_get_pending_detail(client, blogger["id"])
    assert detail["total"] == 2
    a_det = next(d for d in detail["items"] if d["inspiration_id"] == insp_a)
    r = client.post(
        "/api/face-scan/confirm",
        json={
            "action": "confirm",
            "items": [
                {
                    "detection_id": a_det["detection_id"],
                    "person_type": "blogger",
                    "person_id": blogger["id"],
                }
            ],
        },
    )
    assert r.json()["confirmed"] == 1

    # 半增量扫描：A 有 confirmed 记录 → 跳过；B（仅 pending）→ 参与
    _patch_embed_batch(
        monkeypatch,
        [[{"bbox": [0, 0, 10, 10], "det_score": 0.9, "embedding": emb}]],  # 仅 B
    )
    task_id = await _run_scan(client, scope="semi")
    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
        assert task.total == 1
        assert task.result["scanned"] == 1

    # A 的锁定记录原样保留（不受半增量影响）
    dets_a = await _fetch_detections(insp_a)
    assert len(dets_a) == 1
    assert dets_a[0].match_status == "confirmed"
    assert dets_a[0].matched_blogger_id == blogger["id"]
    # B 重新检出写入非空人脸记录
    dets_b = await _fetch_detections(insp_b)
    assert len(dets_b) == 1
    assert dets_b[0].embedding != b""


async def test_scan_all_scope_preserves_locked(client, create_blogger, monkeypatch):
    """全量重扫：保留已确认（锁定）记录，其余记录清空重写、序号从锁定数起编。"""
    emb = _unit(12)
    blogger = _setup_blogger(client, create_blogger, monkeypatch, emb)
    insp = _upload_inspiration(client, (100, 200, 30))
    _patch_embed_batch(
        monkeypatch,
        [[{"bbox": [0, 0, 10, 10], "det_score": 0.9, "embedding": emb}]],
    )
    await _run_scan(client)

    detail = await _match_and_get_pending_detail(client, blogger["id"])
    det_id = detail["items"][0]["detection_id"]
    r = client.post(
        "/api/face-scan/confirm",
        json={
            "action": "confirm",
            "items": [
                {
                    "detection_id": det_id,
                    "person_type": "blogger",
                    "person_id": blogger["id"],
                }
            ],
        },
    )
    assert r.json()["confirmed"] == 1

    # 全量重扫：同一张人脸再次检出 → 锁定记录保留，新记录序号从 1 起编
    _patch_embed_batch(
        monkeypatch,
        [[{"bbox": [0, 0, 10, 10], "det_score": 0.9, "embedding": emb}]],
    )
    task_id = await _run_scan(client, scope="all")
    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
        assert task.result["scanned"] == 1

    dets = await _fetch_detections(insp)
    assert len(dets) == 2
    locked = [d for d in dets if d.match_status == "confirmed"]
    assert len(locked) == 1
    assert locked[0].id == det_id
    assert locked[0].matched_blogger_id == blogger["id"]
    # 新检出记录序号从锁定记录数起编
    assert any(d.match_status is None and d.face_index == 1 for d in dets)


async def test_lock_blocks_update_unlink_delete(client, create_blogger, monkeypatch):
    """锁定：已确认记录禁止修改/解除/删除（409），详情页返回 confirmed 状态。"""
    emb = _unit(11)
    blogger = _setup_blogger(client, create_blogger, monkeypatch, emb)
    blogger2 = create_blogger(name="博主二")
    insp = _upload_inspiration(client, (70, 80, 90))
    _patch_embed_batch(
        monkeypatch,
        [[{"bbox": [0, 0, 10, 10], "det_score": 0.9, "embedding": emb}]],
    )
    await _run_scan(client)

    detail = await _match_and_get_pending_detail(client, blogger["id"])
    det_id = detail["items"][0]["detection_id"]
    r = client.post(
        "/api/face-scan/confirm",
        json={
            "action": "confirm",
            "items": [
                {
                    "detection_id": det_id,
                    "person_type": "blogger",
                    "person_id": blogger["id"],
                }
            ],
        },
    )
    assert r.json()["confirmed"] == 1

    # 详情页人脸列表返回 confirmed 状态
    dets = client.get(f"/api/inspirations/{insp}/face-detections").json()["detections"]
    assert dets[0]["match_status"] == "confirmed"
    assert dets[0]["matched_blogger_id"] == blogger["id"]

    # 修改归属 → 409
    r2 = client.put(
        f"/api/inspirations/{insp}/face-detections/{det_id}",
        json={"person_type": "blogger", "person_id": blogger2["id"]},
    )
    assert r2.status_code == 409
    # 解除关联 → 409
    r3 = client.put(
        f"/api/inspirations/{insp}/face-detections/{det_id}",
        json={"person_type": None, "person_id": None},
    )
    assert r3.status_code == 409
    # 删除记录 → 409
    r4 = client.delete(f"/api/inspirations/{insp}/face-detections/{det_id}")
    assert r4.status_code == 409

    # 记录未被改动
    dets = client.get(f"/api/inspirations/{insp}/face-detections").json()["detections"]
    assert dets[0]["matched_blogger_id"] == blogger["id"]
    assert dets[0]["match_status"] == "confirmed"


async def test_auto_match_default_off(client, create_blogger, monkeypatch):
    """自动全库匹配默认关闭：不传 auto_match 创建的任务不会自动创建匹配任务。"""
    emb = _unit(13)
    _setup_blogger(client, create_blogger, monkeypatch, emb)
    _upload_inspiration(client, (10, 200, 40))
    _patch_embed_batch(
        monkeypatch,
        [[{"bbox": [0, 0, 10, 10], "det_score": 0.9, "embedding": emb}]],
    )

    # 不传 auto_match（默认 false）
    r = client.post("/api/face-scan/start", json={"scope": "incremental"})
    assert r.status_code == 201, r.text
    task_id = r.json()["task_id"]
    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
        assert task.result["auto_match"] is False
        await execute_face_scan(db, task)

    # 没有自动创建 face_match 任务
    async with async_session() as db:
        match_task = (
            await db.execute(
                select(TaskQueue).where(TaskQueue.type == "face_match").order_by(TaskQueue.id.desc())
            )
        ).scalar_one_or_none()
        assert match_task is None
        task = await db.get(TaskQueue, task_id)
        assert task.result["scanned"] == 1
        assert task.result["match_task_id"] is None
