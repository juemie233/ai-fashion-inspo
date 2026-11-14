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


class _OllamaStub:
    """Ollama 桩的可控状态（故障注入用）。

    - ``ok``：正常返回分析 JSON；
    - ``hang``：请求挂起 ``hang_seconds`` 秒（模拟上游卡死，用来在任务 running 时杀 worker）；
    - ``http500``：立即返回 500（模拟上游故障，验证任务不挂死、有可读错误）。
    """

    def __init__(self) -> None:
        self.mode = "ok"
        self.hang_seconds = 120.0
        self.base_url = ""


class _OllamaStubHandler(BaseHTTPRequestHandler):
    stub: _OllamaStub

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
            if self.stub.mode == "http500":
                self._send_json(
                    {"error": {"code": "server_error", "message": "桩模拟上游 500"}}, status=500
                )
                return
            if self.stub.mode == "hang":
                time.sleep(self.stub.hang_seconds)
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
def fake_ollama() -> _OllamaStub:
    stub = _OllamaStub()
    _OllamaStubHandler.stub = stub
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OllamaStubHandler)
    stub.base_url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, name="fake-ollama", daemon=True)
    thread.start()
    try:
        yield stub
    finally:
        stub.mode = "ok"
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# ── 上游桩：ComfyUI（出图系统测试用：提交 → 轮询 → 取图全走 HTTP）──


class _ComfyStub:
    def __init__(self) -> None:
        self.base_url = ""
        self.prompt_count = 0
        self.image_seq = 0


class _ComfyStubHandler(BaseHTTPRequestHandler):
    stub: _ComfyStub

    def log_message(self, *_args):
        return

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _image_bytes(self) -> bytes:
        """每次 /view 返回**不同内容**的图。

        素材入库有「内容哈希全局去重」：桩若每次都返回同一张图，确认入库时第二张
        会被判「已存在（内容重复）」——系统测试要验证的是入库条数，所以每张必须不同。
        """
        import io

        from PIL import Image

        self.stub.image_seq += 1
        color = (120 + self.stub.image_seq % 100, 90, 160)
        buffer = io.BytesIO()
        Image.new("RGB", (64, 64), color).save(buffer, format="PNG")
        return buffer.getvalue()

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/system_stats"):
            self._send_json(
                {
                    "system": {},
                    "devices": [
                        {"vram_total": 24 * 2**30, "vram_free": 20 * 2**30, "name": "stub-gpu"}
                    ],
                }
            )
        elif self.path.startswith("/history/"):
            prompt_id = self.path.rsplit("/", 1)[-1]
            self._send_json(
                {
                    prompt_id: {
                        "status": {"status_str": "success", "completed": True},
                        "outputs": {
                            "9": {
                                "images": [
                                    {
                                        "filename": "stub_out.png",
                                        "subfolder": "",
                                        "type": "output",
                                    }
                                ]
                            }
                        },
                    }
                }
            )
        elif self.path.startswith("/view"):
            data = self._image_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        if self.path.startswith("/prompt"):
            self.stub.prompt_count += 1
            self._send_json({"prompt_id": f"stub-{self.stub.prompt_count}"})
        elif self.path.startswith("/upload/image"):
            self._send_json({"name": "uploaded.png"})
        else:
            self._send_json({"error": "not found"}, status=404)


@pytest.fixture(scope="session")
def fake_comfyui() -> _ComfyStub:
    stub = _ComfyStub()
    _ComfyStubHandler.stub = stub
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ComfyStubHandler)
    stub.base_url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, name="fake-comfyui", daemon=True)
    thread.start()
    try:
        yield stub
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
def server_env(
    system_root: Path, fake_ollama: _OllamaStub, fake_comfyui: _ComfyStub
) -> dict:
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
            "OLLAMA_BASE_URL": fake_ollama.base_url,
            # 系统测试专用：心跳更快、stale 阈值更短，便于验证故障恢复
            "WORKER_HEARTBEAT_INTERVAL_SECONDS": "0.5",
            "WORKER_STALE_HEARTBEAT_THRESHOLD_SECONDS": "3",
            "COMFYUI_BASE_URL": fake_comfyui.base_url,
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


def _worker_heartbeat_ts(db_path: Path) -> str | None:
    """读 worker 最新心跳时间戳（没有则 None）。"""
    import sqlite3

    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT MAX(last_heartbeat_at) FROM service_heartbeats "
                "WHERE service_type = 'worker'"
            ).fetchone()
        return str(row[0]) if row and row[0] else None
    except sqlite3.Error:
        return None


def _wait_worker_heartbeat(
    db_path: Path, *, previous: str | None = None, timeout: float = 90.0
) -> str:
    """等 worker 写一条**新的**心跳（证明本次启动的进程已连上临时库并开始轮询）。

    不能只判断「存在 worker 心跳行」：重启/复用时旧行还在，会误判为就绪。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = _worker_heartbeat_ts(db_path)
        if current and current != previous:
            return current
        time.sleep(0.3)
    raise RuntimeError(f"worker 未在 {timeout}s 内上报新心跳（previous={previous}）")


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

    previous = _worker_heartbeat_ts(system_root / "db.sqlite")
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
            _wait_worker_heartbeat(system_root / "db.sqlite", previous=previous)
            yield proc
        finally:
            _terminate_process(proc)

    print(f"\n[system-test] worker log: {log_path}")


class _WorkerController:
    """可反复启动/强杀的 worker 控制器（故障注入用）。

    与 session 级 `worker_process` 的区别：本控制器由用例显式 start/kill/stop，
    用来验证「worker 崩溃 → 重启 → stale running 被重置并重跑」。
    """

    def __init__(self, env: dict, system_root: Path):
        self._env = env
        self._system_root = system_root
        self._proc: subprocess.Popen | None = None
        self.log_path = system_root / "logs" / "worker-faults.log"

    def start(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        previous = _worker_heartbeat_ts(self._system_root / "db.sqlite")
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        with open(self.log_path, "ab") as log_file:
            self._proc = subprocess.Popen(
                [sys.executable, "-m", "app.worker"],
                cwd=str(BACKEND_DIR),
                env=self._env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
        _wait_worker_heartbeat(
            self._system_root / "db.sqlite", previous=previous, timeout=90.0
        )

    def kill(self) -> None:
        """强杀（模拟进程崩溃/断电）：taskkill /F /T，等价于真实故障。"""
        if self._proc is not None:
            _terminate_process(self._proc)
            self._proc = None

    def stop(self) -> None:
        self.kill()

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None


@pytest.fixture()
def worker_ctl(system_root: Path, server_env: dict, backend_server: str):
    """可启停的 worker（依赖后端先完成迁移）；用例结束自动清理。"""
    controller = _WorkerController(server_env, system_root)
    try:
        yield controller
    finally:
        controller.stop()
        print(f"[system-test] worker-faults log: {controller.log_path}")


@pytest.fixture()
def api(backend_server: str):
    """指向系统测试后端实例的 HTTP 客户端。"""
    with httpx.Client(base_url=backend_server, timeout=60.0) as client:
        yield client
