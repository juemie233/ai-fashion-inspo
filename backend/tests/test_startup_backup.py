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
