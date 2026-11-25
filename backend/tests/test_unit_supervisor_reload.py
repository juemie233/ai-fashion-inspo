"""supervisor 开发热更（2026-11-25 新增）。

覆盖热更的纯逻辑与一轮真实轮询，**不启动任何服务**：
- 指纹采集只收 `.py`（递归）与显式文件，忽略 `__pycache__`；
- 差异计算覆盖新增 / 修改 / 删除，日志文案相对 backend 目录并截断；
- 待重启集合 take 后清空（避免下一轮白重启一次）；
- 开关：默认关，`--watch` 或环境变量 `SUPERVISOR_WATCH=1` 开；
- `run_watch_loop` 真实跑一轮：改文件 → 登记 backend/worker 并 set 唤醒事件。
"""

import importlib.util
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SUPERVISOR_PATH = REPO_ROOT / "scripts" / "ops" / "supervisor.py"


@pytest.fixture(scope="module")
def supervisor():
    """按文件路径加载 supervisor 模块（不执行 run()）。"""
    spec = importlib.util.spec_from_file_location("ops_supervisor_watch_under_test", SUPERVISOR_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, text: str = "x = 1\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_watch_snapshot_only_collects_py(supervisor, tmp_path):
    """目录递归只收 .py；非代码文件与 __pycache__ 不进指纹。"""
    app_dir = tmp_path / "app"
    _write(app_dir / "main.py")
    _write(app_dir / "services" / "f2" / "collects.py")
    _write(app_dir / "notes.md")
    _write(app_dir / "__pycache__" / "junk.py")

    snapshot = supervisor.watch_snapshot([app_dir])

    names = sorted(Path(p).name for p in snapshot)
    assert names == ["collects.py", "main.py"]


def test_watch_snapshot_includes_explicit_file(supervisor, tmp_path):
    """显式给出的 .env 也要监控（改配置同样需要重启才生效）。"""
    env = tmp_path / ".env"
    _write(env, "PORT=18888\n")

    assert str(env) in supervisor.watch_snapshot([env])


def test_watch_snapshot_skips_missing_paths(supervisor, tmp_path):
    """不存在的路径直接跳过，不抛异常。"""
    assert supervisor.watch_snapshot([tmp_path / "nope"]) == {}


def test_changed_paths_detects_new_modified_deleted(supervisor):
    old = {"a.py": (1, 10), "b.py": (2, 20), "c.py": (3, 30)}
    new = {"a.py": (1, 10), "b.py": (9, 20), "d.py": (4, 40)}

    changed = supervisor.changed_paths(old, new)

    assert [Path(p).name for p in changed] == ["b.py", "c.py", "d.py"]


def test_changed_paths_identical_is_empty(supervisor):
    snap = {"a.py": (1, 10)}
    assert supervisor.changed_paths(snap, dict(snap)) == []


def test_describe_changes_truncates_and_relativizes(supervisor):
    backend_dir = supervisor.BACKEND_DIR
    one = str(backend_dir / "app" / "main.py")
    assert supervisor.describe_changes([one]) == str(Path("app") / "main.py")

    many = [str(backend_dir / "app" / f"m{i}.py") for i in range(5)]
    text = supervisor.describe_changes(many, limit=2)
    assert "等 5 个文件" in text
    assert text.count("、") == 1  # 只列 2 个


def test_reload_registry_take_clears(supervisor):
    """take 之后必须清空：否则主循环会拿着旧集合再重启一轮。"""
    registry = supervisor.ReloadRegistry()

    assert registry.pending() is False
    registry.request(("backend", "worker"))
    assert registry.pending() is True

    assert registry.take() == {"backend", "worker"}
    assert registry.pending() is False
    assert registry.take() == set()


def test_watch_disabled_by_default(supervisor, monkeypatch):
    monkeypatch.delenv(supervisor.WATCH_ENV, raising=False)
    args = supervisor.build_arg_parser().parse_args([])
    assert supervisor.watch_enabled(args) is False


def test_watch_enabled_by_flag(supervisor, monkeypatch):
    monkeypatch.delenv(supervisor.WATCH_ENV, raising=False)
    args = supervisor.build_arg_parser().parse_args(["--watch"])
    assert supervisor.watch_enabled(args) is True


def test_watch_enabled_by_env(supervisor, monkeypatch):
    """环境变量开启便于 restart.sh 直接透传，不必改脚本参数。"""
    for value in ("1", "true", "YES", "on"):
        monkeypatch.setenv(supervisor.WATCH_ENV, value)
        args = supervisor.build_arg_parser().parse_args([])
        assert supervisor.watch_enabled(args) is True
    monkeypatch.setenv(supervisor.WATCH_ENV, "0")
    assert supervisor.watch_enabled(supervisor.build_arg_parser().parse_args([])) is False


def test_watch_interval_default_and_override(supervisor):
    parser = supervisor.build_arg_parser()
    assert parser.parse_args([]).watch_interval == supervisor.WATCH_DEFAULT_INTERVAL
    assert parser.parse_args(["--watch-interval", "0.2"]).watch_interval == 0.2


def test_watch_paths_include_app_dir(supervisor):
    paths = supervisor.watch_paths()
    assert paths and paths[0] == supervisor.BACKEND_DIR / "app"


def test_run_watch_loop_requests_restart_on_change(supervisor, tmp_path):
    """真实跑一轮轮询：改文件 → 登记 backend/worker 并唤醒主循环。"""
    app_dir = tmp_path / "app"
    _write(app_dir / "main.py")

    registry = supervisor.ReloadRegistry()
    wake = threading.Event()
    stop = threading.Event()
    logs: list[str] = []

    thread = threading.Thread(
        target=supervisor.run_watch_loop,
        args=([app_dir], 0.05, registry, wake, stop, logs.append),
        daemon=True,
    )
    thread.start()
    try:
        # 等首轮快照建立（日志里出现「热更已开启」）
        deadline = time.time() + 5
        while not logs and time.time() < deadline:
            time.sleep(0.02)
        assert logs, "watcher 没启动"

        _write(app_dir / "main.py", "x = 2\n")
        deadline = time.time() + 5
        while not wake.is_set() and time.time() < deadline:
            time.sleep(0.02)
        assert wake.is_set(), "文件变更后没有唤醒主循环"
        assert registry.take() == {"backend", "worker"}
    finally:
        stop.set()
        thread.join(timeout=3)
    assert any("重启" in line for line in logs)
