"""`routers/ai_shared.py` 的后台分析任务与辅助函数测试。

`_run_analysis` 是「点一次 AI 分析」真正干活的后台协程：注册/排队 → 等暂停解除 →
拿信号量 → 解析分析源（图片原图 / 视频关键帧）→ 调模型 → 成功时清旧失败日志 →
finally 广播结果。这条链路以前没有任何单测，出错的表现是**前端一直转圈、素材永远
挂在「未分析」**，而且日志里看不出卡在哪一步。

本文件覆盖的语义：
- 同一素材已在队列中 → 直接跳过（防重复分析烧显存）；
- 素材不存在 / 视频关键帧解析失败 → 写「不可用日志」而不是静默丢弃（否则用户
  只能看到一个永远清不掉的未分析计数）；
- 成功 → 清理旧失败日志并提交；失败 → 广播 success=false；被取消 → 向上抛 CancelledError；
- finally 必须清理内存队列与任务追踪（取消/异常路径都不漏），并广播终态。
"""

import asyncio
from types import SimpleNamespace

import pytest

from app.routers import ai_shared


class _EmptyResult:
    """SQLAlchemy Result 的最小替身：空结果。"""

    def scalar_one_or_none(self):
        return None

    def __iter__(self):
        return iter(())

    def all(self):
        return []


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def __iter__(self):
        return iter(())


class _RowsResult:
    """模拟 execute(select(id)…) 的返回值：可按 `for row in result` 迭代。"""

    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def all(self):
        return list(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeDb:
    """按顺序吐出预设的 execute 结果；记录 delete/commit 调用。"""

    def __init__(self, results=()):
        self._results = list(results)
        self.deleted: list[object] = []
        self.commits = 0

    async def execute(self, stmt):
        text = str(stmt)
        if text.lstrip().upper().startswith("DELETE"):
            self.deleted.append(stmt)
            return _EmptyResult()
        return self._results.pop(0) if self._results else _EmptyResult()

    async def commit(self):
        self.commits += 1


class _FakeSessionCtx:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_exc):
        return False


@pytest.fixture(autouse=True)
def _clean_memory_state():
    """每个用例前后都清空内存队列，避免测试互相污染（模块级全局可变状态）。"""
    ai_shared._active_analyses.clear()
    ai_shared._pending_queue.clear()
    ai_shared._task_by_id.clear()
    yield
    ai_shared._active_analyses.clear()
    ai_shared._pending_queue.clear()
    ai_shared._task_by_id.clear()


@pytest.fixture
def broadcasts(monkeypatch):
    """拦截 WS 广播，返回收集到的消息列表。"""
    from app.routers import ws

    messages: list[dict] = []

    async def _broadcast(message):
        messages.append(message)

    monkeypatch.setattr(ws.manager, "broadcast", _broadcast)
    return messages


@pytest.fixture
def unavailable_logs(monkeypatch):
    """拦截「分析源不可用」日志写入。"""
    logs: list[tuple[str, str]] = []

    async def _write(inspiration_id, error):
        logs.append((inspiration_id, error))

    monkeypatch.setattr(ai_shared, "write_unavailable_log", _write)
    return logs


def _use_db(monkeypatch, *dbs):
    """让 `_run_analysis` 里每次新建会话依次拿到给定的 _FakeDb。"""
    queue = list(dbs)

    def _factory():
        return _FakeSessionCtx(queue.pop(0) if queue else _FakeDb())

    monkeypatch.setattr(ai_shared, "async_session", _factory)


class TestDuplicateAndUnavailable:
    async def test_duplicate_material_is_skipped(self, broadcasts):
        """已在队列中的素材不重复分析（否则同一张图会烧两次显存）。"""
        ai_shared._active_analyses["insp-1"] = "正在分析..."

        await ai_shared._run_analysis("insp-1", "images/a.jpg")

        assert ai_shared._active_analyses["insp-1"] == "正在分析..."  # 未被改写
        assert broadcasts == []  # 没走 finally 的广播

    async def test_missing_material_writes_unavailable_log(
        self, monkeypatch, broadcasts, unavailable_logs
    ):
        _use_db(monkeypatch, _FakeDb([_EmptyResult()]))  # 查不到素材

        await ai_shared._run_analysis("insp-missing")

        assert unavailable_logs == [("insp-missing", "分析失败：素材不存在")]
        assert broadcasts and broadcasts[0]["success"] is False
        assert "insp-missing" not in ai_shared._active_analyses  # finally 清理

    async def test_empty_frames_writes_unavailable_log(
        self, monkeypatch, broadcasts, unavailable_logs
    ):
        """视频关键帧提取失败：写日志说明原因，而不是静默跳过。"""
        _use_db(monkeypatch, _FakeDb([_ScalarResult(SimpleNamespace(media_type="video"))]))

        async def _no_frames(_inspiration):
            return []

        monkeypatch.setattr(ai_shared, "_resolve_analysis_frames", _no_frames)

        await ai_shared._run_analysis("insp-video", None)

        assert unavailable_logs == [
            ("insp-video", "分析失败：视频关键帧提取失败（视频文件缺失或 ffmpeg 异常）")
        ]


class TestSuccessPath:
    async def test_success_cleans_old_failure_logs_and_broadcasts(
        self, monkeypatch, broadcasts
    ):
        """分析成功：删掉该素材的旧失败日志并提交，广播 success=true。"""
        db = _FakeDb([_RowsResult([(11,), (12,)])])  # 两条旧失败日志
        _use_db(monkeypatch, db)

        async def _analyze_image(_db, _inspiration_id, _frame):
            return object()  # 真值 → 成功

        from app.services import ai_service

        monkeypatch.setattr(ai_service, "analyze_image", _analyze_image)

        await ai_shared._run_analysis("insp-ok", "images/ok.jpg")

        assert len(db.deleted) == 1  # 一条 DELETE 语句
        assert db.commits == 1
        assert broadcasts[-1] == {
            "type": "ai_analysis_done",
            "inspiration_id": "insp-ok",
            "success": True,
        }

    async def test_success_without_old_logs_skips_delete(self, monkeypatch, broadcasts):
        db = _FakeDb([_RowsResult([])])
        _use_db(monkeypatch, db)

        async def _analyze_image(_db, _iid, _frame):
            return object()

        from app.services import ai_service

        monkeypatch.setattr(ai_service, "analyze_image", _analyze_image)

        await ai_shared._run_analysis("insp-ok2", "images/ok.jpg")

        assert db.deleted == []
        assert db.commits == 0
        assert broadcasts[-1]["success"] is True

    async def test_video_uses_multi_frame_analysis(self, monkeypatch, broadcasts):
        """多帧（视频）走 analyze_video，单帧（图片）走 analyze_image。"""
        calls: list[str] = []
        # 视频素材：先查库拿到 inspiration（media_type=video），再由 _resolve 给出两帧
        _use_db(
            monkeypatch,
            _FakeDb([_ScalarResult(SimpleNamespace(id="insp-video2", media_type="video"))]),
        )

        from app.services import ai_service

        async def _analyze_video(_db, _iid, frames):
            calls.append(f"video:{len(frames)}")
            return object()

        async def _analyze_image(_db, _iid, frame):
            calls.append(f"image:{frame}")
            return object()

        monkeypatch.setattr(ai_service, "analyze_video", _analyze_video)
        monkeypatch.setattr(ai_service, "analyze_image", _analyze_image)

        async def _frames(_inspiration):
            return ["kf1.jpg", "kf2.jpg"]

        monkeypatch.setattr(ai_shared, "_resolve_analysis_frames", _frames)

        await ai_shared._run_analysis("insp-video2", None)

        assert calls == ["video:2"]


class TestFailurePaths:
    async def test_generic_failure_broadcasts_false(self, monkeypatch, broadcasts):
        _use_db(monkeypatch, _FakeDb())

        async def _boom(_db, _iid, _frame):
            raise RuntimeError("模型不可达")

        from app.services import ai_service

        monkeypatch.setattr(ai_service, "analyze_image", _boom)

        await ai_shared._run_analysis("insp-fail", "images/f.jpg")  # 不向上抛

        assert broadcasts[-1]["success"] is False
        assert "insp-fail" not in ai_shared._task_by_id

    async def test_cancellation_propagates_and_cleans_state(
        self, monkeypatch, broadcasts
    ):
        """任务被取消（用户点取消/服务关闭）：CancelledError 必须向上抛，状态照常清理。"""
        _use_db(monkeypatch, _FakeDb())

        async def _cancel(_db, _iid, _frame):
            raise asyncio.CancelledError

        from app.services import ai_service

        monkeypatch.setattr(ai_service, "analyze_image", _cancel)

        with pytest.raises(asyncio.CancelledError):
            await ai_shared._run_analysis("insp-cancel", "images/c.jpg")

        assert "insp-cancel" not in ai_shared._active_analyses
        assert broadcasts[-1]["success"] is False

    async def test_pause_blocks_until_resumed(self, monkeypatch, broadcasts):
        """队列暂停期间要等（避免继续烧显存），恢复后才真正开始分析。"""
        _use_db(monkeypatch, _FakeDb())
        sleeps: list[float] = []

        async def _sleep(seconds):
            sleeps.append(seconds)
            ai_shared.set_queue_paused(False)  # 第一次等待后恢复

        monkeypatch.setattr(ai_shared.asyncio, "sleep", _sleep)
        ai_shared.set_queue_paused(True)

        async def _analyze_image(_db, _iid, _frame):
            return object()

        from app.services import ai_service

        monkeypatch.setattr(ai_service, "analyze_image", _analyze_image)

        try:
            await ai_shared._run_analysis("insp-paused", "images/p.jpg")
        finally:
            ai_shared.set_queue_paused(False)

        assert sleeps == [1]  # 只等了一轮
        assert broadcasts[-1]["success"] is True


class TestHelpers:
    async def test_update_env_file_creates_and_applies(self, monkeypatch, tmp_path):
        """`.env` 不存在时创建；新键追加、已有键替换、其它配置原样保留。"""
        fake_root = tmp_path / "backend" / "app"
        fake_root.mkdir(parents=True)
        monkeypatch.setattr(ai_shared, "__file__", str(fake_root / "routers" / "x.py"))
        env_path = tmp_path / "backend" / ".env"
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text("A=1\nB=2", encoding="utf-8")  # 故意不带结尾换行

        await ai_shared._update_env_file({"B": "20", "C": "3"})

        content = env_path.read_text(encoding="utf-8")
        assert "A=1" in content
        assert "B=20" in content  # 替换而不是重复追加
        assert content.count("B=") == 1
        assert "C=3" in content
        assert content.endswith("\n")

    async def test_update_env_file_creates_missing_file(self, monkeypatch, tmp_path):
        fake_root = tmp_path / "backend" / "app"
        fake_root.mkdir(parents=True)
        monkeypatch.setattr(ai_shared, "__file__", str(fake_root / "routers" / "x.py"))
        env_path = tmp_path / "backend" / ".env"

        await ai_shared._update_env_file({"NEW_KEY": "v"})

        assert env_path.read_text(encoding="utf-8") == "NEW_KEY=v\n"

    @pytest.mark.parametrize(
        ("size", "expected"),
        [
            (0, "0 B"),
            (1, "1.0 B"),
            (1023, "1023.0 B"),
            (1024, "1.0 KB"),
            (1536, "1.5 KB"),
            (1024**2, "1.0 MB"),
            (1024**3, "1.0 GB"),
            (1024**5, "1024.0 TB"),  # 封顶到 TB，不再往上涨单位
        ],
    )
    def test_format_size(self, size, expected):
        assert ai_shared._format_size(size) == expected

    async def test_resolve_analysis_frames_delegates(self, monkeypatch):
        from app.services import video_service

        captured: list[object] = []

        async def _resolve(inspiration):
            captured.append(inspiration)
            return ["kf.jpg"]

        monkeypatch.setattr(video_service, "resolve_analysis_frames", _resolve)
        inspiration = SimpleNamespace(id="x", media_type="video")

        assert await ai_shared._resolve_analysis_frames(inspiration) == ["kf.jpg"]
        assert captured == [inspiration]

    def test_queue_state_helpers(self):
        assert ai_shared.get_queue_paused() is False
        ai_shared.set_queue_paused(True)
        try:
            assert ai_shared.get_queue_paused() is True
            ai_shared._active_analyses["a"] = "正在分析..."
            ai_shared._pending_queue.append("b")
            assert ai_shared.get_in_memory_queue_ids() == {"a", "b"}
        finally:
            ai_shared.set_queue_paused(False)
