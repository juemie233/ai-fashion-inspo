"""采集专用 Chrome 生命周期管理。

自动拉起调试模式 Chrome、监控存活并在崩溃后自动重启，支持手动停止与空闲自动关闭。
独立于采集任务子进程，二者通过 CDP 端口协作；任务子进程是否活动由
``scraper_service._scraper_pids`` 与数据库中的 running/pending 任务共同判断。
"""

import asyncio
import logging
import subprocess
import threading
import time

from app.config import settings
from app.services import scraper_service

logger = logging.getLogger(__name__)


class ChromeManager:
    """管理采集专用 Chrome 的启动、停止、状态与存活监控。

    设计要点：
    - Chrome 会派生大量子进程，停止时必须级联杀进程树（``taskkill /F /T``）。
    - 若 Chrome 由外部（用户手动）启动，本管理器只报告状态，不负责关闭。
    """

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._monitor_thread: threading.Thread | None = None
        self._restart_count = 0
        self._stop_flag = threading.Event()
        self._lock = threading.Lock()

    # ---- 对外接口 ----

    def start(self) -> dict:
        """启动采集专用 Chrome（若已运行则直接返回当前状态）。"""
        with self._lock:
            ok, detail, is_chrome = self._check(settings.chrome_debug_port)
            if ok and is_chrome:
                return self._status("running", detail, self._proc.pid if self._proc else None)
            if ok and not is_chrome:
                return self._status("port_conflict", detail, None)

            # 端口空闲，启动 Chrome
            self._stop_flag.clear()
            self._restart_count = 0
            cmd = self._build_cmd()
            try:
                self._proc = subprocess.Popen(cmd)
            except Exception as e:
                logger.error(f"启动 Chrome 失败: {e}")
                return self._status("not_started", f"启动 Chrome 失败: {e}", None)

            # 轮询就绪（复用 scraper_service._check_cdp 探测 /json/version）
            deadline = time.time() + settings.chrome_startup_timeout
            while time.time() < deadline:
                ok, detail, is_chrome = self._check(settings.chrome_debug_port)
                if ok and is_chrome:
                    self._start_monitor()
                    return self._status("running", detail, self._proc.pid)
                if ok and not is_chrome:
                    return self._status("port_conflict", detail, self._proc.pid)
                time.sleep(0.5)

            return self._status(
                "not_started",
                f"Chrome 启动超时（{settings.chrome_startup_timeout}s），请检查路径配置",
                self._proc.pid,
            )

    def stop(self) -> dict:
        """停止由本服务启动的 Chrome（级联杀进程树）。"""
        with self._lock:
            self._stop_flag.set()
            if self._proc is None:
                return self._status("not_started", "Chrome 非本服务启动，无需停止", None)

            pid = self._proc.pid
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    timeout=10,
                )
            except Exception as e:
                logger.warning(f"停止 Chrome 失败: {e}")
            self._proc = None
            self._restart_count = 0
            return self._status("not_started", "Chrome 已停止", pid)

    def status(self) -> dict:
        """返回当前 Chrome 连接状态（running / not_started / port_conflict）。"""
        with self._lock:
            # 自管进程存活优先
            if self._proc is not None and self._proc.poll() is None:
                ok, detail, is_chrome = self._check(settings.chrome_debug_port)
                if ok and is_chrome:
                    return self._status("running", detail, self._proc.pid)

            # 无自管进程：探测端口，区分「未启动」「外部 Chrome」「端口冲突」
            ok, detail, is_chrome = self._check(settings.chrome_debug_port)
            if ok and is_chrome:
                return self._status("running", f"{detail}（外部启动）", None)
            if ok and not is_chrome:
                return self._status("port_conflict", detail, None)
            return self._status("not_started", "Chrome 未启动", None)

    # ---- 内部 ----

    @staticmethod
    def _check(port: int) -> tuple[bool, str, bool]:
        """复用 scraper_service._check_cdp 探测 CDP 端口。"""
        return scraper_service._check_cdp(port)

    @staticmethod
    def _build_cmd() -> list[str]:
        return [
            settings.chrome_executable,
            f"--remote-debugging-port={settings.chrome_debug_port}",
            f"--user-data-dir={settings.chrome_user_data_dir}",
        ]

    @staticmethod
    def _status(state: str, detail: str, pid: int | None) -> dict:
        return {"state": state, "detail": detail, "pid": pid}

    def _start_monitor(self) -> None:
        """启动后台监控线程（已存在则复用）。"""
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            return
        self._monitor_thread = threading.Thread(
            target=self._monitor, daemon=True, name="chrome-monitor"
        )
        self._monitor_thread.start()

    def _monitor(self) -> None:
        """周期检测 Chrome 存活；崩溃自动重启；空闲自动关闭。"""
        idle_since = time.time()
        while not self._stop_flag.is_set():
            time.sleep(2)
            proc = self._proc
            if proc is None:
                break

            # 进程退出检测
            if proc.poll() is not None:
                if self._stop_flag.is_set():
                    break
                # 端口仍由 Chrome 接管（launcher 委托给既有进程）→ 视为存活，重置句柄
                ok, _detail, is_chrome = self._check(settings.chrome_debug_port)
                if ok and is_chrome:
                    self._proc = None
                    break
                # 端口已无 Chrome：自动重启
                if self._restart_count < settings.chrome_auto_restart_limit:
                    self._restart_count += 1
                    logger.warning(
                        f"Chrome 异常退出，自动重启（{self._restart_count}/{settings.chrome_auto_restart_limit}）"
                    )
                    self._proc = subprocess.Popen(self._build_cmd())
                    continue
                logger.error("Chrome 重启次数已达上限，停止监控")
                self._proc = None
                break

            # 空闲自动关闭：无活动采集任务且持续超时。
            # 除进程内 _scraper_pids 外，再查 DB 中 running/pending 任务（跨进程权威），
            # 避免多 worker / API 重启后误判空闲而提前关闭 Chrome、打断采集。
            if settings.chrome_idle_timeout > 0:
                has_active = bool(scraper_service._scraper_pids)
                if not has_active:
                    try:
                        has_active = asyncio.run(scraper_service.has_active_scraper_tasks())
                    except Exception as e:
                        logger.warning(f"查询活动采集任务失败（按有活动处理，避免误关）: {e}")
                        has_active = True
                if has_active:
                    idle_since = time.time()
                elif time.time() - idle_since >= settings.chrome_idle_timeout:
                    logger.info("Chrome 空闲超时，自动关闭")
                    self.stop()
                    break


# 进程内单例
chrome_manager = ChromeManager()
