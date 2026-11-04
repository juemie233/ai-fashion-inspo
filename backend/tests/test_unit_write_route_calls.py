"""写操作 / 破坏性接口的直接调用证据（2026-11-04 测试体系评测 P1-6）。

评测指出一批写路径只有服务层或间接覆盖。这里用 `client` 直接打到路由，并把
真正的副作用函数替换成探针：锁定「路由 → 服务层参数」这一层不被改坏。

不替代各服务自己的业务用例；只补「这个入口有没有人真的调过」。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import generation_service, scraper_service


async def test_generate_confirm_route_passes_filenames(client, monkeypatch):
    seen: dict = {}

    async def fake_confirm(db, run_id, filenames, *, tag_ids=None, caption_from_prompt=True):
        seen.update(run_id=run_id, filenames=filenames, tag_ids=tag_ids, caption=caption_from_prompt)
        return {"imported": len(filenames)}

    monkeypatch.setattr(generation_service, "confirm_import", fake_confirm)
    resp = client.post(
        "/api/generate/runs/7/confirm",
        json={"filenames": ["01.png", "02.png"], "tag_ids": [3], "caption_from_prompt": False},
    )
    assert resp.status_code == 200
    assert resp.json() == {"imported": 2}
    assert seen == {
        "run_id": 7,
        "filenames": ["01.png", "02.png"],
        "tag_ids": [3],
        "caption": False,
    }


async def test_generate_retry_route_passes_seed(client, monkeypatch):
    seen: dict = {}

    async def fake_retry(db, run_id, seed=0):
        seen.update(run_id=run_id, seed=seed)
        return {"run_id": 9}

    monkeypatch.setattr(generation_service, "retry_run", fake_retry)
    resp = client.post("/api/generate/runs/5/retry?seed=123")
    assert resp.status_code == 200
    assert seen == {"run_id": 5, "seed": 123}


async def test_generate_regenerate_route_passes_filename_and_seed(client, monkeypatch):
    seen: dict = {}

    async def fake_regen(db, run_id, filename, seed=0):
        seen.update(run_id=run_id, filename=filename, seed=seed)
        return {"run_id": 10}

    monkeypatch.setattr(generation_service, "regenerate_candidate", fake_regen)
    resp = client.post("/api/generate/runs/5/regenerate?filename=02.png&seed=88")
    assert resp.status_code == 200
    assert seen == {"run_id": 5, "filename": "02.png", "seed": 88}


async def test_generate_cleanup_route_passes_days(client, monkeypatch):
    seen: dict = {}

    async def fake_cleanup(db, days=None):
        seen["days"] = days
        return {"removed": 1}

    monkeypatch.setattr(generation_service, "cleanup_expired_runs", fake_cleanup)
    resp = client.post("/api/generate/runs/cleanup?days=3")
    assert resp.status_code == 200
    assert seen == {"days": 3}


async def test_generate_cancel_route_404_for_missing_run(client):
    resp = client.post("/api/generate/runs/999999/cancel")
    assert resp.status_code == 404
    assert "出图批次不存在" in resp.json()["detail"]


async def test_admin_cleanup_orphans_route_direct_call(client):
    """空临时存储下直接调用：应返回统计结构而不是 500。"""
    resp = client.post("/api/admin/cleanup-orphans")
    assert resp.status_code == 200
    body = resp.json()
    assert {"deleted_count", "freed_bytes"} <= set(body)


async def test_admin_deduplicate_route_creates_task(client, monkeypatch):
    from app.services import task_runner

    async def fake_create(db):
        return SimpleNamespace(id=42)

    monkeypatch.setattr(task_runner, "create_deduplicate_task", fake_create)
    # 路由内部是 `from app.services.task_runner import create_deduplicate_task`，
    # patch 模块属性对该导入同样生效（导入发生在调用时）。
    resp = client.post("/api/admin/deduplicate")
    assert resp.status_code == 200
    assert resp.json()["task_id"] == 42


async def test_admin_vector_backfill_route_direct_call(client, monkeypatch):
    from app.services.task_runners import vector_backfill
    from app.services.vector import store as vector_store

    async def fake_flush(db, force=False, extra_ids=None):
        return SimpleNamespace(id=9, total=3)

    monkeypatch.setattr(vector_store, "is_lancedb_available", lambda: True)
    monkeypatch.setattr(vector_backfill, "flush_pending_vector_backfills", fake_flush)
    resp = client.post("/api/admin/vector-backfill")
    assert resp.status_code == 200
    assert resp.json()["task_id"] == 9


@pytest.mark.parametrize(
    ("method", "path", "attr"),
    [
        ("post", "/api/scraper/tasks/77/cancel", "cancel_scraper_task"),
        ("post", "/api/scraper/tasks/77/retry", "retry_single_task"),
        ("post", "/api/scraper/tasks/retry-failed", "retry_failed_scraper_tasks"),
    ],
)
async def test_scraper_write_routes_direct_call(client, monkeypatch, method, path, attr):
    seen: dict = {}

    async def fake(db, *args, **kwargs):
        seen["called"] = (args, kwargs)
        return {"ok": True}

    monkeypatch.setattr(scraper_service, attr, fake)
    resp = getattr(client, method)(path)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert "called" in seen


async def test_f2_rollback_route_direct_call(client, monkeypatch):
    from app.services.scraper import f2_results

    seen: dict = {}

    async def fake_rollback(db, task_id, force=False):
        seen.update(task_id=task_id, force=force)
        return {"deleted": 0}

    monkeypatch.setattr(f2_results, "rollback_f2_task", fake_rollback)
    resp = client.post("/api/scraper/f2-tasks/77/rollback?force=true")
    assert resp.status_code == 200
    assert seen == {"task_id": 77, "force": True}


async def test_bloggers_batch_delete_route_direct_call(client, monkeypatch):
    from app.services.person import batch as person_batch

    seen: dict = {}

    async def fake_batch_delete(db, ids):
        seen["ids"] = ids
        return {"deleted": 0, "blocked": []}

    monkeypatch.setattr(person_batch, "batch_delete", fake_batch_delete)
    resp = client.post("/api/bloggers/batch/delete", json={"ids": [1, 2]})
    assert resp.status_code == 200
    assert seen == {"ids": [1, 2]}


async def test_bloggers_batch_promote_route_direct_call(client, monkeypatch):
    from app.services.person import batch as person_batch

    seen: dict = {}

    async def fake_batch_promote(db, ids):
        seen["ids"] = ids
        return {"promoted": len(ids), "skipped": []}

    monkeypatch.setattr(person_batch, "batch_promote", fake_batch_promote)
    resp = client.post("/api/bloggers/batch/promote", json={"ids": [3]})
    assert resp.status_code == 200
    assert seen == {"ids": [3]}


async def test_admin_find_duplicates_and_backup_status_direct_call(client):
    assert client.get("/api/admin/find-duplicates").status_code == 200
    assert client.get("/api/admin/backup/status").status_code == 200
