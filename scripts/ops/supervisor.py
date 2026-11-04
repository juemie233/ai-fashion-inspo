"""服务守护进程：管理后端 / 前端 / worker 的启动、健康检查、崩溃自动拉起、日志轮转与资源告警。

用法:
    python scripts/ops/supervisor.py

职责（对应 TODO「服务守护与监控」）：
- 启动并监控三个服务进程，任一异常退出后自动拉起并记录退出码与原因；
- 健康检查：后端/前端走 HTTP 探针，worker 走进程存活判断，连续失败强制重启；
- 日志轮转：捕获子进程 stdout/stderr，日志超限自动轮转（保留 N 份）；
- 资源告警：磁盘 / 内存占用超阈值时写入 supervisor 日志；
- 幂等：通过 PID 心跳文件避免重复启动多个 supervisor 实例；
- 状态落盘：每轮把各服务状态写入 logs/service_status.json，供命令行脚本兜底读取。
"""

from __future__ import annotations

import ctypes
import json
import logging
import logging.handlers
import math
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

# ── 路径与端口（与 scripts/ops/restart.sh、scripts/ops/ensure-services.sh 约定一致）──
PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "backend"
FRONTEND_DIR = PROJECT_ROOT / "web"
LOG_DIR = PROJECT_ROOT / "logs"
PID_FILE = LOG_DIR / "supervisor.pid"
STATUS_FILE = LOG_DIR / "service_status.json"

BACKEND_PORT = int(os.environ.get("BACKEND_PORT", "18888"))
FRONTEND_PORT = int(os.environ.get("FRONTEND_PORT", "17777"))


def _dotenv_value(key: str) -> str | None:
    """从 backend/.env 读一个值（找不到返回 None）。

    后端自身用 pydantic-settings 读这个文件；supervisor 是另一个进程，之前完全没读，
    于是 `.env` 里写 `HOST=0.0.0.0` 也不生效 —— uvicorn 被硬编码绑到 127.0.0.1，
    手机连同一个局域网也连不上（2026-10-23 真机排查确认）。
    """
    env_file = BACKEND_DIR / ".env"
    try:
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip().strip('"').strip("'")
    except OSError:
        return None
    return None


"""绑定地址：显式环境变量 > backend/.env 的 HOST > 仅本机。

真机（手机）访问必须绑 0.0.0.0；默认仍保守选 127.0.0.1，避免无意中把无鉴权的
读接口暴露到局域网。
"""
BACKEND_HOST = os.environ.get("BACKEND_HOST") or _dotenv_value("HOST") or "127.0.0.1"

# 端口同样以 .env 为准：否则改 PORT 后 supervisor 仍把 uvicorn 绑到 18888
if "BACKEND_PORT" not in os.environ:
    _env_port = _dotenv_value("PORT")
    if _env_port and _env_port.isdigit():
        BACKEND_PORT = int(_env_port)

# ── 调参 ──
CHECK_INTERVAL = 5.0            # 健康检查轮询间隔（秒）
RESOURCE_CHECK_INTERVAL = 60.0  # 资源告警检查间隔（秒）
MAX_FAIL_STREAK = 3             # 连续健康检查失败次数阈值（触发强制重启）
LOG_MAX_BYTES = 10 * 1024 * 1024  # 单个服务日志轮转阈值（10 MB）
LOG_BACKUP_COUNT = 5            # 日志轮转保留份数
FAST_CRASH_SECONDS = 30.0       # 运行不足此时长即退出视为「快速崩溃」
MAX_BACKOFF_SECONDS = 60.0      # 快速崩溃重启的最大退避延迟（秒）
DISK_ALERT_PERCENT = 90.0       # 磁盘使用率告警阈值（%）
MEMORY_ALERT_PERCENT = 90.0     # 内存使用率告警阈值（%）


def _env_float(key: str, default: float) -> float:
    """读环境变量 / backend/.env 的浮点配置，非法/非有限值回退默认。"""
    raw = os.environ.get(key) or _dotenv_value(key)
    try:
        value = float(raw) if raw else default
    except (TypeError, ValueError):
        return default
    # P2-7：nan/inf 会让 age <= stale 永远为 False（nan）或永远健康（inf），直接回退
    return value if math.isfinite(value) else default


# worker 心跳过期阈值（秒）：超过则视为「进程活着但事件循环卡死」，按连续健康检查
# 失败强制重启。2026-11-02 事故：worker 进程 pid 在、心跳 23:41:34 后断了，任务队列
# 停摆 20 分钟无人拉起——supervisor 原先对 worker 只看进程存活，这是补上的盲区。
HEARTBEAT_INTERVAL_SECONDS = 10.0  # 与 backend/app/worker.py 的 _HEARTBEAT_INTERVAL 一致
MIN_HEARTBEAT_STALE_SECONDS = 30.0  # 低于该值会把偶发一次慢心跳判死，强制回退默认
WORKER_HEARTBEAT_STALE_RAW = _env_float("WORKER_HEARTBEAT_STALE_SECONDS", 90.0)
WORKER_HEARTBEAT_STALE_SECONDS = WORKER_HEARTBEAT_STALE_RAW
if not (
    math.isfinite(WORKER_HEARTBEAT_STALE_RAW)
    and MIN_HEARTBEAT_STALE_SECONDS <= WORKER_HEARTBEAT_STALE_RAW
):
    WORKER_HEARTBEAT_STALE_SECONDS = 90.0

"""启动时要打印的配置告警（logger 在 run() 里才配置好，先收集后输出）。"""
_CONFIG_WARNINGS: list[str] = []
if WORKER_HEARTBEAT_STALE_SECONDS != WORKER_HEARTBEAT_STALE_RAW:
    _CONFIG_WARNINGS.append(
        "WORKER_HEARTBEAT_STALE_SECONDS="
        f"{WORKER_HEARTBEAT_STALE_RAW:g} 非法（需有限值且 ≥"
        f"{MIN_HEARTBEAT_STALE_SECONDS:g}s，心跳间隔 {HEARTBEAT_INTERVAL_SECONDS:g}s），"
        f"已回退 {WORKER_HEARTBEAT_STALE_SECONDS:g}s"
    )

# ── 服务定义 ──
SERVICE_DEFS = {
    "backend": {
        "cmd": [
            sys.executable, "-m", "uvicorn", "app.main:app",
            "--host", BACKEND_HOST, "--port", str(BACKEND_PORT),
        ],
        "cwd": BACKEND_DIR,
        "env_extra": {
            "PYTHONUTF8": "1",           # 中文日志以 UTF-8 落盘
            "PYTHONUNBUFFERED": "1",     # 输出到管道时行缓冲，保证日志实时
            "PYTHONIOENCODING": "utf-8",
        },
        "health_url": f"http://127.0.0.1:{BACKEND_PORT}/api/health",
        "startup_grace": 40.0,           # 后端迁移 + 建表耗时较长
    },
    "frontend": {
        "cmd": [
            "node", "node_modules/vite/bin/vite.js",
            "--host", "127.0.0.1", "--port", str(FRONTEND_PORT),
        ],
        "cwd": FRONTEND_DIR,
        # 去掉 ANSI 颜色码，日志干净（FORCE_COLOR 对 vite 不够，补 NO_COLOR/CLICOLOR）
        "env_extra": {"FORCE_COLOR": "0", "NO_COLOR": "1", "CLICOLOR": "0"},
        "health_url": f"http://127.0.0.1:{FRONTEND_PORT}/",
        "startup_grace": 30.0,
    },
    "worker": {
        "cmd": [sys.executable, "-m", "app.worker"],
        "cwd": BACKEND_DIR,
        "env_extra": {
            "PYTHONUTF8": "1",
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
        },
        "health_url": None,              # worker 无 HTTP 端口，用进程存活判断
        "startup_grace": 15.0,
    },
}


def _creation_flags() -> int:
    """返回子进程创建标志：Windows 下新进程组 + 无控制台窗口。"""
    if os.name == "nt":
        return subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    return 0


# ═══════════════════════════════════════════════════════════════
#  日志轮转写入器
# ═══════════════════════════════════════════════════════════════

class RotatingFileWriter:
    """线程安全的按大小轮转文件写入器。

    用于把子进程 stdout/stderr 写入带轮转的日志文件：超过阈值时
    ``name.log`` → ``name.log.1`` → ``name.log.2`` …，最多保留 backup_count 份。
    """

    def __init__(self, path: Path, max_bytes: int, backup_count: int):
        self.path = path
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self._lock = threading.Lock()
        self._fh = None
        self._open()

    def _open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a", encoding="utf-8", errors="replace")

    def write(self, text: str) -> None:
        with self._lock:
            if self._fh is None:
                return
            self._fh.write(text)
            self._fh.flush()
            if self._fh.tell() >= self.max_bytes:
                self._rotate()

    def _rotate(self) -> None:
        """轮转日志：移除最老备份，逐级后移，当前文件转为 .1。

        轮转失败（Windows 上日志被杀毒/索引占用很常见）不能让写入异常冒到
        读取线程：那会让 stdout 管道停止被消费，子进程可能写满管道后卡死。
        """
        assert self._fh is not None
        try:
            self._fh.close()
            self._fh = None
            oldest = self.path.with_name(f"{self.path.name}.{self.backup_count}")
            if oldest.exists():
                oldest.unlink()
            for i in range(self.backup_count - 1, 0, -1):
                src = self.path.with_name(f"{self.path.name}.{i}")
                if src.exists():
                    dst = self.path.with_name(f"{self.path.name}.{i + 1}")
                    if dst.exists():
                        dst.unlink()
                    src.replace(dst)
            if self.path.exists():
                self.path.replace(self.path.with_name(f"{self.path.name}.1"))
        except OSError as exc:
            print(f"[supervisor] 日志轮转失败（{self.path}）：{exc}", file=sys.stderr)
        finally:
            # 无论轮转是否成功都重新打开当前日志；打不开就放弃写日志，
            # 但绝不能把异常抛给读取线程（见 docstring）。
            try:
                self._open()
            except OSError as exc:
                print(f"[supervisor] 重新打开日志失败（{self.path}）：{exc}", file=sys.stderr)
                self._fh = None

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None


# ═══════════════════════════════════════════════════════════════
#  托管服务
# ═══════════════════════════════════════════════════════════════

class ManagedService:
    """一个受守护的子服务：启动、健康检查、退避重启、退出原因记录。"""

    def __init__(self, name: str, cfg: dict, log: RotatingFileWriter):
        self.name = name
        self.cmd = cfg["cmd"]
        self.cwd = cfg["cwd"]
        self.health_url = cfg.get("health_url")
        self.grace = cfg.get("startup_grace", 30.0)
        env = dict(os.environ)
        env.update(cfg.get("env_extra", {}))
        self.env = env
        self.log = log

        self.proc: subprocess.Popen | None = None
        self.reader: threading.Thread | None = None
        self.started_at = 0.0
        self.restart_at: float | None = None
        self.fail_streak = 0
        self.crash_streak = 0
        self.restart_count = 0
        self.last_exit_code: int | None = None
        self.last_exit_reason: str | None = None
        # 心跳读取缓存（P2-8：同一轮健康检查/状态文件只读一次库）
        self._hb_reading: HeartbeatReading | None = None
        self._hb_cache_at = 0.0
        self._hb_error_logged: str | None = None

    def start(self) -> None:
        """启动子进程并开启日志读取线程。"""
        self.proc = subprocess.Popen(
            self.cmd,
            cwd=str(self.cwd),
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=_creation_flags(),
        )
        self.started_at = time.time()
        self.fail_streak = 0
        self.reader = threading.Thread(
            target=self._read_loop, args=(self.proc,), daemon=True
        )
        self.reader.start()
        self.log.write(f"\n===== [{self.name}] 启动 PID {self.proc.pid} =====\n")

    def _read_loop(self, proc: subprocess.Popen) -> None:
        """持续读取子进程 stdout 写入日志，进程结束后关闭管道。"""
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                self.log.write(line)
        except Exception:
            pass
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def age(self) -> float:
        return time.time() - self.started_at

    def heartbeat_reading(self, *, refresh: bool = False) -> HeartbeatReading:
        """该服务进程的心跳读取结果（仅 worker 有；短缓存避免一轮读两次库）。"""
        if self.name != "worker" or self.proc is None or self.proc.poll() is not None:
            return HeartbeatReading(None, None)
        now = time.monotonic()
        if (
            not refresh
            and self._hb_reading is not None
            and now - self._hb_cache_at < 2.0
        ):
            return self._hb_reading
        reading = _read_worker_heartbeat(self.proc.pid)
        self._hb_reading = reading
        self._hb_cache_at = now
        if reading.error:
            if reading.error != self._hb_error_logged:
                # P2-9：读失败只记一次 warning + 状态文件 read_error，排障不用靠猜
                logging.getLogger("supervisor").warning(
                    "worker 心跳读取失败（不据此重启 worker）：%s", reading.error
                )
                self._hb_error_logged = reading.error
        else:
            self._hb_error_logged = None
        return reading

    def heartbeat_age(self) -> float | None:
        """该服务进程的心跳距今秒数（仅 worker 有；读不到返回 None）。"""
        return self.heartbeat_reading().age

    def healthy(self) -> bool:
        """健康判定：worker = 进程存活 + 心跳新鲜；其余用 HTTP 探针。

        worker 没有 HTTP 端口，历史实现只判进程存活——进程活着但事件循环卡死时
        supervisor 不会重启（2026-11-02 任务队列停摆 20 分钟的根因）。现在读任务库里
        worker 的心跳年龄：超过 ``WORKER_HEARTBEAT_STALE_SECONDS`` 即不健康，
        连续 ``MAX_FAIL_STREAK`` 次后走既有的强制重启逻辑。

        P1-1：读不到库（``error`` 非空）不是 worker 的问题，**返回健康且不累计
        fail_streak**，避免 DATABASE_URL 配错/库长时间被锁时把 worker 反复重启；
        只有「库可读但心跳年龄超阈值」才判不健康。
        """
        if self.health_url is None:
            reading = self.heartbeat_reading()
            if reading.error:
                return True  # 读失败不误杀；错误已记 supervisor.log + 状态文件
            if reading.age is None:
                # 还没写出第一行心跳：启动宽限（grace + 心跳阈值）内视为健康
                return self.age() < self.grace + WORKER_HEARTBEAT_STALE_SECONDS
            return reading.age <= WORKER_HEARTBEAT_STALE_SECONDS
        return _http_ok(self.health_url)

    def stop(self) -> None:
        """终止子进程（先温和 terminate，超时强杀）。"""
        proc = self.proc
        if proc is None:
            return
        if proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self.proc = None

    def handle_exit(self) -> None:
        """处理子进程退出：记录退出码，计算退避延迟，安排重启。"""
        code = self.proc.poll() if self.proc else None
        self.last_exit_code = code
        self.restart_count += 1
        run_duration = self.age()
        if run_duration < FAST_CRASH_SECONDS:
            self.crash_streak += 1  # 快速崩溃：指数退避
        else:
            self.crash_streak = 0
        delay = min(MAX_BACKOFF_SECONDS, 2 ** self.crash_streak)
        self.last_exit_reason = f"退出码 {code}，运行 {run_duration:.0f}s"
        self.log.write(
            f"[supervisor] {self.name} 异常退出（{self.last_exit_reason}），"
            f"{delay:.0f}s 后自动重启（第 {self.restart_count} 次）\n"
        )
        self.restart_at = time.time() + delay


# ═══════════════════════════════════════════════════════════════
#  探针与资源统计
# ═══════════════════════════════════════════════════════════════

def _http_ok(url: str, timeout: float = 3.0) -> bool:
    """HTTP 探针：返回目标是否返回 < 500 状态码。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status < 500
    except Exception:
        return False


def _disk_used_percent(path: Path | None = None) -> float:
    try:
        usage = shutil.disk_usage(str(path or PROJECT_ROOT))
        return usage.used / usage.total * 100
    except Exception:
        return 0.0


def _storage_root() -> Path:
    """素材存储根：数据盘可能与项目不在同一块盘（.env 的 STORAGE_ROOT）。"""
    raw = _dotenv_value("STORAGE_ROOT")
    if raw:
        path = Path(raw)
        return path if path.is_absolute() else BACKEND_DIR / path
    return BACKEND_DIR / "storage"


def _database_path() -> Path:
    """任务库路径（.env 的 DATABASE_URL 优先，缺省 backend/fashion_inspo.db）。

    只用于 supervisor 读 worker 心跳（只读）：不能 import app.config（那会拉起
    pydantic 全家桶，supervisor 应保持极轻量）。
    """
    raw = os.environ.get("DATABASE_URL") or _dotenv_value("DATABASE_URL") or ""
    if "///" in raw:
        path_text = raw.split("///", 1)[1]
        candidate = Path(path_text)
        return candidate if candidate.is_absolute() else BACKEND_DIR / candidate
    return BACKEND_DIR / "fashion_inspo.db"


class HeartbeatReading(NamedTuple):
    """一次 worker 心跳读取结果。

    ``age`` 为 None 且 ``error`` 为 None = 还没写出心跳行（启动宽限）；
    ``error`` 非空 = 读者自己读不到（库不存在/被锁/查询失败），**不等于 worker 死了**。
    """

    age: float | None
    error: str | None = None


def _read_worker_heartbeat(pid: int | None) -> HeartbeatReading:
    """读该 worker 进程最近一次心跳；区分「没有心跳行」与「读失败」。

    2026-11-03 评审 P1-1：原实现把库不存在/被锁/查询失败都返回 None，健康判定把
    None 当成「还没写出第一行心跳」，超过启动宽限就周期性强制重启 worker ——
    与文档「库读不到不误杀」相反。现在读失败带 ``error`` 返回，健康判定不累计失败。
    只读连接（mode=ro）：supervisor 不该以写权限打开任务库。
    """
    if not pid:
        return HeartbeatReading(None, "worker pid 为空")
    db_path = _database_path()
    if not db_path.exists():
        return HeartbeatReading(None, f"任务库不存在: {db_path}")
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error as exc:
        return HeartbeatReading(None, f"任务库不可读（{db_path}）: {exc}")
    try:
        row = conn.execute(
            "SELECT last_heartbeat_at FROM service_heartbeats "
            "WHERE service_type = 'worker' AND pid = ? "
            "ORDER BY last_heartbeat_at DESC LIMIT 1",
            (pid,),
        ).fetchone()
    except sqlite3.Error as exc:
        return HeartbeatReading(None, f"读取 worker 心跳失败（{db_path}）: {exc}")
    finally:
        conn.close()
    if not row or not row[0]:
        return HeartbeatReading(None, None)  # 查询成功但没有心跳行
    try:
        last = datetime.fromisoformat(str(row[0]))
    except ValueError as exc:
        return HeartbeatReading(None, f"心跳时间格式非法: {row[0]!r}（{exc}）")
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)  # 库里存的是 naive UTC
    return HeartbeatReading(max(0.0, (datetime.now(UTC) - last).total_seconds()), None)


def read_worker_heartbeat_age(pid: int | None) -> float | None:
    """兼容接口：只返回心跳年龄（读失败/无行均 None；需要区分时用 ``_read_worker_heartbeat``）。"""
    return _read_worker_heartbeat(pid).age


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _memory_used_percent() -> float | None:
    if not sys.platform.startswith("win"):
        return None
    try:
        stat = _MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return None
        return float(stat.dwMemoryLoad)
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════
#  幂等锁（PID 心跳文件）
# ═══════════════════════════════════════════════════════════════

def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_lock() -> bool:
    """获取 supervisor 单实例锁：已有活跃实例则返回 False。

    锁文件记录 {pid, heartbeat_at}，supervisor 每轮刷新 heartbeat；
    启动时若 PID 存活且心跳新鲜（< 60s）则判定已有实例在运行。
    """
    if PID_FILE.exists():
        try:
            data = json.loads(PID_FILE.read_text(encoding="utf-8"))
            pid = int(data.get("pid", 0))
            last_hb = float(data.get("heartbeat_at", 0))
            if pid and _pid_alive(pid) and (time.time() - last_hb) < 60:
                print(f"supervisor 已在运行 (PID {pid})，本次退出")
                return False
        except Exception:
            pass
        # 陈旧锁：清理
        try:
            PID_FILE.unlink(missing_ok=True)
        except OSError:
            pass
    _write_pid_heartbeat()
    return True


def _write_pid_heartbeat() -> None:
    """原子写心跳：直接 write_text 会让第二个实例读到半截 JSON 并误判为陈旧锁。"""
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PID_FILE.with_name(f"{PID_FILE.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps({"pid": os.getpid(), "heartbeat_at": time.time()}),
        encoding="utf-8",
    )
    tmp.replace(PID_FILE)


# ═══════════════════════════════════════════════════════════════
#  状态落盘
# ═══════════════════════════════════════════════════════════════

def _service_status(svc: ManagedService) -> str:
    """根据进程与健康状态推导服务状态标签。"""
    if svc.proc is not None and svc.proc.poll() is None:
        if svc.age() < svc.grace:
            return "starting"
        return "ok" if svc.healthy() else "unhealthy"
    return "down"


def write_status_file(services: dict, alerts: list[str]) -> None:
    """原子写入服务状态 JSON，供命令行脚本兜底读取。

    worker 额外带 ``heartbeat_age_seconds``：进程假死时 status 会是 unhealthy，
    心跳年龄是判断「卡了多久」的直接证据（2026-11-02 事故复盘用）。
    """
    service_entries: dict[str, dict] = {}
    for name, svc in services.items():
        entry: dict = {
            "status": _service_status(svc),
            "pid": svc.proc.pid if svc.proc else None,
            "restarts": svc.restart_count,
            "last_exit_code": svc.last_exit_code,
            "last_exit_reason": svc.last_exit_reason,
        }
        if name == "worker":
            reading = svc.heartbeat_reading()  # 复用本轮的缓存，避免再读一次库
            entry["heartbeat_age_seconds"] = (
                round(reading.age, 1) if reading.age is not None else None
            )
            entry["heartbeat_read_error"] = reading.error
        service_entries[name] = entry
    payload = {
        "updated_at": datetime.now().isoformat(),
        "supervisor": {"pid": os.getpid()},
        "services": service_entries,
        "alerts": alerts,
    }
    tmp = STATUS_FILE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(STATUS_FILE)


# ═══════════════════════════════════════════════════════════════
#  主循环
# ═══════════════════════════════════════════════════════════════

def _check_resources(logger: logging.Logger) -> list[str]:
    """检查磁盘/内存占用，超阈值写入告警日志并返回告警文案。"""
    alerts: list[str] = []
    disks = [("项目盘", PROJECT_ROOT)]
    storage = _storage_root()
    if storage != PROJECT_ROOT and storage.exists():
        disks.append(("素材盘", storage))
    for label, path in disks:
        disk = _disk_used_percent(path)
        if disk >= DISK_ALERT_PERCENT:
            msg = f"{label} {path} 使用率 {disk:.1f}% 超过阈值 {DISK_ALERT_PERCENT:.0f}%"
            alerts.append(msg)
            logger.warning(f"[资源告警] {msg}")

    mem = _memory_used_percent()
    if mem is not None and mem >= MEMORY_ALERT_PERCENT:
        msg = f"内存使用率 {mem:.1f}% 超过阈值 {MEMORY_ALERT_PERCENT:.0f}%"
        alerts.append(msg)
        logger.warning(f"[资源告警] {msg}")
    return alerts


def _supervisor_logger() -> logging.Logger:
    """supervisor 自身日志：写到 logs/supervisor.log 并带轮转。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("supervisor")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "supervisor.log",
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())  # 前台也打印一份
    return logger


def run() -> None:
    """supervisor 主流程：启动服务、循环健康检查与自动拉起。"""
    logger = _supervisor_logger()

    def _on_stop(signum, _frame) -> None:
        raise SystemExit(0)

    # 收到终止信号也要走 finally 停子服务；否则直接退出会把 backend/worker 留成孤儿
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _on_stop)
        except (OSError, ValueError):
            pass

    if not acquire_lock():
        return

    services = {
        name: ManagedService(name, cfg, RotatingFileWriter(
            LOG_DIR / f"{name}.log", LOG_MAX_BYTES, LOG_BACKUP_COUNT
        ))
        for name, cfg in SERVICE_DEFS.items()
    }

    logger.info("supervisor 启动，开始守护 backend / frontend / worker ...")
    logger.info(f"后端监听 {BACKEND_HOST}:{BACKEND_PORT}")
    for warning in _CONFIG_WARNINGS:
        logger.warning(warning)
    logger.info(
        "worker 心跳过期阈值 %.0fs（WORKER_HEARTBEAT_STALE_SECONDS；心跳间隔 %.0fs）",
        WORKER_HEARTBEAT_STALE_SECONDS,
        HEARTBEAT_INTERVAL_SECONDS,
    )
    if BACKEND_HOST not in ("127.0.0.1", "localhost"):
        # 真机需要这个；但接口目前无鉴权，只适合可信局域网
        logger.warning(
            f"后端绑定 {BACKEND_HOST}：局域网内设备可访问，且读接口无鉴权（仅限可信网络）"
        )

    last_resource_check = 0.0
    resource_alerts: list[str] = []

    try:
        while True:
            time.sleep(CHECK_INTERVAL)

            for svc in services.values():
                if svc.proc is not None and svc.proc.poll() is None:
                    # 运行中：过了启动宽限期后做健康检查
                    if svc.age() >= svc.grace:
                        if svc.healthy():
                            svc.fail_streak = 0
                        else:
                            svc.fail_streak += 1
                            if svc.fail_streak >= MAX_FAIL_STREAK:
                                reason = "健康检查连续失败"
                                if svc.health_url is None:
                                    reading = svc.heartbeat_reading(refresh=True)
                                    reason = (
                                        f"worker 心跳 {reading.age:.0f}s 未更新"
                                        if reading.age is not None
                                        else "worker 启动后一直没有心跳"
                                    )
                                # 两处都写：supervisor.log 记录决策，worker.log 方便贴给用户看
                                logger.warning(
                                    "[强制重启] %s %s（连续 %s 次健康检查失败）",
                                    svc.name,
                                    reason,
                                    svc.fail_streak,
                                )
                                svc.log.write(
                                    f"[supervisor] {svc.name} {reason}，强制重启\n"
                                )
                                svc.stop()
                                svc.restart_count += 1
                                svc.last_exit_reason = reason
                                svc.restart_at = time.time() + 2
                    continue

                # 进程未运行
                if svc.restart_at is None and svc.proc is not None:
                    svc.handle_exit()  # 刚退出：记录原因 + 安排退避重启
                if svc.restart_at is None or time.time() >= svc.restart_at:
                    svc.start()
                    svc.restart_at = None

            # 资源告警检查（低频）
            if time.time() - last_resource_check >= RESOURCE_CHECK_INTERVAL:
                resource_alerts = _check_resources(logger)
                last_resource_check = time.time()

            # 刷新 PID 心跳 + 落盘状态
            _write_pid_heartbeat()
            write_status_file(services, resource_alerts)
    finally:
        logger.info("supervisor 停止，终止所有子服务 ...")
        for svc in services.values():
            svc.stop()
        try:
            PID_FILE.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    run()
