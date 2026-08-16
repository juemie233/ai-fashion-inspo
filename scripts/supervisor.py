"""服务守护进程：管理后端 / 前端 / worker 的启动、健康检查、崩溃自动拉起、日志轮转与资源告警。

用法:
    python scripts/supervisor.py

职责（对应 TODO「服务守护与监控」）：
- 启动并监控三个服务进程，任一异常退出后自动拉起并记录退出码与原因；
- 健康检查：后端/前端走 HTTP 探针，worker 走进程存活判断，连续失败强制重启；
- 日志轮转：捕获子进程 stdout/stderr，日志超限自动轮转（保留 N 份）；
- 资源告警：磁盘 / 内存占用超阈值时写入 supervisor 日志；
- 幂等：通过 PID 心跳文件避免重复启动多个 supervisor 实例；
- 状态落盘：每轮把各服务状态写入 logs/service_status.json，供命令行脚本兜底读取。
"""

import ctypes
import json
import logging
import logging.handlers
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

# ── 路径与端口（与 scripts/restart.sh、scripts/ensure-services.sh 约定一致）──
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
FRONTEND_DIR = PROJECT_ROOT / "web"
LOG_DIR = PROJECT_ROOT / "logs"
PID_FILE = LOG_DIR / "supervisor.pid"
STATUS_FILE = LOG_DIR / "service_status.json"

BACKEND_PORT = int(os.environ.get("BACKEND_PORT", "18888"))
FRONTEND_PORT = int(os.environ.get("FRONTEND_PORT", "17777"))

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

# ── 服务定义 ──
SERVICE_DEFS = {
    "backend": {
        "cmd": [
            sys.executable, "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1", "--port", str(BACKEND_PORT),
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
        "env_extra": {"FORCE_COLOR": "0"},  # 去掉 ANSI 颜色码，日志干净
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
        """轮转日志：移除最老备份，逐级后移，当前文件转为 .1。"""
        assert self._fh is not None
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
        self._open()

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

    def healthy(self) -> bool:
        """健康判定：无 HTTP 端口（worker）用进程存活；其余用 HTTP 探针。"""
        if self.health_url is None:
            return True
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


def _disk_used_percent() -> float:
    try:
        usage = shutil.disk_usage(str(PROJECT_ROOT))
        return usage.used / usage.total * 100
    except Exception:
        return 0.0


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
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(
        json.dumps({"pid": os.getpid(), "heartbeat_at": time.time()}),
        encoding="utf-8",
    )


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
    """原子写入服务状态 JSON，供命令行脚本兜底读取。"""
    payload = {
        "updated_at": datetime.now().isoformat(),
        "supervisor": {"pid": os.getpid()},
        "services": {
            name: {
                "status": _service_status(svc),
                "pid": svc.proc.pid if svc.proc else None,
                "restarts": svc.restart_count,
                "last_exit_code": svc.last_exit_code,
                "last_exit_reason": svc.last_exit_reason,
            }
            for name, svc in services.items()
        },
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
    disk = _disk_used_percent()
    if disk >= DISK_ALERT_PERCENT:
        msg = f"磁盘使用率 {disk:.1f}% 超过阈值 {DISK_ALERT_PERCENT:.0f}%"
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

    if not acquire_lock():
        return

    services = {
        name: ManagedService(name, cfg, RotatingFileWriter(
            LOG_DIR / f"{name}.log", LOG_MAX_BYTES, LOG_BACKUP_COUNT
        ))
        for name, cfg in SERVICE_DEFS.items()
    }

    logger.info("supervisor 启动，开始守护 backend / frontend / worker ...")

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
                                svc.log.write(
                                    f"[supervisor] {svc.name} 健康检查连续失败，强制重启\n"
                                )
                                svc.stop()
                                svc.restart_count += 1
                                svc.last_exit_reason = "健康检查连续失败"
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
