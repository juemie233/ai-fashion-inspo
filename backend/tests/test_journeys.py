"""链路端到端旅程测试（方案 B）：验证「环节衔接」而非单环节内部。

设计原则：
- 单测保环节、旅程测试保衔接：每条旅程把一条业务链路从头走到尾，
  每个环节之间断言状态机合法（verify_trash_invariants 零违规）、
  留痕完整（审计/墓碑）、自愈语义正确。
- 不依赖真实 Ollama/CLIP/LanceDB：向量生成用 fake 成功实现，
  故障注入用现有 monkeypatch 模式，保证测试确定性。

覆盖旅程：
1. 素材旅程：上传 → 打标 → 向量 → 垃圾桶 → 恢复 → 再删 → 清空
2. 采集旅程：插件会话 → from-url 入库 → 任务完成 → 删除 → 墓碑 → 重采被拒
3. 失败旅程：文件缺失自愈（trash/restore 均不悬空）
4. 崩溃旅程：worker 崩溃（心跳超时）→ 重置 → 重跑成功
"""

import io
import sqlite3
from datetime import timedelta

from PIL import Image

from app.config import settings
from app.utils.time import utcnow


# ── 通用辅助 ──


def _sql(statement: str, params: tuple = ()) -> list:
    """直接查/改库（断言墓碑、审计等跨表状态用）。"""
    db_path = settings.storage_root.parent / "fashion_inspo.db"
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(statement, params)
        conn.commit()
        if cur.description:
            return cur.fetchall()
        return []
    finally:
        conn.close()


def _fake_httpx_image_download(monkeypatch, img_bytes: bytes):
    """mock httpx.AsyncClient，让 from-url 服务端下载返回固定图片字节。"""
    import httpx

    class FakeStream:
        def __init__(self):
            self.headers = {
                "content-type": "image/jpeg",
                "content-length": str(len(img_bytes)),
            }

        def raise_for_status(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def aiter_bytes(self, _chunk_size):
            yield img_bytes

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        def stream(self, _method, _url):
            return FakeStream()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)


def _fake_rebuild_vectors_success(monkeypatch):
    """mock rebuild_inspiration_vectors：所有素材向量生成成功（不依赖真实 CLIP）。

    同时 mock 落库验证的读回（真实 LanceDB 在测试临时目录中为空表）。
    """
    from app.services.task_runners import vector_backfill as vb_module

    async def _fake(db, inspiration_id: str) -> dict:
        return {"text": True, "image": True}

    async def _fake_get_vector(kind: str, inspiration_id: str):
        return [0.1] * (384 if kind == "text" else 512)

    monkeypatch.setattr(vb_module, "rebuild_inspiration_vectors", _fake)
    monkeypatch.setattr(vb_module.vector_store, "get_vector", _fake_get_vector)


async def _verify_invariants() -> list[dict]:
    """调用垃圾桶不变量校验（断言健康旅程不产生半状态）。"""
    from app.database import async_session
    from app.services.inspiration_service import verify_trash_invariants

    async with async_session() as db:
        return await verify_trash_invariants(db)


# ── 1. 素材旅程 ──


async def test_material_full_journey(client, upload, monkeypatch):
    """素材完整旅程：上传 → 打标 → 向量 → 垃圾桶 → 恢复 → 再删 → 清空。

    每个环节后断言垃圾桶不变量零违规，垃圾桶进出留痕（墓碑/审计）完整。
    """
    # ① 上传（带来源 URL，供墓碑断言）
    source_url = "https://example.com/note/1001"
    r = upload(source_url=source_url)
    assert r.status_code == 201, r.text
    insp_id = r.json()["id"]
    detail = client.get(f"/api/inspirations/{insp_id}").json()
    assert detail["deleted_at"] is None
    assert detail["file_path"].startswith("images/")
    assert await _verify_invariants() == []

    # ② 打标（穿搭大标签）
    r = client.post(
        f"/api/inspirations/{insp_id}/tags",
        json={"names": ["法式穿搭"], "category": "outfit"},
    )
    assert r.status_code == 200, r.text
    detail = client.get(f"/api/inspirations/{insp_id}").json()
    assert any(t["tag"]["name"] == "法式穿搭" for t in detail["tags"])

    # ③ 向量回填：创建任务 → 执行 → 统计完整（fake 成功实现）
    _fake_rebuild_vectors_success(monkeypatch)
    from app.database import async_session
    from app.services.task_runners.vector_backfill import (
        create_vector_backfill_task,
        execute_vector_backfill,
    )

    async with async_session() as db:
        task = await create_vector_backfill_task(db, [insp_id])
        assert task is not None
        await execute_vector_backfill(db, task)
        assert task.result["image_done"] == 1
        assert task.result["text_done"] == 1

    # ④ 移入垃圾桶（带原因）
    r = client.post(f"/api/inspirations/{insp_id}/trash", json={"reason": "质量差"})
    assert r.status_code == 200, r.text
    detail = client.get(f"/api/inspirations/{insp_id}").json()
    assert detail["deleted_at"] is not None
    assert detail["trash_reason"] == "质量差"
    assert detail["trash_source"] == "manual"
    assert detail["file_path"].startswith("trash/")  # 文件已移入垃圾桶目录
    assert await _verify_invariants() == []

    # 墓碑 + 审计留痕
    assert _sql(
        "SELECT COUNT(*) FROM scraper_seen_urls WHERE source_url = ?", (source_url,)
    )[0][0] == 1
    actions = [row[0] for row in _sql("SELECT action FROM audit_logs ORDER BY id")]
    assert "trash" in actions

    # ⑤ 恢复
    r = client.post(f"/api/inspirations/{insp_id}/restore")
    assert r.status_code == 200, r.text
    detail = client.get(f"/api/inspirations/{insp_id}").json()
    assert detail["deleted_at"] is None
    assert detail["trash_reason"] is None
    assert detail["trash_source"] is None
    assert detail["file_path"].startswith("images/")  # 文件移回媒体目录
    assert await _verify_invariants() == []
    actions = [row[0] for row in _sql("SELECT action FROM audit_logs ORDER BY id")]
    assert "restore" in actions

    # ⑥ 再移入 → 清空垃圾桶（物理删除）
    client.post(f"/api/inspirations/{insp_id}/trash", json={"reason": "重复"})
    r = client.delete("/api/inspirations/trash")
    assert r.status_code == 200
    assert r.json()["deleted"] == 1

    assert client.get(f"/api/inspirations/{insp_id}").status_code == 404
    # 物理文件已删除（垃圾桶路径与媒体路径均不存在）
    assert not (settings.storage_root / detail["file_path"]).exists()
    assert await _verify_invariants() == []


# ── 2. 采集旅程 ──


async def test_scraper_full_journey(client, monkeypatch):
    """采集旅程：插件会话 → from-url 入库 → 任务完成 → 删除 → 墓碑 → 重采被拒。"""
    page_url = "https://www.xiaohongshu.com/explore/note_abc"
    img_url = "https://sns-webpic-qc.xhscdn.com/note.jpg"

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 100, 50)).save(buf, format="JPEG")
    _fake_httpx_image_download(monkeypatch, buf.getvalue())

    # ① 插件采集会话开始
    r = client.post(
        "/api/scraper/extension-tasks",
        json={"platform": "xiaohongshu", "source_url": page_url},
    )
    assert r.status_code == 201, r.text
    task_id = r.json()["id"]

    # ② 插件采集上传（服务端下载入库，关联采集任务）
    r = client.post(
        "/api/inspirations/from-url",
        json={
            "url": img_url,
            "source_type": "browser_extension",
            "source_url": page_url,
            "source_platform_id": "note_abc",
            "scraper_task_id": task_id,
        },
    )
    assert r.status_code == 201, r.text
    insp_id = r.json()["id"]
    detail = client.get(f"/api/inspirations/{insp_id}").json()
    assert detail["source_type"] == "browser_extension"
    assert detail["source_url"] == page_url  # 来源页地址优先落库

    # ③ 采集会话结束（汇总漏斗）
    r = client.post(
        f"/api/scraper/extension-tasks/{task_id}/complete",
        json={"items_found": 1, "items_added": 1, "source_url": page_url},
    )
    assert r.status_code == 200, r.text
    assert r.json()["items_added"] == 1

    # ④ 删除（移入垃圾桶）→ 立即写墓碑
    client.post(f"/api/inspirations/{insp_id}/trash", json={"reason": "不喜欢"})
    assert _sql(
        "SELECT COUNT(*) FROM scraper_seen_urls WHERE source_url = ?", (page_url,)
    )[0][0] == 1
    assert await _verify_invariants() == []

    # ⑤ 重采被拒：同来源页再传 → 409（墓碑拦截，不下载不入库）
    r = client.post(
        "/api/inspirations/from-url",
        json={"url": img_url, "source_type": "browser_extension", "source_url": page_url},
    )
    assert r.status_code == 409
    assert "墓碑" in r.json()["detail"]

    # ⑥ 恢复后墓碑仍在：重采仍被拒（防重复闭环——素材已在库，采集器应跳过）
    client.post(f"/api/inspirations/{insp_id}/restore")
    assert await _verify_invariants() == []
    r = client.post(
        "/api/inspirations/from-url",
        json={"url": img_url, "source_type": "browser_extension", "source_url": page_url},
    )
    assert r.status_code == 409
    # 素材本体完好（恢复成功，未被重采影响）
    assert client.get(f"/api/inspirations/{insp_id}").json()["deleted_at"] is None


# ── 3. 失败旅程：文件缺失自愈 ──


async def test_file_missing_self_heal_journey(client, upload):
    """素材文件丢失后：移入垃圾桶/恢复均不产生悬空记录（DB 路径保持原值自愈）。"""
    insp_id = upload().json()["id"]
    detail = client.get(f"/api/inspirations/{insp_id}").json()
    original_path = detail["file_path"]
    full = settings.storage_root / original_path
    assert full.exists()

    # 模拟文件损坏/丢失（磁盘上删掉）
    full.unlink()

    # 移入垃圾桶：文件缺失 → move_to_trash 返回 None，DB 路径保持原值，软删除仍成功
    r = client.post(f"/api/inspirations/{insp_id}/trash", json={"reason": "质量差"})
    assert r.status_code == 200, r.text
    detail = client.get(f"/api/inspirations/{insp_id}").json()
    assert detail["deleted_at"] is not None
    assert detail["file_path"] == original_path  # 路径未变（自愈：不指向不存在的 trash/ 路径）
    assert await _verify_invariants() == []

    # 恢复：垃圾桶里没有文件 → restore_from_trash 返回 None，恢复仍成功
    r = client.post(f"/api/inspirations/{insp_id}/restore")
    assert r.status_code == 200, r.text
    detail = client.get(f"/api/inspirations/{insp_id}").json()
    assert detail["deleted_at"] is None
    assert detail["file_path"] == original_path  # 仍指向原路径，DB 与磁盘状态一致
    assert await _verify_invariants() == []

    # 素材仍在正常列表
    assert client.get("/api/inspirations").json()["total"] == 1


# ── 4. 崩溃旅程：worker 心跳超时 → 重置 → 重跑成功 ──


async def test_crash_rerun_journey(client, upload, monkeypatch):
    """worker 崩溃后：心跳超时的 running 任务被重置为 pending，重跑成功完成。"""
    a = upload().json()["id"]
    b = upload().json()["id"]
    _fake_rebuild_vectors_success(monkeypatch)

    from app.database import async_session
    from app.models.task import TaskQueue
    from app.services.task_runners.vector_backfill import (
        create_vector_backfill_task,
        execute_vector_backfill,
    )
    from app.worker import _reset_stale_tasks

    async with async_session() as db:
        task = await create_vector_backfill_task(db, [a, b])
        task_id = task.id

        # 模拟崩溃现场：任务被认领为 running 后心跳停止（120s 前，超过 90s 阈值）
        task.status = "running"
        task.claimed_by = "worker-dead"
        task.heartbeat_at = utcnow() - timedelta(seconds=120)
        await db.commit()

    # worker 重启：重置心跳超时的遗留任务
    await _reset_stale_tasks()

    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
        assert task.status == "pending"
        assert task.claimed_by is None
        assert "进程异常终止" in (task.error or "")

        # 重跑：任务正常完成（不再假成功，统计完整）
        await execute_vector_backfill(db, task)
        assert task.done == 2
        assert task.result["image_done"] == 2
        assert task.result["text_done"] == 2

    # 素材本体不受崩溃影响
    assert client.get("/api/inspirations").json()["total"] == 2
