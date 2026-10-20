"""f2「入库前预览」与「批次回滚」的服务层用例（2026-10-20 从 TODO 落地）。

两件事在 TODO 里挂了很久，共同点是「**写之前先看清楚**」：

- 预览：跑同一条「扫描 → 五层判重」链路，只回「新增多少 / 跳过多少（含原因）」；
- 回滚：先给「能删多少、保留多少（含原因）」，确认后才物理删除本批。

这里覆盖的是两者最容易错的边界：空目录、已在库、作者白名单、以及「被改动过的不删」。
"""

import json
import sqlite3

import pytest
from sqlalchemy import select

from app.database import async_session
from app.models.audit import AuditLog
from app.models.inspiration import Inspiration
from app.models.task import TaskQueue
from app.services.scraper import f2_preview, f2_results
from scripts import import_f2_downloads as f2
from f2_import_unit_helpers import (  # noqa: F401
    SEC,
    _fake_tree,
    _jpeg,
    _make_lib,
)


# ── 预览 ──


def test_preview_counts_new_files_and_works(tmp_path):
    """空库 + 假产物树：待导入 = 全部可识别文件，按作品去重计数。"""
    root = tmp_path / "f2"
    _fake_tree(root)
    db = _make_lib(tmp_path)

    result = f2_preview.preview_import_sync(
        root=root, db_path=db, include_unknown_authors=True
    )

    assert result["has_any"] is True
    assert result["files"] == 3
    assert result["files_to_import"] == 3
    assert result["works"] == 2  # 一个图集（2 张）+ 一个视频
    assert result["skipped"] == {}


def test_preview_reports_skip_reason_for_hash_in_library(tmp_path):
    """已在库的内容计入跳过明细（界面要显示「跳过 M（含原因）」）。"""
    root = tmp_path / "f2"
    files = _fake_tree(root)
    db = _make_lib(tmp_path)
    first = files[0]

    from scripts import import_f2_downloads as f2mod

    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO inspirations VALUES ('x1', ?, NULL, 'f2:seed#image1')",
        (f2mod.sha256_file(first.path),),
    )
    conn.commit()
    conn.close()

    result = f2_preview.preview_import_sync(
        root=root, db_path=db, include_unknown_authors=True
    )

    assert result["files_to_import"] == 2
    assert result["skipped"], "应给出跳过原因"
    assert sum(result["skipped"].values()) >= 1


def test_preview_empty_dir_is_not_an_error(tmp_path):
    """空目录：不抛异常，明确回 has_any=False（界面据此提示「没有新产物」）。"""
    root = tmp_path / "empty"
    root.mkdir()
    db = _make_lib(tmp_path)

    result = f2_preview.preview_import_sync(root=root, db_path=db)

    assert result["has_any"] is False
    assert result["files"] == 0
    assert result["files_to_import"] == 0


def test_preview_respects_registered_blogger_whitelist(tmp_path, monkeypatch):
    """发布模式默认只算「已登记博主」的产物；显式包含未登记账号时都算。"""
    root = tmp_path / "f2"
    _jpeg(root / "里香1√" / "2025-01-01 10-00-00_#jk_image_1.jpg")
    _jpeg(root / "陌生人" / "2025-01-01 10-00-00_#jk_image_1.jpg", "blue")
    db = _make_lib(tmp_path)  # 库里的抖音博主只有「里香」

    monkeypatch.setattr(
        f2, "load_f2_authors", lambda _dir: [{"nickname": "里香1√", "sec_user_id": SEC}]
    )

    scoped = f2_preview.preview_import_sync(root=root, db_path=db)
    assert scoped["files"] == 2          # 扫描到 2 个
    assert scoped["files_to_import"] == 1  # 只算已登记博主的那个
    assert scoped["authors_scope"]

    loose = f2_preview.preview_import_sync(
        root=root, db_path=db, include_unknown_authors=True
    )
    assert loose["files_to_import"] == 2


async def test_preview_endpoint_smoke(client, tmp_path, monkeypatch):
    """路由冒烟：空产物目录也要返回结构化结果而不是 500。

    ⚠️ 必须把扫描根指到空临时目录：本机 E:\\f2-downloads 里是真实产物，
    让测试去扫它会实算上千张哈希（慢且违背「测试不碰真实数据」）。
    """
    from app.services.scraper import f2_preview as preview_module

    empty = tmp_path / "empty-root"
    empty.mkdir()
    monkeypatch.setattr(preview_module, "_scan_root", lambda *_a, **_k: empty)

    r = client.get("/api/scraper/f2-preview")
    assert r.status_code == 200
    body = r.json()
    assert body["has_any"] is False
    assert body["files_to_import"] == 0
    assert "skipped" in body


# ── 批次回滚 ──


async def _make_task_with_batch(tmp_path, insp_id: str) -> int:
    """造一个「带批次清单」的 f2 任务（清单里只有一条导入记录）。"""
    batch_dir = tmp_path / "batches"
    batch_dir.mkdir(exist_ok=True)
    batch_file = batch_dir / "f2-20260101-test.json"
    batch_file.write_text(
        json.dumps(
            {
                "batch_id": "20260101-test",
                "created_at": "2026-01-01 10:00:00",
                "db": str(tmp_path / "fashion_inspo.db"),
                "storage_root": str(tmp_path / "storage"),
                "imported": [
                    {"inspiration_id": insp_id, "file_path": f"images/x/{insp_id}.jpg"}
                ],
                "errors": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    async with async_session() as db:
        task = TaskQueue(
            type="f2_import",
            status="success",
            progress=100,
            total=1,
            done=1,
            result={"import": {"batch_file": str(batch_file)}},
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        return int(task.id)


async def test_rollback_preview_then_apply(client, tmp_path):
    """预览只读；执行后行被物理删除，并写 f2_rollback 审计。"""
    insp_id = "rb-1"
    async with async_session() as db:
        db.add(
            Inspiration(
                id=insp_id,
                source_type="douyin",
                file_path=f"images/x/{insp_id}.jpg",
                quality_status="pending",
            )
        )
        await db.commit()
    task_id = await _make_task_with_batch(tmp_path, insp_id)

    async with async_session() as db:
        preview = await f2_results.preview_f2_task_rollback(db, task_id)
    assert preview["total"] == 1
    assert preview["deletable"] == 1
    assert preview["kept"] == 0
    assert preview["sample"][0]["inspiration_id"] == insp_id

    # 预览不该动数据
    async with async_session() as db:
        assert await db.get(Inspiration, insp_id) is not None

    async with async_session() as db:
        result = await f2_results.rollback_f2_task(db, task_id)
    assert result["deleted"] == 1
    async with async_session() as db:
        assert await db.get(Inspiration, insp_id) is None
        audits = (
            await db.execute(select(AuditLog.action).where(AuditLog.action == "f2_rollback"))
        ).scalars().all()
    assert audits == ["f2_rollback"]


async def test_rollback_keeps_touched_inspiration_unless_forced(client, tmp_path):
    """打过标签的素材默认保留（不抹掉用户后续工作）；force=True 才删。"""
    insp_id = "rb-2"
    async with async_session() as db:
        db.add(
            Inspiration(
                id=insp_id,
                source_type="douyin",
                file_path=f"images/x/{insp_id}.jpg",
                quality_status="pending",
                is_favorite=True,  # 被收藏过 = 已被改动
            )
        )
        await db.commit()
    task_id = await _make_task_with_batch(tmp_path, insp_id)

    async with async_session() as db:
        preview = await f2_results.preview_f2_task_rollback(db, task_id)
    assert preview["deletable"] == 0
    assert preview["kept"] == 1
    assert preview["kept_reasons"]  # 原因文案要能显示

    async with async_session() as db:
        result = await f2_results.rollback_f2_task(db, task_id)
    assert result["deleted"] == 0
    async with async_session() as db:
        assert await db.get(Inspiration, insp_id) is not None

    async with async_session() as db:
        forced = await f2_results.rollback_f2_task(db, task_id, force=True)
    assert forced["deleted"] == 1


async def test_rollback_without_batch_file_is_a_message_not_an_error(client):
    """没有批次清单（入库阶段写失败）：返回可读提示，不抛 500。"""
    async with async_session() as db:
        task = TaskQueue(
            type="f2_import", status="success", progress=100, total=0, done=0, result={}
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        task_id = int(task.id)

    async with async_session() as db:
        preview = await f2_results.preview_f2_task_rollback(db, task_id)
        result = await f2_results.rollback_f2_task(db, task_id)

    assert preview["deletable"] == 0
    assert "批次清单" in preview["message"]
    assert result["deleted"] == 0
