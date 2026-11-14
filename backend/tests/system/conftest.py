"""L2 系统测试环境：临时 DB/存储 + 真 uvicorn 子进程 + 上游 HTTP 桩。

设计要点（见 `docs/overview/测试体系与系统测试方案-2026-11-14.md`）：

- **只碰临时目录**：`DATABASE_URL` / `STORAGE_ROOT` 指向 pytest 临时根，
  启动前自检，绝不读写真实库与真实存储盘；
- **端口隔离**：动态取空闲端口，不碰 18888/17777/18889；
- **黑盒断言**：测试进程不 import app（避免与子进程共享模块状态），
  只经 HTTP API 与直连临时 SQLite 验证；
- **子进程清理**：Windows 用 `taskkill /F /T` 级联，失败用例也保证清理。

父级 `tests/conftest.py` 的 autouse fixture 会启动进程内 TestClient / 清库，
对系统测试既慢又无意义，这里用同名 fixture 覆盖成 no-op。
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent

# 模型名与 `settings.ollama_vision_model` 默认值一致（桩里不读配置，保持字面量）
_VISION_MODEL = "qwen3-vl:8b-instruct"

_ANALYSIS_JSON = json.dumps(
    {
        "style": ["法式"],
        "items": [{"type": "米色风衣", "color": "米色", "features": ["长款", "双排扣"]}],
        "fit": ["直筒"],
        "design_detail": ["双排扣"],
        "material": ["棉"],
        "attributes": ["全身", "站姿"],
        "dominant_colors": ["#D9C7A7 米色"],
        "atmosphere": ["优雅"],
        "expression": ["无表情"],
        "leg_posture": ["并拢"],
    },
    ensure_ascii=False,
)


# ── 覆盖父级 autouse fixture（系统测试不需要进程内 app / 清库）──


@pytest.fixture(autouse=True)
def clean_state():  # noqa: PT004 —— 有意覆盖父级同名 fixture
    """覆盖 tests/conftest.py 的「每用例清库」：系统测试有独立临时库。"""
    yield


@pytest.fixture(scope="session", autouse=True)
def _mock_clip_image_encoding():  # noqa: PT004
    """覆盖父级的 CLIP mock：系统测试不在测试进程里跑向量编码。"""
    yield


@pytest.fixture(autouse=True)
def guard_f2_merge_off_real_roots():  # noqa: PT004
    """覆盖父级的真实下载目录守卫（系统测试不调用 f2 合并）。"""
    yield


# ── 临时环境 ──


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def system_root(tmp_path_factory) -> Path:
    """系统测试的临时根（storage/db/logs 全在这里）。"""
    root = tmp_path_factory.mktemp("system-test")
    (root / "storage").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    return root


# ── 上游桩：Ollama（后续分析链路用；本阶段只保证后端指向桩而不指向本机 11434）──


class _OllamaStubHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):  # 静默访问日志
        return

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/tags"):
            self._send_json({"models": [{"name": _VISION_MODEL, "size": 1}]})
        elif self.path.startswith("/api/version"):
            self._send_json({"version": "stub"})
        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)  # 读完请求体（桩不解析内容）
        if self.path.startswith("/api/chat"):
            self._send_json(
                {"model": _VISION_MODEL, "message": {"role": "assistant", "content": _ANALYSIS_JSON}, "done": True}
            )
        elif self.path.startswith("/api/embed"):
            # 新接口批量形式；维度与 settings.lancedb_text_dim 默认一致（1024）
            self._send_json({"embeddings": [[0.0] * 1024]})
        elif self.path.startswith("/api/embeddings"):
            # 老接口单条形式
            self._send_json({"embedding": [0.0] * 1024})
        else:
            self._send_json({"error": "not found"}, status=404)


@pytest.fixture(scope="session")
def fake_ollama():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OllamaStubHandler)
    thread = threading.Thread(target=server.serve_forever, name="fake-ollama", daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# ── 后端服务子进程 ──


def _terminate_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            timeout=30,
            check=False,
        )
    else:
        proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


def _wait_health(base_url: str, *, timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=2.0) as client:
                response = client.get(f"{base_url}/api/health")
            if response.status_code == 200:
                return
        except Exception as exc:  # 启动期连接拒绝等，继续等
            last_error = exc
        time.sleep(0.4)
    raise RuntimeError(f"后端未在 {timeout}s 内就绪：{last_error}")


@pytest.fixture(scope="session")
def server_env(system_root: Path, fake_ollama: str) -> dict:
    """后端/worker 子进程共用的环境变量（临时 DB/存储 + 上游桩）。"""
    db_path = system_root / "db.sqlite"
    storage_root = system_root / "storage"
    env = os.environ.copy()
    # 注意：父级 tests/conftest.py 在导入时把 IMAGES_DIR/THUMBNAILS_DIR/... 都设成了
    # 它自己的临时目录；这里必须**逐项覆盖**，否则上传会写到父级临时存储而不是
    # 本系统测试的 system_root，隔离断言就会失真。
    env.update(
        {
            "DATABASE_URL": f"sqlite+aiosqlite:///{db_path.as_posix()}",
            "STORAGE_ROOT": str(storage_root),
            "IMAGES_DIR": str(storage_root / "images"),
            "THUMBNAILS_DIR": str(storage_root / "thumbnails"),
            "VIDEOS_DIR": str(storage_root / "videos"),
            "TRASH_DIR": str(storage_root / "trash"),
            "KEYFRAMES_DIR": str(storage_root / "keyframes"),
            "PERSON_PHOTOS_DIR": str(storage_root / "person_photos"),
            "PERSON_THUMBNAILS_DIR": str(storage_root / "person_thumbnails"),
            "LANCEDB_DIR": str(storage_root / "lancedb"),
            "BACKUP_ON_STARTUP": "false",
            "LOG_LEVEL": "WARNING",
            "DEBUG": "false",
            "API_KEY": "",
            "OLLAMA_BASE_URL": fake_ollama,
            # ComfyUI 指到不可用端口：确保本阶段不会有任何真实出图请求
            "COMFYUI_BASE_URL": "http://127.0.0.1:1",
            "SEEDREAM_DAILY_BUDGET_CNY": "0",
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )

    # 自检：数据库必须在临时根下，避免误连真实库
    assert db_path.resolve().is_relative_to(system_root.resolve())
    real_db = BACKEND_DIR / "fashion_inspo.db"
    assert "fashion_inspo.db" not in env["DATABASE_URL"], f"禁止使用真实库：{real_db}"
    return env


@pytest.fixture(scope="session")
def backend_server(system_root: Path, server_env: dict):
    """启动真 uvicorn（临时端口），yield base_url，退出时级联清理。"""
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    log_path = system_root / "logs" / "uvicorn.log"
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

    with open(log_path, "ab") as log_file:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
                "--no-access-log",
            ],
            cwd=str(BACKEND_DIR),
            env=server_env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
        try:
            _wait_health(base_url)
            yield base_url
        finally:
            _terminate_process(proc)

    # 失败排查线索：日志与临时根路径
    print(f"\n[system-test] temp root: {system_root}")
    print(f"[system-test] uvicorn log: {log_path}")


def _wait_worker_heartbeat(db_path: Path, *, timeout: float = 90.0) -> None:
    """等 worker 写第一条心跳（证明它已连上同一临时库并开始轮询）。"""
    import sqlite3

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT 1 FROM service_heartbeats WHERE service_type = 'worker' LIMIT 1"
                ).fetchone()
            if row:
                return
        except sqlite3.Error:
            pass
        time.sleep(0.3)
    raise RuntimeError(f"worker 未在 {timeout}s 内上报心跳")


@pytest.fixture(scope="session")
def worker_process(system_root: Path, server_env: dict, backend_server: str):
    """起真 worker 子进程（与 API 共用临时 DB/存储与上游桩）。

    依赖 `backend_server`：必须先由 API 进程跑完 Alembic 迁移，worker 再
    `create_all` 兜底，避免 worker 先建表导致迁移报「table already exists」。
    """
    log_path = system_root / "logs" / "worker.log"
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

    with open(log_path, "ab") as log_file:
        proc = subprocess.Popen(
            [sys.executable, "-m", "app.worker"],
            cwd=str(BACKEND_DIR),
            env=server_env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
        try:
            _wait_worker_heartbeat(system_root / "db.sqlite")
            yield proc
        finally:
            _terminate_process(proc)

    print(f"\n[system-test] worker log: {log_path}")


@pytest.fixture()
def api(backend_server: str):
    """指向系统测试后端实例的 HTTP 客户端。"""
    with httpx.Client(base_url=backend_server, timeout=60.0) as client:
        yield client
