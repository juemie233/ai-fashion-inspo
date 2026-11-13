"""f2 导入结果浏览与批量处置（`services/scraper/f2_results.py`）测试。

f2 素材没有 `scraper_task_id`，**批次清单 `result.import.batch_file` 是唯一回溯依据**。
这条链路上的逻辑判断都很细，错了不报错：

- 清单缺失/损坏时要按「本批没有可浏览的结果」处理（不能 500，也不能把整库素材当本批）；
- 列表要区分 live / trash / gone（已被物理删除的条目用占位卡，不可操作），
  且 `counts` 是**整批口径**（不受筛选影响），前端 tab 的角标靠它；
- 移入垃圾桶/还原/彻底删除前，要先把请求 ID **收敛到本批范围内**（越权/脏 ID 丢弃），
  否则能通过任务接口操作不属于该批的素材；
- 还原失败（如平台 ID 冲突）要计入 skipped 继续处理其余，不能整批失败；
- 「彻底删除」不是真删，而是创建 `batch_delete` 任务交给 worker（与 admin 同一链路）。

本文件覆盖纯函数、列表聚合、三种批量处置与清单路径解析。
"""

import json

import pytest
from fastapi import HTTPException

from app.database import async_session
from app.models.inspiration import Inspiration
from app.models.task import TaskQueue
from app.services.scraper import f2_results as f2r


async def _make_task(result: dict | None = None, type_: str = "f2_import") -> TaskQueue:
    async with async_session() as db:
        task = TaskQueue(
            type=type_,
            status="running",
            progress=90,
            total=0,
            done=0,
            result=result or {},
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        return task


def _manifest_file(tmp_path, entries: list[dict], batch_id: str = "batch-1"):
    path = tmp_path / "batch-manifest.json"
    path.write_text(
        json.dumps({"imported": entries, "batch_id": batch_id}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


async def _make_insp(upload, **fields) -> str:
    """用真实上传造素材（文件真实存在，垃圾桶搬运才走得通），再改字段。"""
    inspiration_id = upload().json()["id"]
    async with async_session() as db:
        row = await db.get(Inspiration, inspiration_id)
        for key, value in fields.items():
            setattr(row, key, value)
        await db.commit()
    return inspiration_id


class TestPureHelpers:
    async def test_load_f2_task_rejects_other_task_types(self, client):
        task = await _make_task(type_="batch_analyze")
        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await f2r._load_f2_task(db, task.id)
        assert exc_info.value.status_code == 404
        assert "抖音采集任务未找到" in exc_info.value.detail

    async def test_load_f2_task_ok(self, client):
        task = await _make_task()
        async with async_session() as db:
            loaded = await f2r._load_f2_task(db, task.id)
        assert loaded.id == task.id

    async def test_batch_entries_variants(self, client, tmp_path):
        no_result = await _make_task(result={})
        assert f2r._batch_entries(no_result) == ([], "")

        no_import = await _make_task(result={"fetch": {"ok": 1}})
        assert f2r._batch_entries(no_import) == ([], "")

        bad_import = await _make_task(result={"import": "not-a-dict"})
        assert f2r._batch_entries(bad_import) == ([], "")

        manifest = _manifest_file(tmp_path, [{"inspiration_id": "a"}], "b-9")
        with_file = await _make_task(result={"import": {"batch_file": str(manifest)}})
        entries, batch_id = f2r._batch_entries(with_file)
        assert entries == [{"inspiration_id": "a"}]
        assert batch_id == "b-9"

        corrupt = tmp_path / "corrupt.json"
        corrupt.write_text("{坏", encoding="utf-8")
        with_corrupt = await _make_task(result={"import": {"batch_file": str(corrupt)}})
        assert f2r._batch_entries(with_corrupt) == ([], "")  # 损坏清单按「无批次」处理

    def test_result_summary_degrades_on_bad_values(self):
        task = TaskQueue(
            type="f2_import",
            status="success",
            result={
                "import": {"imported": "8", "failed": "x"},
                "fetch": {"ok": 10, "total": 12},
            },
        )
        summary = f2r._result_summary(task)
        assert summary == {"imported": 8, "failed": 0, "fetch_ok": 10, "fetch_total": 12}

        assert f2r._result_summary(TaskQueue(type="f2_import", status="success", result=None)) == {
            "imported": 0,
            "failed": 0,
            "fetch_ok": 0,
            "fetch_total": 0,
        }

    def test_entry_ids_dedupes_and_skips_missing(self):
        entries = [
            {"inspiration_id": "a"},
            {"inspiration_id": "a"},
            {"inspiration_id": ""},
            {},
            {"inspiration_id": "b"},
        ]
        assert f2r._entry_ids(entries) == ["a", "b"]

    async def test_batch_file_of(self, client, tmp_path):
        assert f2r._batch_file_of(TaskQueue(type="f2_import", status="success")) is None
        assert (
            f2r._batch_file_of(
                TaskQueue(type="f2_import", status="success", result={"import": {}})
            )
            is None
        )
        path = tmp_path / "m.json"
        task = TaskQueue(
            type="f2_import", status="success", result={"import": {"batch_file": str(path)}}
        )
        assert f2r._batch_file_of(task) == path


class TestGetTaskResults:
    async def _seed_batch(self, upload, tmp_path):
        """4 条清单条目：live-pending / live-approved / 垃圾桶 / 已物理删除(gone)。"""
        # 手动上传默认免审核（auto_approve）→ 要显式置 pending 才有「待审核」样本
        pending = await _make_insp(
            upload,
            source_author="博主甲",
            caption="待审",
            quality_status="pending",
            source_platform_id="p1",
        )
        approved = await _make_insp(
            upload, source_author="博主乙", caption="已通过", quality_status="approved"
        )
        trashed = await _make_insp(upload, source_author="博主甲", caption="被删")
        async with async_session() as db:
            row = await db.get(Inspiration, trashed)
            await db.delete(row)  # 物理删除：清单条目变 gone
            await db.commit()

        entries = [
            {
                "inspiration_id": pending,
                "author_dir": "博主甲",
                "media_type": "image",
                "caption": "清单里的待审",
                "hashtags": ["#穿搭"],
                "platform_id": "p1",
            },
            {"inspiration_id": approved, "author_dir": "博主乙"},
            {"inspiration_id": trashed, "author_dir": "博主甲"},
            {"inspiration_id": "gone-not-in-db", "author_dir": "博主丙", "caption": "占位"},
        ]
        manifest = _manifest_file(tmp_path, entries)
        task = await _make_task(
            result={
                "import": {"batch_file": str(manifest), "imported": 3},
                "fetch": {"ok": 3, "total": 4},
            }
        )
        return task, {"pending": pending, "approved": approved, "trashed": trashed}

    async def test_invalid_state_400(self, client):
        task = await _make_task()
        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await f2r.get_f2_task_results(db, task.id, state="unknown")
        assert exc_info.value.status_code == 400
        assert "筛选口径不支持" in exc_info.value.detail

    async def test_without_manifest_returns_empty_batch(self, client):
        task = await _make_task(result={})
        async with async_session() as db:
            result = await f2r.get_f2_task_results(db, task.id)

        assert result["items"] == []
        assert result["total"] == 0
        assert result["has_batch"] is False
        assert result["counts"]["total"] == 0

    async def test_counts_cover_whole_batch_regardless_of_filter(self, client, upload, tmp_path):
        task, ids = await self._seed_batch(upload, tmp_path)
        async with async_session() as db:
            result = await f2r.get_f2_task_results(db, task.id, size=100)

        assert result["has_batch"] is True
        assert result["counts"] == {
            "total": 4,
            "live": 2,
            "trash": 0,  # trashed 素材已被物理删除 → 归 gone 而不是 trash
            "gone": 2,
            "pending": 1,
            "approved": 1,
            "rejected": 0,
        }
        assert result["total"] == 4
        # 已删除的条目用占位卡：无文件路径、不可操作
        gone_item = next(i for i in result["items"] if i["id"] == "gone-not-in-db")
        assert gone_item["state"] == "gone"
        assert gone_item["file_path"] is None
        assert gone_item["is_favorite"] is False
        # 存活素材字段来自库行（作者、caption 等）
        live = next(i for i in result["items"] if i["id"] == ids["pending"])
        assert live["state"] == "pending"
        assert live["author"] == "博主甲"
        assert live["caption"] == "待审"
        assert live["hashtags"] == ["#穿搭"]
        assert live["source_platform_id"] == "p1"

    async def test_state_filter_and_author_filter(self, client, upload, tmp_path):
        task, ids = await self._seed_batch(upload, tmp_path)
        async with async_session() as db:
            only_approved = await f2r.get_f2_task_results(db, task.id, state="approved")
            only_author = await f2r.get_f2_task_results(db, task.id, author="博主丙")
            only_gone = await f2r.get_f2_task_results(db, task.id, state="gone")

        assert [i["id"] for i in only_approved["items"]] == [ids["approved"]]
        assert only_approved["total"] == 1
        # 作者筛选：占位条目作者来自清单 author_dir
        assert [i["id"] for i in only_author["items"]] == ["gone-not-in-db"]
        assert only_gone["total"] == 2  # 3 条 gone：已被删的素材 + 清单里不存在的 ID

    async def test_authors_sorted_by_count_then_name(self, client, upload, tmp_path):
        task, _ids = await self._seed_batch(upload, tmp_path)
        async with async_session() as db:
            result = await f2r.get_f2_task_results(db, task.id, size=100)

        # 按 (-数量, 名称码点) 排序：同为 1 条时「丙」在「乙」前（中文码点序）
        assert result["authors"] == [
            {"name": "博主甲", "count": 2},
            {"name": "博主丙", "count": 1},
            {"name": "博主乙", "count": 1},
        ]

    async def test_pagination(self, client, upload, tmp_path):
        task, _ids = await self._seed_batch(upload, tmp_path)
        async with async_session() as db:
            page2 = await f2r.get_f2_task_results(db, task.id, page=2, size=3)

        assert page2["page"] == 2
        assert page2["size"] == 3
        assert page2["total"] == 4
        assert len(page2["items"]) == 1  # 4 条里第二页只剩 1 条

    async def test_task_summary_included(self, client, upload, tmp_path):
        task, _ids = await self._seed_batch(upload, tmp_path)
        async with async_session() as db:
            result = await f2r.get_f2_task_results(db, task.id)

        assert result["task"]["id"] == task.id
        assert result["task"]["status"] == "running"
        assert result["task"]["imported"] == 3
        assert result["task"]["fetch_total"] == 4


class TestBatchActions:
    @pytest.fixture
    async def batch(self, monkeypatch, upload):
        """构造「本批 2 条素材」，并把清单解析替换成直接返回条目（免落盘）。"""
        ids = [await _make_insp(upload) for _ in range(2)]
        entries = [{"inspiration_id": i, "author_dir": "作者"} for i in ids]
        monkeypatch.setattr(f2r, "_batch_entries", lambda _task: (entries, "batch-x"))
        return ids

    async def test_allowed_ids_requires_batch(self, client, monkeypatch):
        task = await _make_task(result={})
        monkeypatch.setattr(f2r, "_batch_entries", lambda _task: ([], ""))

        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await f2r._allowed_ids(db, task.id, ["a"])

        assert exc_info.value.status_code == 400
        assert "没有可操作的批次清单" in exc_info.value.detail

    async def test_allowed_ids_drops_foreign_ids(self, client, batch):
        task = await _make_task()
        async with async_session() as db:
            allowed = await f2r._allowed_ids(db, task.id, [batch[0], "not-in-batch"])
        assert allowed == [batch[0]]

    async def test_trash_rejects_unknown_reason(self, client, batch):
        task = await _make_task()
        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await f2r.trash_f2_task_results(db, task.id, [batch[0]], reason="编造原因")
        assert exc_info.value.status_code == 400

    async def test_trash_moves_batch_members_and_counts_skipped(self, client, batch):
        task = await _make_task()
        async with async_session() as db:
            result = await f2r.trash_f2_task_results(
                db, task.id, [batch[0], "not-in-batch"]
            )

        assert result == {"requested": 2, "trashed": 1, "skipped": 1}
        async with async_session() as db:
            row = await db.get(Inspiration, batch[0])
        assert row.deleted_at is not None
        assert row.trash_reason is not None

    async def test_trash_nothing_matching_400(self, client, batch):
        task = await _make_task()
        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await f2r.trash_f2_task_results(db, task.id, ["not-in-batch"])
        assert exc_info.value.status_code == 400
        assert "没有属于该任务的素材" in exc_info.value.detail

    async def test_restore_reports_skipped_for_live_material(
        self, client, batch, monkeypatch
    ):
        """已在库里的素材还原会失败 → 计入 skipped 继续，不整批失败。"""
        audited: list[dict] = []

        async def _audit(**kwargs):
            audited.append(kwargs)

        monkeypatch.setattr(f2r, "record_audit_log", _audit)
        task = await _make_task()

        async with async_session() as db:
            result = await f2r.restore_f2_task_results(db, task.id, [batch[0]])

        assert result["requested"] == 1
        assert result["restored"] == 0
        assert result["skipped"] == 1
        assert audited == []  # 没有成功还原就不写审计

    async def test_restore_trashed_material_and_audit_once(self, client, batch, monkeypatch):
        audited: list[dict] = []

        async def _audit(**kwargs):
            audited.append(kwargs)

        monkeypatch.setattr(f2r, "record_audit_log", _audit)
        task = await _make_task()

        async with async_session() as db:
            await f2r.trash_f2_task_results(db, task.id, [batch[0]])
        async with async_session() as db:
            result = await f2r.restore_f2_task_results(db, task.id, [batch[0], batch[1]])

        assert result["restored"] == 1
        assert result["skipped"] == 1  # batch[1] 从来没被删
        assert len(audited) == 1  # 批量还原只写一条审计
        assert audited[0]["action"] == "batch_restore"
        assert audited[0]["count"] == 1

    async def test_delete_creates_batch_delete_task(self, client, batch, monkeypatch):
        audited: list[dict] = []

        async def _audit(**kwargs):
            audited.append(kwargs)

        monkeypatch.setattr(f2r, "record_audit_log", _audit)
        task = await _make_task()

        async with async_session() as db:
            result = await f2r.delete_f2_task_results(db, task.id, batch + ["脏ID"])

        assert result["requested"] == 3
        assert result["count"] == 2  # 脏 ID 被过滤
        async with async_session() as db:
            delete_task = await db.get(TaskQueue, result["task_id"])
            assert delete_task.type == "batch_delete"
            assert delete_task.result["inspiration_ids"] == batch
            assert delete_task.result["label"] == f"f2_task_{task.id}"
        assert audited[0]["action"] == "batch_delete"
        assert audited[0]["count"] == 2

    async def test_delete_nothing_matching_400(self, client, batch):
        task = await _make_task()
        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await f2r.delete_f2_task_results(db, task.id, ["not-in-batch"])
        assert exc_info.value.status_code == 400

    async def test_rollback_preview_without_batch_file(self, client):
        """没有批次清单时给出可读提示，并返回全零预览（前端据此禁用回滚按钮）。"""
        task = await _make_task(result={})

        async with async_session() as db:
            preview = await f2r.preview_f2_task_rollback(db, task.id)

        assert preview["deletable"] == 0
        assert preview["total"] == 0
        assert preview["batch_file"] == ""
        assert "无法回滚" in preview["message"]

    async def test_rollback_without_deletable_returns_message(self, client, tmp_path):
        manifest = _manifest_file(tmp_path, [{"inspiration_id": "a"}], "b-1")
        task = await _make_task(result={"import": {"batch_file": str(manifest)}})

        async with async_session() as db:
            result = await f2r.rollback_f2_task(db, task.id)

        assert result["deleted"] == 0
        assert result["removed_files"] == 0
        assert result["total"] == 1
        # 清单里的素材在库里不存在 → plan 认为无可删
        assert "没有可回滚的素材" in result.get("message", "")
