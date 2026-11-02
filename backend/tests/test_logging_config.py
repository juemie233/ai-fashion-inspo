"""日志配置回归（2026-11-02 日志全量扫描 P0-1 / P1-5）。

守三件此前失守的事：

1. API/worker 启动后 **root 必须有 handler**——uvicorn 默认配置只配
   ``uvicorn.*``，``app.*`` 日志向 root 传播后无人接手，INFO 直接被丢弃；
2. 请求/任务关联字段（``request_id`` / ``task_id``）要真的出现在日志行里；
3. worker 的按天 ``analyze-YYYY-MM-DD.log`` 要真的落盘（不是只测 handler 类）。

另附 uvicorn 访问日志降噪（P0-2）与 f2 子进程输出改道（P0-3）的用例。
"""

import logging
import sys

import pytest

from app.logging_config import (
    UvicornNoiseFilter,
    request_id_var,
    setup_logging,
    task_id_var,
)


def _uvicorn_access_record(path: str, status: int) -> logging.LogRecord:
    """构造一条 uvicorn 访问日志记录（与 h11 实现的实际 args 一致）。"""
    template = '%s - "%s %s HTTP/%s" %d'
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        template,
        ("127.0.0.1:12345", "GET", path, "1.1", status),
        None,
    )


def _remove_daily_handlers() -> None:
    """移除测试期间加上的按天文件 handler，避免污染后续用例。"""
    root = logging.getLogger()
    for handler in root.handlers[:]:
        if type(handler).__name__ == "DailyFileHandler":
            root.removeHandler(handler)
            handler.close()


def test_setup_logging_配置_root_handler且业务日志可见(capsys):
    """P0-1：配置后 root 有 handler，``app.*`` 的 INFO 日志能写进标准输出。"""
    setup_logging("test")

    root = logging.getLogger()
    assert root.handlers, "root 必须有 handler，否则 app.* INFO 日志会被静默丢弃"

    logging.getLogger("app.demo").info("业务日志可见")
    out = capsys.readouterr().out
    assert "app.demo" in out
    assert "业务日志可见" in out


def test_record_工厂注入_request_id(capsys):
    """P1-3：contextvar 里的 request_id / task_id 自动进入日志行。"""
    setup_logging("test")
    request_token = request_id_var.set("req-abc123")
    task_token = task_id_var.set("#456")
    try:
        logging.getLogger("app.demo").info("带关联 ID 的日志")
        out = capsys.readouterr().out
    finally:
        request_id_var.reset(request_token)
        task_id_var.reset(task_token)

    assert "req=req-abc123" in out
    assert "task=#456" in out


@pytest.mark.parametrize(
    ("path", "status"),
    [
        ("/api/health", 200),  # supervisor 每 5 秒一次，必须丢
        ("/api/inspirations", 200),  # 2xx 访问行不再落盘
        ("/api/inspirations", 304),  # 3xx 同样丢
    ],
)
def test_降噪filter丢弃健康检查与2xx(path, status):
    """P0-2：健康检查/2xx/3xx 访问行不落盘。"""
    assert UvicornNoiseFilter().filter(_uvicorn_access_record(path, status)) is False


def test_降噪filter保留4xx5xx与真实错误():
    """P0-2：4xx/5xx 访问行、非访问类错误日志必须保留。"""
    noise_filter = UvicornNoiseFilter()
    assert noise_filter.filter(_uvicorn_access_record("/api/tasks", 500)) is True
    assert noise_filter.filter(_uvicorn_access_record("/api/tasks", 404)) is True
    error = logging.LogRecord(
        "uvicorn.error", logging.ERROR, __file__, 1, "Exception in ASGI application", None, None
    )
    assert noise_filter.filter(error) is True
    websocket = logging.LogRecord(
        "uvicorn.error",
        logging.INFO,
        __file__,
        1,
        '%s - "WebSocket %s" [accepted]',
        ("127.0.0.1:1234", "/api/ws"),
        None,
    )
    assert noise_filter.filter(websocket) is False


def test_worker_按天日志真的落盘(tmp_path, monkeypatch):
    """P1-5：``_setup_logging`` 后 ``analyze-YYYY-MM-DD.log`` 存在且包含启动日志。"""
    from app import worker

    monkeypatch.setattr(worker, "_LOG_DIR", tmp_path)
    try:
        worker._setup_logging()
        logging.getLogger("app.worker.test").info("按天日志落盘检查")
    finally:
        _remove_daily_handlers()

    files = list(tmp_path.glob("analyze-*.log"))
    assert files, f"未生成按天日志，目录内容：{list(tmp_path.iterdir())}"
    content = files[0].read_text(encoding="utf-8")
    assert "按天日志落盘检查" in content


def test_f2子进程输出改道独立文件(tmp_path, monkeypatch):
    """P0-3：f2 子进程输出写 ``logs/f2-*.log``，不再直灌 worker.log。"""
    from app import logging_config
    from app.services.task_runners.f2_progress import _run_subprocess

    monkeypatch.setattr(logging_config, "LOG_DIR", tmp_path)
    rc = _run_subprocess([sys.executable, "-c", "print('hello-f2-output')"], tmp_path)

    assert rc == 0
    logs = list(tmp_path.glob("f2-*.log"))
    assert len(logs) == 1
    assert "hello-f2-output" in logs[0].read_text(encoding="utf-8")


def test_真实启动链路配置了root_handler并回传request_id(client):
    """P0-1/P1-3：走 FastAPI lifespan（即真实启动路径）后 root 有 handler，
    且响应头带回 X-Request-ID。"""
    response = client.get("/api/health")

    assert response.status_code == 200
    request_id = response.headers.get("X-Request-ID")
    assert request_id and len(request_id) >= 6
    root = logging.getLogger()
    assert root.handlers, "lifespan 启动后 root 必须已有 handler"
