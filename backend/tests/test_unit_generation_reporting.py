"""出图服务（`services/generation_service.py`）的报表/导出/清理路径测试。

这些函数是「出图历史区」的数据源，出错不会 500，而是**数字悄悄不对**：
- `export_runs`：筛选条件必须与列表共用，导出的 CSV/JSON 是用户拿去二次分析的原始台账；
- `auto_title_for_run`：模型不可用时必须回退到描述前 20 字，绝不能 500 或返回空标题；
- `run_stats`：入库率按「已入库素材数 / 出图张数」算（不是批次），窗口外数据不能混进来；
- `retry_run`：必须原样带上云端档位的 size/watermark/pose 等参数，否则「同参重跑」静默变默认值；
- `cleanup_expired_runs`：只删终态过期批次的候选目录，**收藏批次不删**、库行不删，
  且目录必须落在 storage_root 内（batch_dir 来自 DB，防写坏后误删系统文件）。
"""

import json
import uuid
from datetime import timedelta

import pytest
from fastapi import HTTPException

from app.database import async_session
from app.models.generation import GenerationRun
from app.models.inspiration import Inspiration
from app.services import generation_service as gen
from app.utils.time import utcnow


async def _run(**overrides) -> int:
    payload = {
        "kind": "t2i",
        "status": "done",
        "prompt": "法式通勤穿搭",
        "model_key": "z_image_int8",
        "total": 2,
        "done": 2,
        "failed": 0,
        "params_json": "{}",
        "batch_dir": "",
        "favorite": False,
    }
    payload.update(overrides)
    async with async_session() as db:
        run = GenerationRun(**payload)
        db.add(run)
        await db.commit()
        return run.id


async def _imported_inspiration(run_id: int, *, deleted: bool = False) -> str:
    inspiration_id = str(uuid.uuid4())
    async with async_session() as db:
        row = Inspiration(
            id=inspiration_id,
            source_type="generated",
            file_path=f"generated/{inspiration_id}.png",
            media_type="image",
            generation_run_id=run_id,
            deleted_at=utcnow() if deleted else None,
        )
        db.add(row)
        await db.commit()
    return inspiration_id


class TestTitleHelpers:
    def test_clean_title_strips_quotes_bullets_and_truncates(self):
        assert gen._clean_title('"法式通勤穿搭"') == "法式通勤穿搭"
        assert gen._clean_title("  - 复古 港风  ") == "复古港风"
        assert gen._clean_title("一二三四五六七八九十甲乙丙丁戊己庚辛") == "一二三四五六七八九十甲乙丙丁戊己庚辛"[:20]
        assert gen._clean_title("") == ""

    def test_fallback_title_compacts_whitespace_and_truncates(self):
        assert gen.fallback_title("  法式  通勤 ") == "法式通勤"
        assert gen.fallback_title("") == "未命名批次"
        assert len(gen.fallback_title("字" * 50)) == 20


class TestExportRuns:
    async def test_invalid_format_is_400(self, client):
        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await gen.export_runs(db, fmt="xlsx")
        assert exc_info.value.status_code == 400

    async def test_csv_contains_header_and_rows(self, client):
        run_id = await _run(prompt="复古港风", params_json=json.dumps({"pose_id": "pose-9"}))
        await _imported_inspiration(run_id)

        async with async_session() as db:
            filename, text = await gen.export_runs(db, fmt="csv")

        assert filename.startswith("generate-history-") and filename.endswith(".csv")
        header = text.splitlines()[0]
        assert header == ",".join(gen.EXPORT_COLUMNS)
        assert "复古港风" in text
        assert "pose-9" in text  # pose 从 params 快照里取出
        assert "1" in text.splitlines()[1]  # imported_count 列出 1

    async def test_json_records_with_imported_count(self, client):
        run_id = await _run(prompt="极简风", favorite=True)
        await _imported_inspiration(run_id)
        await _imported_inspiration(run_id, deleted=True)  # 垃圾桶不计入

        async with async_session() as db:
            filename, text = await gen.export_runs(db, fmt="json")

        records = json.loads(text)
        assert filename.endswith(".json")
        assert len(records) == 1
        assert records[0]["prompt"] == "极简风"
        assert records[0]["favorite"] is True
        assert records[0]["imported_count"] == 1
        assert records[0]["created_at"].endswith("Z")  # 时间必须带时区后缀

    async def test_filters_shared_with_listing(self, client):
        await _run(prompt="法式通勤", favorite=True)
        await _run(prompt="运动风", favorite=False)

        async with async_session() as db:
            _f, csv_only_fav = await gen.export_runs(db, fmt="csv", favorite=True)
            _f2, csv_search = await gen.export_runs(db, fmt="csv", q="运动")

        assert "法式通勤" in csv_only_fav and "运动风" not in csv_only_fav
        assert "运动风" in csv_search and "法式通勤" not in csv_search


class _FakeResponse:
    def __init__(self, content: str):
        self._content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"message": {"content": self._content}}


class _FakeAsyncClient:
    """替换 httpx.AsyncClient：记录请求并按预设返回/抛错。"""

    last_payload: dict = {}
    raise_error: Exception | None = None
    content: str = ""

    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def post(self, url, json=None, **_kwargs):
        type(self).last_payload = {"url": url, "json": json}
        if type(self).raise_error is not None:
            raise type(self).raise_error
        return _FakeResponse(type(self).content)


class TestAutoTitle:
    async def test_missing_run_404(self, client):
        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await gen.auto_title_for_run(db, 999999)
        assert exc_info.value.status_code == 404

    async def test_empty_prompt_uses_fallback_without_calling_model(
        self, client, monkeypatch
    ):
        run_id = await _run(prompt="   ")

        def _should_not_be_called(*_a, **_k):
            raise AssertionError("空描述不应调用模型")

        monkeypatch.setattr(gen.httpx, "AsyncClient", _should_not_be_called)

        async with async_session() as db:
            result = await gen.auto_title_for_run(db, run_id)

        assert result == {"title": "未命名批次", "source": "fallback"}

    async def test_ai_title_is_cleaned_and_trimmed(self, client, monkeypatch):
        run_id = await _run(prompt="法式通勤穿搭，米色风衣配牛仔裤")
        _FakeAsyncClient.raise_error = None
        _FakeAsyncClient.content = '  "法式通勤穿搭"  '
        monkeypatch.setattr(gen.httpx, "AsyncClient", _FakeAsyncClient)

        async with async_session() as db:
            result = await gen.auto_title_for_run(db, run_id)

        assert result == {"title": "法式通勤穿搭", "source": "ai"}
        sent = _FakeAsyncClient.last_payload["json"]
        assert sent["stream"] is False
        assert "法式通勤穿搭" in sent["messages"][0]["content"]

    async def test_empty_model_content_falls_back(self, client, monkeypatch):
        run_id = await _run()
        _FakeAsyncClient.raise_error = None
        _FakeAsyncClient.content = "   "
        monkeypatch.setattr(gen.httpx, "AsyncClient", _FakeAsyncClient)

        async with async_session() as db:
            result = await gen.auto_title_for_run(db, run_id)

        assert result["source"] == "fallback"
        assert result["title"]  # 不能返回空标题

    async def test_model_exception_falls_back(self, client, monkeypatch):
        run_id = await _run(prompt="复古港风")
        _FakeAsyncClient.raise_error = RuntimeError("Ollama 未启动")
        monkeypatch.setattr(gen.httpx, "AsyncClient", _FakeAsyncClient)

        async with async_session() as db:
            result = await gen.auto_title_for_run(db, run_id)

        assert result == {"title": "复古港风", "source": "fallback"}


class TestRunStats:
    async def test_empty_history_returns_zeros(self, client):
        async with async_session() as db:
            stats = await gen.run_stats(db)

        assert stats["runs"] == 0
        assert stats["images"] == 0
        assert stats["import_rate"] == 0
        assert stats["avg_duration_s"] == 0
        assert stats["failure_rate"] == 0
        assert stats["by_model"] == []

    async def test_counts_rate_and_model_breakdown(self, client):
        done = await _run(model_key="z_image_int8", total=2, status="done")
        await _run(model_key="z_image_int8", total=3, status="failed", failed=3)
        await _run(
            model_key="seedream",
            total=1,
            status="done",
            created_at=utcnow() - timedelta(days=100),  # 窗口外
        )
        await _imported_inspiration(done)

        async with async_session() as db:
            stats = await gen.run_stats(db, days=30)

        assert stats["runs"] == 2  # 100 天前那批不在 30 天窗口内
        assert stats["images"] == 5
        assert stats["imported_images"] == 1
        assert stats["import_rate"] == round(1 / 5, 4)
        assert stats["failure_rate"] == 0.5
        assert stats["by_model"][0] == {"model_key": "z_image_int8", "runs": 2, "images": 5}

    async def test_days_zero_counts_all_history(self, client):
        await _run(created_at=utcnow() - timedelta(days=100))

        async with async_session() as db:
            stats = await gen.run_stats(db, days=0)

        assert stats["runs"] == 1
        assert stats["days"] == 0


class TestRecentAndRetry:
    async def test_recent_runs_delegates_to_list(self, client, monkeypatch):
        seen: dict = {}

        async def _fake_list(db, *, page, size, **_kwargs):
            seen["page"] = page
            seen["size"] = size
            return {"items": [{"id": 1}]}

        monkeypatch.setattr(gen, "list_runs", _fake_list)
        async with async_session() as db:
            result = await gen.recent_runs(db, limit=5)

        assert result == [{"id": 1}]
        assert seen == {"page": 1, "size": 5}

    async def test_retry_missing_run_404(self, client):
        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await gen.retry_run(db, 999999)
        assert exc_info.value.status_code == 404

    async def test_retry_reuses_all_params_including_cloud_fields(self, client, monkeypatch):
        captured: dict = {}

        async def _fake_create_run(_db, payload, **_kwargs):
            captured.update(payload)
            return {"run_id": 2, "task_id": 3}

        run_id = await _run(
            params_json=json.dumps(
                {
                    "width": 768,
                    "height": 1024,
                    "steps": 8,
                    "cfg": 1.5,
                    "count": 4,
                    "pose_id": "pose-1",
                    "size": "1536x2048",
                    "watermark": True,
                }
            ),
            reference_ids=json.dumps(["ref-a", "ref-b"]),
            total=2,
        )
        monkeypatch.setattr(gen, "create_run", _fake_create_run)

        async with async_session() as db:
            result = await gen.retry_run(db, run_id, seed=1234)

        assert result == {"run_id": 2, "task_id": 3}
        assert captured["width"] == 768
        assert captured["height"] == 1024
        assert captured["count"] == 4  # params 优先于 run.total
        assert captured["reference_ids"] == ["ref-a", "ref-b"]
        assert captured["pose_id"] == "pose-1"
        assert captured["size"] == "1536x2048"  # 云端档位字段必须带
        assert captured["watermark"] is True
        assert captured["seed"] == 1234


class TestCleanupExpiredRuns:
    async def test_disabled_returns_zeros(self, client):
        async with async_session() as db:
            result = await gen.cleanup_expired_runs(db, days=0)
        assert result == {"scanned": 0, "removed": 0, "skipped_favorites": 0}

    async def test_removes_only_expired_terminal_folders_inside_storage(
        self, client, tmp_path, monkeypatch
    ):
        storage = tmp_path / "storage"
        storage.mkdir()
        monkeypatch.setattr(gen.settings, "storage_root", storage)

        reconciled: list[bool] = []

        async def _fake_reconcile(_db):
            reconciled.append(True)
            return {"reconciled": 0}

        monkeypatch.setattr(gen, "reconcile_stale_generation_runs", _fake_reconcile)

        old_done = await _run(
            status="done", batch_dir="generated/1", created_at=utcnow() - timedelta(days=10)
        )
        favorite = await _run(
            status="done",
            batch_dir="generated/2",
            favorite=True,
            created_at=utcnow() - timedelta(days=10),
        )
        running = await _run(
            status="running", batch_dir="generated/3", created_at=utcnow() - timedelta(days=10)
        )
        (storage / "generated/1").mkdir(parents=True)
        (storage / "generated/2").mkdir(parents=True)
        (storage / "generated/3").mkdir(parents=True)

        async with async_session() as db:
            result = await gen.cleanup_expired_runs(db, days=7)

        assert reconciled  # 先对账停滞 running
        # scanned 只统计「真正参与清理」的非收藏终态批次；收藏单独回报跳过数
        assert result["scanned"] == 1
        assert result["skipped_favorites"] == 1
        assert result["removed"] == 1
        assert not (storage / "generated/1").exists()  # 过期且非收藏：删目录
        assert (storage / "generated/2").exists()  # 收藏：保留
        assert (storage / "generated/3").exists()  # running：保留
        # 库行保留（历史仍可查）
        async with async_session() as db:
            assert await db.get(GenerationRun, old_done) is not None
            assert await db.get(GenerationRun, favorite) is not None
            assert await db.get(GenerationRun, running) is not None

    async def test_missing_folder_and_outside_storage_are_skipped(
        self, client, tmp_path, monkeypatch
    ):
        storage = tmp_path / "storage"
        storage.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        monkeypatch.setattr(gen.settings, "storage_root", storage)

        async def _fake_reconcile(_db):
            return {"reconciled": 0}

        monkeypatch.setattr(gen, "reconcile_stale_generation_runs", _fake_reconcile)

        await _run(
            status="failed", batch_dir="generated/missing", created_at=utcnow() - timedelta(days=30)
        )
        await _run(
            status="done", batch_dir="../outside", created_at=utcnow() - timedelta(days=30)
        )

        async with async_session() as db:
            result = await gen.cleanup_expired_runs(db, days=7)

        assert result["removed"] == 0
        assert outside.exists()  # 路径逃逸 storage_root：不删

    async def test_rmtree_failure_is_logged_not_raised(self, client, tmp_path, monkeypatch):
        storage = tmp_path / "storage"
        storage.mkdir()
        monkeypatch.setattr(gen.settings, "storage_root", storage)
        (storage / "generated/bad").mkdir(parents=True)
        await _run(
            status="done", batch_dir="generated/bad", created_at=utcnow() - timedelta(days=30)
        )

        async def _fake_reconcile(_db):
            return {"reconciled": 0}

        def _boom(*_a, **_k):  # 同步函数：cleanup 通过 asyncio.to_thread 调 rmtree
            raise OSError("文件被占用")

        monkeypatch.setattr(gen, "reconcile_stale_generation_runs", _fake_reconcile)
        monkeypatch.setattr(gen.shutil, "rmtree", _boom)

        async with async_session() as db:
            result = await gen.cleanup_expired_runs(db, days=7)  # 不抛

        assert result["removed"] == 0
