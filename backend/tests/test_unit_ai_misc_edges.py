"""AI 小模块与通用工具的边缘单测（直接调服务/工具函数）。

覆盖：
- ``ai_service/outfit_summary``：空标签 / 成功 / 正则兜底解析 / 去重限 3 /
  非 dict、非列表、空内容、HTTP 异常降级；
- ``routers/ai_outfit``：404 / 无小标签提示 / 成功汇总接线；
- ``ollama_utils``：主机端口解析、运行/端口探测、等待就绪、启动分支；
- ``inspiration_service.check_duplicate_hash``：未命中 / 命中 / 记录已删（路径 None）；
- ``model_config``：默认值、坏文件、读缓存、更新（None 忽略）、reset、copy；
- ``utils/task_summary``：_trim 各类型、预算内原样、超预算裁剪、丢 key、截断标记；
- ``utils/system_stats``：磁盘百分比、非 Windows、Windows 内存成功/失败/异常；
- ``utils/daily_log``：按天写文件、跨天切换、emit 异常、close。
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app.database import async_session
from app.models.inspiration import Inspiration
from app.models.tag import InspirationTag, Tag
from app.services import inspiration_service
from app.services import model_config
from app.services import ollama_utils
from app.services.ai_service import outfit_summary
from app.routers import ai_outfit
from app.utils import daily_log, system_stats, task_summary


# ═══════════════════════════════════════════════════════════════
#  ai_service/outfit_summary
# ═══════════════════════════════════════════════════════════════


class _FakeChatResponse:
    def __init__(self, content: str) -> None:
        self._content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"message": {"content": self._content}}


class _FakeHTTPClient:
    """httpx.AsyncClient 替身：记录构造参数与请求体。"""

    captured: list[dict] = []
    content = ""
    exc: Exception | None = None
    instances = 0

    def __init__(self, **kwargs) -> None:
        type(self).instances += 1
        type(self).captured.append(kwargs)

    async def __aenter__(self) -> "_FakeHTTPClient":
        return self

    async def __aexit__(self, *_args) -> bool:
        return False

    async def post(self, url: str, json=None):
        type(self).captured.append({"url": url, "json": json})
        if type(self).exc is not None:
            raise type(self).exc
        return _FakeChatResponse(type(self).content)


@pytest.fixture
def fake_chat(monkeypatch):
    """替换 httpx.AsyncClient，并固定模型配置的超时。"""
    _FakeHTTPClient.captured = []
    _FakeHTTPClient.content = ""
    _FakeHTTPClient.exc = None
    _FakeHTTPClient.instances = 0
    monkeypatch.setattr(httpx, "AsyncClient", _FakeHTTPClient)
    monkeypatch.setattr(outfit_summary, "get_model_config", lambda _m: {"timeout": 7})
    return _FakeHTTPClient


async def test_outfit_summary_empty_tags_skips_http(fake_chat):
    assert await outfit_summary.summarize_outfit_tags([]) == []
    assert fake_chat.instances == 0


async def test_outfit_summary_success_and_payload(fake_chat):
    fake_chat.content = '{"outfit_tags": ["法式连衣裙穿搭", "白色系穿搭"]}'

    result = await outfit_summary.summarize_outfit_tags(["连衣裙", "白色"])

    assert result == ["法式连衣裙穿搭", "白色系穿搭"]
    assert fake_chat.captured[0] == {"timeout": 7}
    request = fake_chat.captured[1]
    assert request["url"].endswith("/api/chat")
    payload = request["json"]
    assert payload["model"] == outfit_summary.settings.ollama_vision_model
    assert payload["think"] is False
    assert payload["options"]["num_predict"] == 512
    assert "连衣裙、白色" in payload["messages"][0]["content"]


async def test_outfit_summary_regex_fallback_dedup_and_limit(fake_chat):
    """JSON 外有废话时用正则兜底；去重、跳过空值、最多 3 个。"""
    fake_chat.content = '说明如下：{"outfit_tags": ["A", "A", "", "B", "C", "D"]} 以上'

    assert await outfit_summary.summarize_outfit_tags(["x"]) == ["A", "B", "C"]


async def test_outfit_summary_http_error_returns_empty(fake_chat):
    fake_chat.exc = httpx.ConnectError("网络不可用")
    assert await outfit_summary.summarize_outfit_tags(["x"]) == []


@pytest.mark.parametrize(
    "content",
    [
        "",  # 空响应
        "[1, 2, 3]",  # 顶层不是 dict
        '{"outfit_tags": "not-a-list"}',  # 非列表
        "完全无法解析",  # 没有 JSON 片段
    ],
)
async def test_outfit_summary_malformed_payload_returns_empty(fake_chat, content):
    fake_chat.content = content
    assert await outfit_summary.summarize_outfit_tags(["x"]) == []


# ═══════════════════════════════════════════════════════════════
#  routers/ai_outfit
# ═══════════════════════════════════════════════════════════════


async def _insp() -> Inspiration:
    async with async_session() as db:
        insp = Inspiration(file_path="images/outfit.jpg", media_type="image")
        db.add(insp)
        await db.commit()
        return insp


async def test_ai_outfit_404_and_no_tags(client):
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await ai_outfit.suggest_outfit_tags(inspiration_id="no-such", db=db)
    assert exc.value.status_code == 404
    assert exc.value.detail == "素材未找到"

    insp = await _insp()
    async with async_session() as db:
        body = await ai_outfit.suggest_outfit_tags(inspiration_id=insp.id, db=db)
    assert body == {
        "suggestions": [],
        "small_tags": [],
        "message": "素材暂无小标签，无法总结大标签",
    }


async def test_ai_outfit_success_wiring(client, monkeypatch):
    insp = await _insp()
    async with async_session() as db:
        tag = Tag(name="连衣裙", category="item_type")
        db.add(tag)
        await db.commit()
        db.add(InspirationTag(inspiration_id=insp.id, tag_id=tag.id))
        await db.commit()

    calls: list[list[str]] = []

    async def _fake(_tags: list[str]) -> list[str]:
        calls.append(_tags)
        return ["法式连衣裙穿搭"]

    monkeypatch.setattr("app.services.ai_service.summarize_outfit_tags", _fake)

    async with async_session() as db:
        body = await ai_outfit.suggest_outfit_tags(inspiration_id=insp.id, db=db)

    assert body == {
        "suggestions": ["法式连衣裙穿搭"],
        "small_tags": ["连衣裙"],
        "count": 1,
    }
    assert calls == [["连衣裙"]]


# ═══════════════════════════════════════════════════════════════
#  ollama_utils
# ═══════════════════════════════════════════════════════════════


def test_ollama_host_port_parsing(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ollama_base_url", "http://127.0.0.1:11434")
    assert ollama_utils._ollama_host_port() == ("127.0.0.1", 11434)

    monkeypatch.setattr(settings, "ollama_base_url", "http://localhost:9999")
    assert ollama_utils._ollama_host_port() == ("localhost", 9999)

    assert ollama_utils._is_windows() == (os.name == "nt")


class _FakeHTTPResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _FakeAsyncClient:
    def __init__(self, **_kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> bool:
        return False

    async def get(self, *_args, **_kwargs):
        return _FakeHTTPResponse(ollama_utils_test_status)


ollama_utils_test_status = 200


async def test_is_ollama_running_branches(monkeypatch):
    global ollama_utils_test_status
    monkeypatch.setattr(ollama_utils.httpx, "AsyncClient", _FakeAsyncClient)

    ollama_utils_test_status = 200
    assert await ollama_utils.is_ollama_running() is True

    ollama_utils_test_status = 503
    assert await ollama_utils.is_ollama_running() is False

    class _Boom(_FakeAsyncClient):
        async def get(self, *_args, **_kwargs):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(ollama_utils.httpx, "AsyncClient", _Boom)
    assert await ollama_utils.is_ollama_running() is False


async def test_is_ollama_port_listening_branches(monkeypatch):
    closed: list[bool] = []

    class _Writer:
        def close(self) -> None:
            closed.append(True)

        async def wait_closed(self) -> None:
            raise RuntimeError("already closed")  # contextlib.suppress 吞掉

    async def _connected(_host, _port):
        return object(), _Writer()

    monkeypatch.setattr(ollama_utils.asyncio, "open_connection", _connected)
    assert await ollama_utils.is_ollama_port_listening() is True
    assert closed == [True]

    async def _refused(_host, _port):
        raise OSError("连不上")

    monkeypatch.setattr(ollama_utils.asyncio, "open_connection", _refused)
    assert await ollama_utils.is_ollama_port_listening() is False


async def test_wait_ollama_ready_returns_hint(monkeypatch):
    monkeypatch.setattr(ollama_utils.httpx, "AsyncClient", _FakeAsyncClient)
    assert await ollama_utils._wait_ollama_ready() == "Ollama 正在启动中，请稍后重试分析任务"

    class _Boom(_FakeAsyncClient):
        async def get(self, *_args, **_kwargs):
            raise RuntimeError("尚未就绪")

    monkeypatch.setattr(ollama_utils.httpx, "AsyncClient", _Boom)
    assert await ollama_utils._wait_ollama_ready() == "Ollama 正在启动中，请稍后重试分析任务"


async def test_start_ollama_early_return_branches(monkeypatch):
    monkeypatch.setattr(ollama_utils, "_is_windows", lambda: False)
    assert await ollama_utils.start_ollama() is None

    monkeypatch.setattr(ollama_utils, "_is_windows", lambda: True)

    async def _running() -> bool:
        return True

    monkeypatch.setattr(ollama_utils, "is_ollama_running", _running)
    assert await ollama_utils.start_ollama() == "Ollama 已在运行"

    async def _not_running() -> bool:
        return False

    async def _listening() -> bool:
        return True

    async def _ready() -> str:
        return "等待中就绪"

    monkeypatch.setattr(ollama_utils, "is_ollama_running", _not_running)
    monkeypatch.setattr(ollama_utils, "is_ollama_port_listening", _listening)
    monkeypatch.setattr(ollama_utils, "_wait_ollama_ready", _ready)
    assert await ollama_utils.start_ollama() == "等待中就绪"


class _FakeProc:
    def __init__(self, stdout: bytes, stderr: bytes = b"") -> None:
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        return self._stdout, self._stderr


async def test_start_ollama_spawn_branches(monkeypatch):
    monkeypatch.setattr(ollama_utils, "_is_windows", lambda: True)

    async def _not_running() -> bool:
        return False

    async def _not_listening() -> bool:
        return False

    async def _ready() -> str:
        return "已就绪"

    monkeypatch.setattr(ollama_utils, "is_ollama_running", _not_running)
    monkeypatch.setattr(ollama_utils, "is_ollama_port_listening", _not_listening)
    monkeypatch.setattr(ollama_utils, "_wait_ollama_ready", _ready)

    async def _spawn_ok(*_args, **_kwargs):
        return _FakeProc(b"OK\n")

    monkeypatch.setattr(ollama_utils.asyncio, "create_subprocess_exec", _spawn_ok)
    assert await ollama_utils.start_ollama() == "已就绪"

    async def _spawn_fail(*_args, **_kwargs):
        return _FakeProc(b"FAIL\n", b"bind failed")

    monkeypatch.setattr(ollama_utils.asyncio, "create_subprocess_exec", _spawn_fail)
    assert await ollama_utils.start_ollama() == "无法启动 Ollama，请手动启动后重试"

    async def _spawn_boom(*_args, **_kwargs):
        raise OSError("powershell 不存在")

    monkeypatch.setattr(ollama_utils.asyncio, "create_subprocess_exec", _spawn_boom)
    result = await ollama_utils.start_ollama()
    assert result is not None and result.startswith("启动 Ollama 失败:")


# ═══════════════════════════════════════════════════════════════
#  inspiration_service.check_duplicate_hash
# ═══════════════════════════════════════════════════════════════


async def test_check_duplicate_hash_branches(client, monkeypatch):
    async with async_session() as db:
        insp = Inspiration(
            file_path="images/hash.jpg", media_type="image", content_hash="a" * 64
        )
        db.add(insp)
        await db.commit()
        insp_id = insp.id

    async def _not_found(_db, _hash):
        return None

    monkeypatch.setattr(inspiration_service, "find_duplicate_by_hash", _not_found)
    async with async_session() as db:
        assert await inspiration_service.check_duplicate_hash(db, "b" * 64) == {
            "exists": False,
            "inspiration_id": None,
            "file_path": None,
        }

    async def _found(_db, _hash):
        return insp_id

    monkeypatch.setattr(inspiration_service, "find_duplicate_by_hash", _found)
    async with async_session() as db:
        hit = await inspiration_service.check_duplicate_hash(db, "a" * 64)
    assert hit == {
        "exists": True,
        "inspiration_id": insp_id,
        "file_path": "images/hash.jpg",
    }

    async def _stale(_db, _hash):
        return "already-deleted-id"

    monkeypatch.setattr(inspiration_service, "find_duplicate_by_hash", _stale)
    async with async_session() as db:
        stale = await inspiration_service.check_duplicate_hash(db, "c" * 64)
    assert stale == {
        "exists": True,
        "inspiration_id": "already-deleted-id",
        "file_path": None,
    }


# ═══════════════════════════════════════════════════════════════
#  model_config
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def config_env(monkeypatch, tmp_path):
    """配置读写指到 tmp 文件，并隔离模块读缓存（结束恢复）。"""
    path = tmp_path / "model_configs.json"
    monkeypatch.setattr(model_config, "_CONFIG_FILE", path)
    monkeypatch.setattr(model_config, "_cache", {"mtime": None, "data": None})
    yield path
    model_config._cache["mtime"] = None
    model_config._cache["data"] = None


def test_model_config_defaults_and_bad_file(config_env):
    defaults = model_config.get_model_config("unknown")
    assert defaults["think"] is False
    expected_keys = {"timeout", "num_predict", "num_ctx", "temperature", "top_p", "top_k"}
    assert expected_keys <= set(defaults)
    assert model_config.get_all_model_configs() == {}

    config_env.write_text("not-json", encoding="utf-8")
    assert model_config.get_all_model_configs() == {}
    assert model_config.get_model_config("m")["think"] is False

    config_env.write_text(json.dumps([1, 2]), encoding="utf-8")
    assert model_config.get_all_model_configs() == {}  # 顶层非 dict 当空配置


def test_model_config_read_cache_by_mtime(config_env, monkeypatch):
    config_env.write_text(json.dumps({"m1": {"timeout": 11}}), encoding="utf-8")
    calls: list[str] = []
    real_read = Path.read_text

    def _counting(self, *args, **kwargs):
        calls.append(self.name)
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _counting)

    assert model_config.get_model_config("m1")["timeout"] == 11
    assert model_config.get_model_config("m1")["timeout"] == 11
    assert calls.count("model_configs.json") == 1  # mtime 未变命中缓存


async def test_update_model_config_merges_and_ignores_none(config_env):
    updated = await model_config.update_model_config(
        "m1", {"timeout": 30, "top_p": None}
    )
    assert updated["timeout"] == 30
    assert updated["top_p"] is not None  # 默认值保留（None 值被忽略）
    assert model_config.get_all_model_configs() == {"m1": {"timeout": 30}}

    merged = await model_config.update_model_config("m1", {"num_predict": 128})
    assert merged["timeout"] == 30 and merged["num_predict"] == 128
    saved = json.loads(config_env.read_text(encoding="utf-8"))
    assert saved["m1"] == {"timeout": 30, "num_predict": 128}


async def test_reset_and_copy_model_config(config_env):
    await model_config.update_model_config("a", {"timeout": 5})
    await model_config.copy_model_config("a", "b")

    assert model_config.get_all_model_configs()["b"] == {"timeout": 5}
    copied = model_config.get_all_model_configs()["b"]
    copy_dict = dict(copied)
    copy_dict["timeout"] = 6
    assert model_config.get_all_model_configs()["b"] == {"timeout": 5}  # 深拷贝

    # 源不存在：不动目标
    await model_config.copy_model_config("no-such", "c")
    assert "c" not in model_config.get_all_model_configs()

    reset = await model_config.reset_model_config("a")
    assert reset["timeout"] == model_config._defaults()["timeout"]
    assert "a" not in model_config.get_all_model_configs()
    assert "b" in model_config.get_all_model_configs()

    missing = await model_config.reset_model_config("never-existed")
    assert "never-existed" not in model_config.get_all_model_configs()
    assert missing["think"] is False


# ═══════════════════════════════════════════════════════════════
#  utils/task_summary
# ═══════════════════════════════════════════════════════════════


def test_trim_value_types():
    assert task_summary._trim(None) is None
    assert task_summary._trim(True) is True
    assert task_summary._trim(3) == 3
    assert task_summary._trim(1.5) == 1.5
    long_str = "x" * (task_summary.STRING_LIMIT + 10)
    trimmed = task_summary._trim(long_str)
    assert len(trimmed) == task_summary.STRING_LIMIT + 1 and trimmed.endswith("…")
    assert task_summary._trim(list(range(100))) == list(range(10))
    big_dict = {str(i): i for i in range(100)}
    assert len(task_summary._trim(big_dict)) == 40
    # 非 JSON 原生类型：降级为字符串
    class _Weird:
        def __str__(self) -> str:
            return "weird"

    assert task_summary._trim(_Weird()) == "weird"


def test_summarize_result_small_and_invalid():
    assert task_summary.summarize_result(None) is None
    assert task_summary.summarize_result({}) is None
    assert task_summary.summarize_result([1, 2]) is None  # type: ignore[arg-type]

    small = {"done": 1, "message": "ok"}
    assert task_summary.summarize_result(small) is small  # 预算内原样返回


def test_summarize_result_trims_oversize():
    result = {"done": 1, "text": "y" * 5000, "items": list(range(100))}
    summary = task_summary.summarize_result(result, limit=800)

    assert summary is not None
    assert summary["done"] == 1
    assert len(json.dumps(summary, ensure_ascii=False)) <= 800
    assert len(summary["text"]) <= task_summary.STRING_LIMIT + 1
    assert len(summary["items"]) <= task_summary.LIST_LIMIT


def test_summarize_result_drops_large_non_priority_keys():
    result = {
        "done": 1,
        "message": "完成",
        "big_a": "a" * 500,
        "big_b": "b" * 500,
        "big_c": "c" * 500,
    }
    summary = task_summary.summarize_result(result, limit=120)

    assert summary is not None
    assert summary["done"] == 1 and summary["message"] == "完成"
    assert len(json.dumps(summary, ensure_ascii=False)) <= 120


def test_summarize_result_priority_only_falls_back_to_marker():
    """优先字段自己就超预算：返回可解析的截断标记（不让列表 500）。"""
    result = {"done": "x" * 400, "text_done": "y" * 400}
    summary = task_summary.summarize_result(result, limit=100)

    assert summary == {"_truncated": True, "message": "结果过大，完整内容见任务详情"}


# ═══════════════════════════════════════════════════════════════
#  utils/system_stats
# ═══════════════════════════════════════════════════════════════


def test_disk_usage_percent(monkeypatch):
    monkeypatch.setattr(
        system_stats.shutil,
        "disk_usage",
        lambda _p: SimpleNamespace(total=200, used=50, free=150),
    )
    info = system_stats.disk_usage("C:/")
    assert info == {
        "path": "C:/",
        "total_bytes": 200,
        "used_bytes": 50,
        "free_bytes": 150,
        "used_percent": 25.0,
    }

    monkeypatch.setattr(
        system_stats.shutil,
        "disk_usage",
        lambda _p: SimpleNamespace(total=0, used=0, free=0),
    )
    assert system_stats.disk_usage("C:/")["used_percent"] == 0.0


def test_memory_usage_non_windows(monkeypatch):
    monkeypatch.setattr(system_stats.sys, "platform", "linux")
    assert system_stats.memory_usage() is None


class _FakeMemStatus:
    def __init__(self) -> None:
        self.dwMemoryLoad = 42
        self.ullTotalPhys = 1000
        self.ullAvailPhys = 580


def _fake_ctypes(ok: bool = True, raises: bool = False):
    def _global_memory(_stat) -> bool:
        if raises:
            raise OSError("kernel32 不可用")
        return ok

    return SimpleNamespace(
        sizeof=lambda _cls: 8,
        byref=lambda obj: obj,
        windll=SimpleNamespace(kernel32=SimpleNamespace(GlobalMemoryStatusEx=_global_memory)),
    )


def test_memory_usage_windows_branches(monkeypatch):
    monkeypatch.setattr(system_stats.sys, "platform", "win32")
    monkeypatch.setattr(system_stats, "_MEMORYSTATUSEX", _FakeMemStatus)

    monkeypatch.setattr(system_stats, "ctypes", _fake_ctypes(ok=True))
    assert system_stats.memory_usage() == {
        "used_percent": 42.0,
        "total_bytes": 1000,
        "available_bytes": 580,
    }

    monkeypatch.setattr(system_stats, "ctypes", _fake_ctypes(ok=False))
    assert system_stats.memory_usage() is None

    monkeypatch.setattr(system_stats, "ctypes", _fake_ctypes(raises=True))
    assert system_stats.memory_usage() is None


# ═══════════════════════════════════════════════════════════════
#  utils/daily_log
# ═══════════════════════════════════════════════════════════════


class _FakeDateTime:
    """可控「当前日期」的 datetime 替身（只用到 now/strftime）。"""

    current = datetime(2026, 1, 1, 12, 0, 0)

    @classmethod
    def now(cls):
        return cls.current


def _record(message: str = "测试日志", args=()) -> logging.LogRecord:
    return logging.LogRecord(
        name="daily", level=logging.INFO, pathname=__file__, lineno=1,
        msg=message, args=args, exc_info=None,
    )


def test_daily_log_writes_and_switches_day(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_log, "datetime", _FakeDateTime)
    handler = daily_log.DailyFileHandler(tmp_path, prefix="analyze")
    handler.setFormatter(logging.Formatter("%(message)s"))
    try:
        _FakeDateTime.current = datetime(2026, 1, 1, 12, 0, 0)
        handler.emit(_record("第一天 %s", ("A",)))
        first_file = handler._file
        assert first_file is not None and not first_file.closed
        day1 = tmp_path / "analyze-2026-01-01.log"
        assert day1.read_text(encoding="utf-8").strip() == "第一天 A"

        # 跨天：旧文件被关闭，新文件按新日期建
        _FakeDateTime.current = datetime(2026, 1, 2, 8, 0, 0)
        handler.emit(_record("第二天"))
        assert first_file.closed is True
        day2 = tmp_path / "analyze-2026-01-02.log"
        assert day2.read_text(encoding="utf-8").strip() == "第二天"
        assert handler._date == "2026-01-02"
    finally:
        handler.close()


def test_daily_log_emit_failures_are_swallowed(tmp_path, monkeypatch):
    handler = daily_log.DailyFileHandler(tmp_path, prefix="x")
    handled: list[logging.LogRecord] = []
    monkeypatch.setattr(handler, "handleError", handled.append)

    # 1) _ensure_file 内部抛错 → emit 捕获并交给 handleError
    def _boom() -> None:
        raise OSError("磁盘只读")

    monkeypatch.setattr(handler, "_ensure_file", _boom)
    record = _record()
    handler.emit(record)
    assert handled == [record]

    # 2) _ensure_file 没建出文件（_file 仍为 None）→ 静默返回
    handled.clear()
    monkeypatch.setattr(handler, "_ensure_file", lambda: None)
    handler.emit(_record())
    assert handled == []

    # 3) close 无文件时安全
    handler.close()
    assert handler._file is None


def test_daily_log_close_closes_file(tmp_path):
    handler = daily_log.DailyFileHandler(tmp_path)
    handler._ensure_file()
    file_obj = handler._file
    handler.close()
    assert file_obj is not None and file_obj.closed is True
    assert handler._file is None
