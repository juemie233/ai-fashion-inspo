"""统一日志配置：API、worker 与服务脚本共用的 dictConfig 入口。

背景（2026-11-02 日志系统审计 P0-1，见 docs/ops/日志系统扫描与整改-2026-11-02.md §1）：
API 进程此前只跑 uvicorn 默认日志配置，而它只给 ``uvicorn`` / ``uvicorn.error`` /
``uvicorn.access`` 配 handler，**没有 root handler**。``app.*`` 的 logger 向 root
传播后无 handler 接手，INFO 级业务日志被静默丢弃（``logs/backend.log*`` 里
``app.*`` 一共 0 行），启动、定时采集、垃圾桶清理等关键信息全部看不到。

本模块负责：

- ``setup_logging()``：用 ``dictConfig`` 一次配好 root 与 uvicorn 命名空间，
  ``disable_existing_loggers=False``（不掐掉 alembic / sqlalchemy / 业务模块
  已经建好的 logger，参见 ``tests/test_logging_visibility.py`` 守的那条回归）；
- 统一文本格式：``时间 [级别] logger [req=… task=…]: 消息``，
  ``LOG_FORMAT=json`` 时切 JSON（本地人读为主，排障需要结构化时再开）；
- 请求/任务关联字段：``request_id`` / ``task_id`` 由 contextvar 提供，
  API 中间件与 worker 任务执行处分别写入，日志里自动带上；
- uvicorn 访问日志降噪：健康检查、WebSocket 生命周期、2xx/3xx 访问行不落盘，
  只保留 4xx/5xx（慢请求由 ``main.py`` 的中间件单独记 WARNING）。

``httpx`` / ``httpcore`` 默认是 INFO，每发一次 HTTP 请求记一行；worker 日志里
26% 是这类噪音。这里统一压到 WARNING，需要看请求明细时把 ``LOG_LEVEL``
调到 DEBUG 即可临时打开。
"""

from __future__ import annotations

import contextvars
import json
import logging
import logging.config
import re
from datetime import datetime
from pathlib import Path
from typing import Any

"""仓库根目录（``backend/app/logging_config.py`` → 上三级）。"""
PROJECT_ROOT = Path(__file__).resolve().parents[2]

"""日志目录：与 supervisor（``scripts/ops/supervisor.py``）写日志的位置一致。"""
LOG_DIR = PROJECT_ROOT / "logs"

"""统一文本日志格式（所有进程共用；contextvar 字段由 LogRecord 工厂补齐）。"""
TEXT_FORMAT = (
    "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d "
    "[req=%(request_id)s task=%(task_id)s]: %(message)s"
)

"""请求关联 ID：API 中间件每请求生成/继承一个，日志 filter 自动注入。"""
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)

"""任务关联 ID：worker 开始执行任务时写入，任务 runner 内所有日志自动带上。"""
task_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("task_id", default="-")

"""账本运行 ID：``task_run_logs`` 行的主键，用于把日志与一次具体执行对齐。"""
run_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("run_id", default="-")

_ORIGINAL_RECORD_FACTORY = logging.getLogRecordFactory()
_FACTORY_INSTALLED = False

"""uvicorn 访问日志里的健康检查行（supervisor 每 5 秒探一次，占 backend.log 一半）。"""
_HEALTH_RE = re.compile(r'"GET /api/health(?:[ ?"])')

"""WebSocket 连接生命周期行（匹配 ``- "WebSocket /api/ws" [accepted]`` 等）。"""
_WEBSOCKET_RE = re.compile(r'"WebSocket ')

"""访问日志行尾的状态码（``… HTTP/1.1" 200``）。"""
_STATUS_RE = re.compile(r'"\s+(\d{3})\s*$')


def _install_record_factory() -> None:
    """给每条 LogRecord 注入关联字段（contextvar 取值），格式串才敢直接用。

    为什么不用 logger filter：logger 上的 filter **只对直接写在该 logger 上的
    记录生效**，子 logger 传播到 root 时不会再走父 logger 的 filter；而
    ``DailyFileHandler`` 可能挂在任一 handler 上。改 LogRecord 工厂是唯一
    能保证「所有 handler、所有 logger」都拿得到字段的做法。
    """
    global _FACTORY_INSTALLED
    if _FACTORY_INSTALLED:
        return

    def _factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = _ORIGINAL_RECORD_FACTORY(*args, **kwargs)
        record.request_id = request_id_var.get()
        record.task_id = task_id_var.get()
        record.run_id = run_id_var.get()
        return record

    logging.setLogRecordFactory(_factory)
    _FACTORY_INSTALLED = True


class ContextFormatter(logging.Formatter):
    """文本格式：复用 ``TEXT_FORMAT``，并容忍第三方 record 缺字段的情况。"""

    def format(self, record: logging.LogRecord) -> str:
        # LogRecord 工厂已补齐字段；第三方库手工构造 record 时兜底
        for field, default in (
            ("request_id", "-"),
            ("task_id", "-"),
            ("run_id", "-"),
        ):
            if not hasattr(record, field):
                setattr(record, field, default)
        return super().format(record)


class JsonFormatter(logging.Formatter):
    """JSON 格式（``LOG_FORMAT=json``）：一行一个 JSON，便于 grep/jq 与后续入库。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created).astimezone().isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "line": record.lineno,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "task_id": getattr(record, "task_id", "-"),
            "run_id": getattr(record, "run_id", "-"),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False)


class UvicornNoiseFilter(logging.Filter):
    """uvicorn 访问/错误日志降噪。

    过滤规则（任一命中即丢弃）：

    - ``/api/health`` 健康检查：supervisor 每 5 秒一次，占 backend.log 约 55%，
      健康状态由 ``logs/service_status.json`` 负责，不需要重复写业务日志；
    - WebSocket 连接/断开生命周期：每次前端连接都打一行，没有排障价值；
    - 2xx/3xx 的访问行：只保留 4xx/5xx，慢请求由中间件单独记。

    非访问日志（如 ``Exception in ASGI application``）原样放行。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 —— 拿不到消息时不要因此丢掉日志
            return True
        if _WEBSOCKET_RE.search(message):
            return False
        if _HEALTH_RE.search(message):
            return False
        status = _STATUS_RE.search(message)
        if status and status.group(1)[0] in ("2", "3"):
            return False
        return True


def _install_noise_filters() -> None:
    """把降噪 filter 挂到 uvicorn 命名空间（重复调用安全）。

    挂在 **logger** 上而不是 handler 上：uvicorn 启动时会用自己的
    ``LOGGING_CONFIG`` 重配 handler，logger 上的 filter 不会被清掉；
    这样即便配置时机先后有差异，降噪仍然生效。
    """
    for name in ("uvicorn.access", "uvicorn.error"):
        target = logging.getLogger(name)
        if not any(isinstance(item, UvicornNoiseFilter) for item in target.filters):
            target.addFilter(UvicornNoiseFilter())


def resolve_level(name: str | int | None) -> int:
    """把 ``LOG_LEVEL`` 字符串解析成 logging 级别；非法值回退 INFO。"""
    if isinstance(name, int):
        return name
    level = getattr(logging, (name or "INFO").strip().upper(), None)
    return level if isinstance(level, int) else logging.INFO


def _settings() -> Any:
    """延迟导入 Settings（脚本/测试场景下 app.config 可能尚未加载）。"""
    from app.config import settings

    return settings


def setup_logging(service: str = "api") -> None:
    """配置 root + uvicorn 的日志（幂等，可重复调用）。

    Args:
        service: 进程标识（``api`` / ``worker`` / 脚本名），只用于自检日志。
    """
    settings = _settings()
    level = resolve_level(getattr(settings, "log_level", "INFO"))
    json_mode = str(getattr(settings, "log_format", "text")).strip().lower() == "json"
    formatter_name = "json" if json_mode else "text"

    config: dict[str, Any] = {
        "version": 1,
        # 关键：迁移/第三方库已经建好的 logger 不能被禁用（历史回归见
        # tests/test_logging_visibility.py；alembic fileConfig 默认是 True）
        "disable_existing_loggers": False,
        "formatters": {
            "text": {"()": "app.logging_config.ContextFormatter", "format": TEXT_FORMAT},
            "json": {"()": "app.logging_config.JsonFormatter"},
        },
        "handlers": {
            # API/worker 的 stdout/stderr 由 supervisor 捕获并轮转，这里只写控制台，
            # 不再自己开文件（避免同一份日志写两遍、轮转策略两套）
            "console": {
                "class": "logging.StreamHandler",
                "formatter": formatter_name,
                "stream": "ext://sys.stdout",
            },
        },
        # root 必须放在顶层 "root" 键（dictConfig 契约），不能塞进 loggers
        "root": {"level": level, "handlers": ["console"]},
        "loggers": {
            # uvicorn.* 各自挂 handler 且不向 root 传播：既不丢日志，也不重复落盘
            "uvicorn": {"level": level, "handlers": ["console"], "propagate": False},
            "uvicorn.error": {"level": level, "handlers": ["console"], "propagate": False},
            "uvicorn.access": {"level": level, "handlers": ["console"], "propagate": False},
        },
    }

    _install_record_factory()
    logging.config.dictConfig(config)
    _install_noise_filters()

    # 第三方 HTTP 客户端：每请求一行 INFO，worker.log 里占 26%。
    # LOG_LEVEL=DEBUG 时保留（明确要看请求明细的场景）。
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(
            logging.DEBUG if level <= logging.DEBUG else logging.WARNING
        )
    # SQLAlchemy 的 SQL 日志只在明确 DEBUG 时才有意义
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.DEBUG if level <= logging.DEBUG else logging.WARNING
    )

    logging.getLogger("app.logging_config").info(
        "[日志] 初始化完成 service=%s level=%s format=%s root_handlers=%s",
        service,
        logging.getLevelName(level),
        "json" if json_mode else "text",
        [type(handler).__name__ for handler in logging.getLogger().handlers],
    )


def setup_uvicorn_access_log() -> None:
    """startup 时再挂一次降噪 filter（uvicorn 的自身配置可能在 import 后重跑）。"""
    _install_noise_filters()


if __name__ == "__main__":  # 手工验证：python -m app.logging_config
    setup_logging("manual")
    logging.getLogger("app.demo").info("如果你能看到这行，root handler 已就绪")
