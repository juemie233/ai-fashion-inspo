"""`app/main.py` 的 lifespan 启动/关闭与请求中间件测试。

lifespan 里每一步都是「失败不阻断启动」的兜底清理，原覆盖 75.6%。没测到的正是这些
**异常容错分支**：快照清理失败、摘要回填失败、保留策略失败、对账失败、垃圾桶清理失败
都只能告警不能阻止服务起来；同时它们成功时的日志（清了多少、补了多少）是运维排查依据。

中间件补：请求 ID 生成/复用与回传、慢请求告警、**未处理异常统一 500 并保留堆栈关联 ID**；
以及业务异常 → HTTP 状态码映射（含未映射类型按 500）。
"""

import asyncio
from types import SimpleNamespace

from starlette.requests import Request
from starlette.responses import PlainTextResponse

from app import main as main_module
from app.exceptions import (
    AppException,
    AuthenticationException,
    NotFoundException,
    ValidationException,
)
from app.services.person.base import (
    PersonConflictError,
    PersonHasInspirationsError,
    PersonNotFoundError,
)


class _FakeLogger:
    def __init__(self):
        self.infos: list[str] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def _fmt(self, message, args):
        return message % args if args else message

    def info(self, message, *args, **_kw):
        self.infos.append(self._fmt(message, args))

    def warning(self, message, *args, **_kw):
        self.warnings.append(self._fmt(message, args))

    def error(self, message, *args, **_kw):
        self.errors.append(self._fmt(message, args))

    def exception(self, message, *args, **_kw):
        self.errors.append(self._fmt(message, args))

    def debug(self, *_args, **_kw):
        pass


class _FakeSession:
    def __init__(self, rowcount=2):
        self.rowcount = rowcount
        self.commits = 0

    async def execute(self, _stmt):
        return SimpleNamespace(rowcount=self.rowcount)

    async def commit(self):
        self.commits += 1


def _session_factory(session: _FakeSession):
    def _factory():
        class _Ctx:
            async def __aenter__(self):
                return session

            async def __aexit__(self, *_exc):
                return False

        return _Ctx()

    return _factory


async def _never():
    await asyncio.Event().wait()


def _request(headers: dict | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/x",
            "raw_path": b"/api/x",
            "query_string": b"",
            "root_path": "",
            "headers": [
                (key.lower().encode(), value.encode()) for key, value in (headers or {}).items()
            ],
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1234),
        }
    )


def _patch_lifespan_deps(monkeypatch, logs, calls: list[str], *, failures=frozenset()):
    """把 lifespan 的外部依赖全部替换成记录/可控失败的替身。"""

    def _maybe(name):
        if name in failures:
            def _boom(*_a, **_k):
                raise RuntimeError(f"{name} 失败")
            return _boom
        return None

    monkeypatch.setattr(main_module, "setup_logging", lambda _n: calls.append("setup_logging"))
    monkeypatch.setattr(main_module, "setup_uvicorn_access_log", lambda: calls.append("access_log"))
    monkeypatch.setattr(main_module.settings, "host", "127.0.0.1")
    monkeypatch.setattr(main_module.settings, "api_key", "test-key")
    monkeypatch.setattr(main_module.settings, "backup_on_startup", False)
    monkeypatch.setattr(main_module.settings, "trash_retention_days", 7)
    monkeypatch.setattr(main_module.settings, "app_name", "测试应用")
    monkeypatch.setattr(main_module.settings, "app_version", "1.0.0")
    monkeypatch.setattr(main_module.settings, "port", 18888)
    monkeypatch.setattr(main_module, "run_migrations", lambda: calls.append("migrate"))

    async def _init_db():
        calls.append("init_db")

    async def _ensure_schema():
        calls.append("ensure_schema")

    monkeypatch.setattr(main_module, "init_db", _init_db)
    monkeypatch.setattr(main_module, "ensure_schema", _ensure_schema)

    # 过期快照清理（同步、走 to_thread）
    import app.routers.ai_reset as ai_reset

    boom = _maybe("snapshot")
    if boom is not None:
        monkeypatch.setattr(ai_reset, "cleanup_expired_snapshots", boom)
    else:
        monkeypatch.setattr(
            ai_reset, "cleanup_expired_snapshots", lambda: calls.append("snapshot")
        )

    # 摘要回填
    boom = _maybe("backfill")
    if boom is not None:
        async def _backfill():
            raise RuntimeError("backfill 失败")
        monkeypatch.setattr(main_module, "_backfill_task_result_summaries", _backfill)
    else:
        async def _backfill():
            calls.append("backfill")
            return 5

        monkeypatch.setattr(main_module, "_backfill_task_result_summaries", _backfill)

    # 保留策略
    import app.services.task_retention as retention

    if "prune" in failures:
        async def _prune():
            raise RuntimeError("prune 失败")
    else:
        async def _prune():
            calls.append("prune")
            return {"pruned": 2, "bytes": 3 * 1024 * 1024}

    monkeypatch.setattr(retention, "prune_task_results_in_session", _prune)

    # 出图批次对账
    import app.services.generation_service as generation_service

    if "reconcile" in failures:
        async def _reconcile(_db):
            raise RuntimeError("reconcile 失败")
    else:
        async def _reconcile(_db):
            calls.append("reconcile")
            return {"reconciled": 1}

    monkeypatch.setattr(generation_service, "reconcile_stale_generation_runs", _reconcile)

    # 僵尸采集任务清理 / 预设标签 / 合集根节点 / 垃圾桶清理共用假会话
    session = _FakeSession(rowcount=2 if "zombie" not in failures else 0)
    import app.database as database

    monkeypatch.setattr(database, "async_session", _session_factory(session))

    async def _seed_tags(_db):
        calls.append("seed_tags")
        return 3

    import app.services.tag_service as tag_service

    monkeypatch.setattr(tag_service, "seed_tags", _seed_tags)

    async def _ensure_roots(_db):
        calls.append("ensure_roots")
        return ["手动收藏", "自动收藏"]

    import app.services.collection_service as collection_service

    monkeypatch.setattr(collection_service, "ensure_root_collections", _ensure_roots)

    if "purge" in failures:
        async def _purge(_db, only_expired=True):
            raise RuntimeError("purge 失败")
    else:
        async def _purge(_db, only_expired=True):
            calls.append("purge")
            return {"deleted": 4}

    import app.services.inspiration_service as inspiration_service

    monkeypatch.setattr(inspiration_service, "purge_trash", _purge)

    # 周期任务替换为「挂起直到取消」，避免真实轮询
    monkeypatch.setattr(main_module, "_sweep_expired_trash", _never)
    monkeypatch.setattr(main_module, "_scraper_schedule_loop", _never)
    monkeypatch.setattr(main_module, "logger", logs)
    return session


class TestLifespan:
    async def test_startup_runs_all_steps_and_logs(self, monkeypatch):
        logs = _FakeLogger()
        calls: list[str] = []
        _patch_lifespan_deps(monkeypatch, logs, calls)

        async with main_module.lifespan(None):
            pass

        # 顺序：日志初始化 → 过期快照清理 → Alembic 迁移 → 建表兜底 → 其余清理
        assert calls[:2] == ["setup_logging", "access_log"]
        assert "snapshot" in calls and "migrate" in calls
        assert calls.index("snapshot") < calls.index("migrate")
        for step in ("backfill", "prune", "reconcile", "seed_tags", "ensure_roots", "purge"):
            assert step in calls, step
        assert any("已补齐 5 条任务列表摘要" in line for line in logs.infos)
        assert any("任务结果保留策略清空 2 条" in line for line in logs.infos)
        assert any("已对账 1 个停滞出图批次" in line for line in logs.infos)
        assert any("已清理 2 个僵尸采集任务" in line for line in logs.infos)
        assert any("已导入 3 个预设标签" in line for line in logs.infos)
        assert any("收藏合集一级节点就绪" in line for line in logs.infos)
        assert any("启动清理 4 个过期素材" in line for line in logs.infos)

    async def test_cleanup_failures_do_not_block_startup(self, monkeypatch):
        logs = _FakeLogger()
        calls: list[str] = []
        _patch_lifespan_deps(
            monkeypatch,
            logs,
            calls,
            failures=frozenset({"snapshot", "backfill", "prune", "reconcile", "purge"}),
        )

        async with main_module.lifespan(None) as _:
            pass  # 不抛即成功启动

        assert any("过期快照清理失败" in line for line in logs.warnings)
        assert any("任务列表摘要回填失败" in line for line in logs.warnings)
        assert any("任务结果保留清理失败" in line for line in logs.warnings)
        assert any("出图批次对账失败" in line for line in logs.warnings)
        assert any("启动清理失败" in line for line in logs.warnings)

    async def test_skips_trash_purge_and_backup_when_disabled(self, monkeypatch):
        logs = _FakeLogger()
        calls: list[str] = []
        _patch_lifespan_deps(monkeypatch, logs, calls)
        monkeypatch.setattr(main_module.settings, "trash_retention_days", 0)
        monkeypatch.setattr(main_module.settings, "backup_on_startup", False)

        async with main_module.lifespan(None):
            pass

        assert "purge" not in calls  # 禁用自动回收：不清理

    async def test_non_loopback_without_api_key_warns(self, monkeypatch):
        logs = _FakeLogger()
        calls: list[str] = []
        _patch_lifespan_deps(monkeypatch, logs, calls)
        monkeypatch.setattr(main_module.settings, "host", "0.0.0.0")
        monkeypatch.setattr(main_module.settings, "api_key", "")

        async with main_module.lifespan(None):
            pass

        assert any("安全警告" in line for line in logs.warnings)


class TestRequestContextMiddleware:
    async def test_generates_and_reuses_request_id(self):
        async def _ok(_request):
            return PlainTextResponse("ok")

        first = await main_module.request_context_middleware(_request(), _ok)
        assert len(first.headers["X-Request-ID"]) == 12

        second = await main_module.request_context_middleware(
            _request({"X-Request-ID": "abcdef123456"}), _ok
        )
        assert second.headers["X-Request-ID"] == "abcdef123456"

    async def test_slow_request_warning_and_context_reset(self, monkeypatch):
        logs = _FakeLogger()
        monkeypatch.setattr(main_module, "logger", logs)
        monkeypatch.setattr(main_module.settings, "slow_request_ms", 0)

        from app.logging_config import request_id_var

        before = request_id_var.get()

        async def _ok(_request):
            return PlainTextResponse("ok")

        response = await main_module.request_context_middleware(_request(), _ok)

        assert any("慢请求" in line for line in logs.warnings)
        assert request_id_var.get() == before  # 上下文已复位
        assert response.headers["X-Request-ID"]

    async def test_unhandled_exception_becomes_500_with_request_id(self, monkeypatch):
        logs = _FakeLogger()
        monkeypatch.setattr(main_module, "logger", logs)

        async def _boom(_request):
            raise RuntimeError("数据库炸了")

        response = await main_module.request_context_middleware(_request(), _boom)

        assert response.status_code == 500
        assert response.body == b'{"detail":"\xe6\x9c\x8d\xe5\x8a\xa1\xe5\x99\xa8\xe5\x86\x85\xe9\x83\xa8\xe9\x94\x99\xe8\xaf\xaf"}'
        assert response.headers["X-Request-ID"]
        assert any("未处理异常" in line for line in logs.errors)  # 堆栈日志带请求 ID


class TestExceptionHandlers:
    async def test_business_exception_status_mapping(self):
        mapped = await main_module.app_exception_handler(
            None, NotFoundException("素材", "abc")
        )
        assert mapped.status_code == 404

        validation = await main_module.app_exception_handler(
            None, ValidationException("count", "参数错")
        )
        assert validation.status_code == 400

        auth = await main_module.app_exception_handler(None, AuthenticationException("未登录"))
        assert auth.status_code == 401

        # 未映射的 AppException 子类按 500，消息原样返回便于排查
        unknown = await main_module.app_exception_handler(None, AppException("未知业务异常"))
        assert unknown.status_code == 500
        assert b"\xe6\x9c\xaa\xe7\x9f\xa5\xe4\xb8\x9a\xe5\x8a\xa1\xe5\xbc\x82\xe5\xb8\xb8" in unknown.body

    async def test_person_exception_handlers(self):
        assert (
            await main_module._person_not_found_handler(None, PersonNotFoundError("no"))
        ).status_code == 404
        assert (
            await main_module._person_conflict_handler(None, PersonConflictError("dup"))
        ).status_code == 409
        assert (
            await main_module._person_has_inspirations_handler(
                None, PersonHasInspirationsError("has")
            )
        ).status_code == 400
