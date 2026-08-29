"""启动后自动补备测试：判定逻辑 + 触发/跳过。

判定逻辑是纯函数，直接测；触发逻辑（_spawn_backup / loop）通过 mock
subprocess 验证，不真跑备份脚本。
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.services import backup_service


def _make_backup(root: Path, stamp: str, success: bool = True) -> Path:
    """在 root 下造一个时间戳命名的备份目录，可选写 SUCCESS 标记。"""
    d = root / stamp
    d.mkdir(parents=True)
    (d / "fashion_inspo.db").write_bytes(b"fake")
    if success:
        (d / "SUCCESS").write_text("ok", encoding="utf-8")
    return d


# ── latest_success_backup_time ──


def test_latest_success_none_when_empty(tmp_path):
    assert backup_service.latest_success_backup_time(tmp_path) is None


def test_latest_success_none_when_missing(tmp_path):
    # 目录不存在
    assert backup_service.latest_success_backup_time(tmp_path / "nope") is None


def test_latest_success_ignores_failed_and_garbage(tmp_path):
    # 失败备份（无 SUCCESS）与非时间戳目录不计
    _make_backup(tmp_path, "2026-08-20_030000", success=False)
    (tmp_path / "manual_note").mkdir()
    (tmp_path / "2026-08-99_999999").mkdir()  # 非法日期
    assert backup_service.latest_success_backup_time(tmp_path) is None


def test_latest_success_returns_newest(tmp_path):
    _make_backup(tmp_path, "2026-08-20_030000")
    _make_backup(tmp_path, "2026-08-25_030000")
    _make_backup(tmp_path, "2026-08-23_030000", success=False)  # 失败，忽略
    latest = backup_service.latest_success_backup_time(tmp_path)
    assert latest == datetime(2026, 8, 25, 3, 0, 0)


# ── should_run_startup_backup ──


def test_should_run_disabled(tmp_path):
    """开关关闭时一律不补备。"""
    now = datetime(2026, 8, 26, 12, 0, 0)
    assert (
        backup_service.should_run_startup_backup(
            tmp_path, now=now, min_interval_hours=20, backup_on_startup=False
        )
        is False
    )


def test_should_run_when_never_backed_up(tmp_path):
    """从未成功备份过 → 补一次。"""
    now = datetime(2026, 8, 26, 12, 0, 0)
    assert backup_service.should_run_startup_backup(
        tmp_path, now=now, min_interval_hours=20, backup_on_startup=True
    )


def test_should_skip_when_recent_backup(tmp_path):
    """距上次成功备份 <20h → 跳过。"""
    _make_backup(tmp_path, "2026-08-26_030000")  # 当天 03:00 已备份
    now = datetime(2026, 8, 26, 12, 0, 0)  # 9 小时后
    assert (
        backup_service.should_run_startup_backup(
            tmp_path, now=now, min_interval_hours=20, backup_on_startup=True
        )
        is False
    )


def test_should_run_when_stale_backup(tmp_path):
    """距上次成功备份 ≥20h → 补备（覆盖「当天已备但已过 20h」）。"""
    _make_backup(tmp_path, "2026-08-25_120000")  # 昨天中午备份
    now = datetime(2026, 8, 26, 12, 0, 0)  # 整 24 小时后
    assert backup_service.should_run_startup_backup(
        tmp_path, now=now, min_interval_hours=20, backup_on_startup=True
    )


def test_should_run_ignores_failed_backup(tmp_path):
    """只有失败备份（无 SUCCESS）时视为从未成功 → 补备。"""
    _make_backup(tmp_path, "2026-08-26_030000", success=False)
    now = datetime(2026, 8, 26, 12, 0, 0)
    assert backup_service.should_run_startup_backup(
        tmp_path, now=now, min_interval_hours=20, backup_on_startup=True
    )


# ── build_backup_status（任务管理页展示用只读状态）──


def _make_log(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_build_backup_status_basic(tmp_path):
    """历史按时间倒序、成功标记正确、最近成功取最新一份。"""
    _make_backup(tmp_path, "2026-08-20_030000")
    _make_backup(tmp_path, "2026-08-25_030000", success=False)
    _make_backup(tmp_path, "2026-08-26_030000")
    (tmp_path / "manual_note").mkdir()  # 非时间戳目录忽略
    log = _make_log(tmp_path / "logs" / "backup.log", "line1\nline2\n")

    st = backup_service.build_backup_status(target_root=tmp_path, log_path=log)

    assert st["configured"] is True
    assert st["target_path"] == str(tmp_path)
    assert st["running"] is False
    assert st["latest_success_dir"] == "2026-08-26_030000"
    assert st["latest_success_at"] == "2026-08-26T03:00:00"
    # 倒序：最新在前；失败备份也列出但 success=False
    names = [h["name"] for h in st["history"]]
    assert names == ["2026-08-26_030000", "2026-08-25_030000", "2026-08-20_030000"]
    assert st["history"][1]["success"] is False
    assert st["history"][2]["success"] is True
    assert st["log_tail"] == ["line1", "line2"]


def test_build_backup_status_history_capped_at_five(tmp_path):
    """历史默认至多 5 条（最新在前）——任务管理页卡片不无限增长。"""
    # 造 8 份历史备份（时间倒序取前 5）
    for day in range(1, 9):
        _make_backup(tmp_path, f"2026-08-{day:02d}_030000")

    st = backup_service.build_backup_status(target_root=tmp_path)
    names = [h["name"] for h in st["history"]]
    assert len(names) == 5
    assert names[0] == "2026-08-08_030000"  # 最新在前
    assert "2026-08-01_030000" not in names  # 最旧的被截断


def test_build_backup_status_running_lock(tmp_path):
    """目标根下存在 .backup.lock 并发锁 → running=True（双通道可见）。"""
    _make_backup(tmp_path, "2026-08-26_030000")
    (tmp_path / ".backup.lock").mkdir()
    st = backup_service.build_backup_status(
        target_root=tmp_path, log_path=tmp_path / "logs" / "backup.log"
    )
    assert st["running"] is True


def test_build_backup_status_not_configured(tmp_path, monkeypatch):
    """未配置目标目录（且配置为空）→ configured=False 空状态。"""
    monkeypatch.setattr(backup_service.settings, "backup_target_path", "")
    st = backup_service.build_backup_status(
        target_root=None, log_path=tmp_path / "nope.log"
    )
    assert st["configured"] is False
    assert st["target_path"] == ""
    assert st["latest_success_at"] is None
    assert st["history"] == []
    assert st["log_tail"] == []  # 日志文件不存在


# ── 触发逻辑（_spawn_backup 调脚本，loop 按判定触发）──


@pytest.mark.asyncio
async def test_spawn_backup_calls_script(monkeypatch, tmp_path):
    """_spawn_backup 用正确参数调用 backup_data.sh，并把日志写到 backup.log。"""
    captured = {}

    class _FakeProc:
        def __init__(self):
            self.returncode = 0

        async def wait(self):
            return 0

    async def _fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr(
        backup_service.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec
    )

    rc = await backup_service._spawn_backup("E:/fashion-inspo-backups")
    assert rc == 0
    # 命令形如 <bash> <scripts/backup_data.sh> E:/fashion-inspo-backups；
    # bash 可能是 PATH 中的 "bash"，也可能是探测到的绝对路径（服务环境）
    assert Path(captured["args"][0]).name.lower() in ("bash", "bash.exe")
    assert str(captured["args"][1]).endswith("backup_data.sh")
    assert captured["args"][2] == "E:/fashion-inspo-backups"
    # stderr 合并到 stdout；cwd 为项目根（与仓库目录名无关，CI 上目录名
    # 可能是 fashion-inspo 或 ai-fashion-inspo，直接对比 PROJECT_ROOT）
    assert captured["kwargs"]["stderr"] == backup_service.asyncio.subprocess.STDOUT
    assert Path(captured["kwargs"]["cwd"]) == backup_service.PROJECT_ROOT


@pytest.mark.asyncio
async def test_spawn_backup_returns_minus1_without_bash(monkeypatch, tmp_path):
    """PATH 与常见安装路径都没有 bash 时，_spawn_backup 返回 -1 且不执行。"""
    async def _unexpected(*args, **kwargs):
        raise AssertionError("不应执行 create_subprocess_exec")

    monkeypatch.setattr(backup_service, "_resolve_bash", lambda: None)
    monkeypatch.setattr(
        backup_service.asyncio, "create_subprocess_exec", _unexpected
    )
    rc = await backup_service._spawn_backup("E:/fashion-inspo-backups")
    assert rc == -1
