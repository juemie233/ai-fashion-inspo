"""备份服务边界分支：Git Bash 探测、非法时间戳降级、日志尾读取、状态汇总与常驻循环。

``test_startup_backup.py`` 已覆盖判定纯函数与 _spawn_backup；这里补：
- ``_resolve_bash`` 的 PATH/常见路径/找不到三种结果；
- 非法目录名（正则匹配但日期非法）在 latest_success 与 build_backup_status 中的降级；
- 日志尾读取的 OSError 降级、running 锁、history 排序截断；
- ``_startup_backup_loop`` 未配置目标直接返回、以及一轮循环后取消。
"""

import asyncio
import builtins
from datetime import datetime
from pathlib import Path

import pytest

from app.config import settings
from app.services import backup_service


def _make_backup(root: Path, stamp: str, success: bool = True) -> Path:
    d = root / stamp
    d.mkdir(parents=True, exist_ok=True)
    (d / "fashion_inspo.db").write_bytes(b"fake")
    if success:
        (d / "SUCCESS").write_text("ok", encoding="utf-8")
    return d


# ── _resolve_bash ──


def test_resolve_bash_prefers_path(monkeypatch):
    """PATH 里能解析到 bash 时优先用它（开发环境常态）。"""
    monkeypatch.setattr("shutil.which", lambda name: r"C:\Git\bin\bash.exe")

    assert backup_service._resolve_bash() == r"C:\Git\bin\bash.exe"


def test_resolve_bash_falls_back_to_known_candidates(monkeypatch, tmp_path):
    """PATH 无 bash 时回退到常见安装路径（服务运行场景）。"""
    fake_bash = tmp_path / "bash.exe"
    fake_bash.write_bytes(b"")
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(backup_service, "_BASH_CANDIDATES", (str(fake_bash),))

    assert backup_service._resolve_bash() == str(fake_bash)


def test_resolve_bash_returns_none_when_missing(monkeypatch):
    """PATH 与常见路径都找不到时返回 None，由调用方记录错误并跳过。"""
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(backup_service, "_BASH_CANDIDATES", ("/no/such/bash",))

    assert backup_service._resolve_bash() is None


# ── 非法时间戳降级 ──


def test_latest_success_ignores_invalid_date_with_success_marker(tmp_path):
    """目录名匹配时间戳正则但日期非法（如 13 月）时跳过，不抛异常。"""
    _make_backup(tmp_path, "2026-13-45_999999")

    assert backup_service.latest_success_backup_time(tmp_path) is None


def test_build_backup_status_reports_invalid_date_as_none(tmp_path):
    """history 里非法日期目录仍列出，但 time 为 None（前端不必解析崩溃）。"""
    _make_backup(tmp_path, "2026-08-20_030000")
    _make_backup(tmp_path, "2026-13-45_999999", success=False)

    status = backup_service.build_backup_status(tmp_path, log_path=tmp_path / "no.log")

    names = {item["name"]: item for item in status["history"]}
    assert names["2026-13-45_999999"]["time"] is None
    assert names["2026-13-45_999999"]["success"] is False
    assert status["latest_success_dir"] == "2026-08-20_030000"


# ── _read_log_tail 与 build_backup_status ──


def test_read_log_tail_returns_last_lines(tmp_path):
    log = tmp_path / "backup.log"
    log.write_text("l1\nl2\nl3\nl4\n", encoding="utf-8")

    assert backup_service._read_log_tail(log, max_lines=2) == ["l3", "l4"]


def test_read_log_tail_returns_empty_for_missing_file(tmp_path):
    assert backup_service._read_log_tail(tmp_path / "absent.log") == []


def test_read_log_tail_returns_empty_on_oserror(tmp_path, monkeypatch):
    """日志被占用/权限不足时降级为空列表，不能影响任务管理页其它字段。"""
    log = tmp_path / "backup.log"
    log.write_text("line\n", encoding="utf-8")
    real_open = builtins.open

    def _open_raises(file, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        if str(file) == str(log):
            raise OSError("文件被占用")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _open_raises)

    assert backup_service._read_log_tail(log) == []


def test_build_backup_status_summarizes_lock_history_and_log(tmp_path):
    """状态汇总：配置/开关、running 锁、成功时间、history 倒序截断、日志尾。"""
    _make_backup(tmp_path, "2026-08-20_030000")
    _make_backup(tmp_path, "2026-08-22_030000", success=False)
    _make_backup(tmp_path, "2026-08-25_030000")
    (tmp_path / ".backup.lock").mkdir()
    (tmp_path / "not-a-backup").mkdir()
    log = tmp_path / "backup.log"
    log.write_text("[1] 触发\n[2] 完成\n", encoding="utf-8")

    status = backup_service.build_backup_status(tmp_path, log_path=log, limit=2)

    assert status["configured"] is True
    assert status["target_path"] == str(tmp_path)
    assert status["running"] is True
    assert status["latest_success_dir"] == "2026-08-25_030000"
    assert status["keep_count"] == settings.backup_keep_count
    assert [item["name"] for item in status["history"]] == [
        "2026-08-25_030000",  # 倒序
        "2026-08-22_030000",
    ]
    assert status["history"][1]["success"] is False
    assert status["log_tail"] == ["[1] 触发", "[2] 完成"]


def test_build_backup_status_unconfigured(monkeypatch, tmp_path):
    """未配置目标路径时：configured=False、running=False、history/log 为空。"""
    monkeypatch.setattr(settings, "backup_target_path", "")

    status = backup_service.build_backup_status(None, log_path=tmp_path / "no.log")

    assert status["configured"] is False
    assert status["running"] is False
    assert status["target_path"] == ""
    assert status["history"] == []
    assert status["log_tail"] == []


# ── _startup_backup_loop ──


async def test_startup_loop_returns_immediately_without_target(monkeypatch):
    """未配置 BACKUP_TARGET_PATH：循环直接退出（不占常驻任务）。"""
    monkeypatch.setattr(settings, "backup_target_path", "")

    await asyncio.wait_for(backup_service._startup_backup_loop(), timeout=1)


async def test_startup_loop_runs_one_round_then_cancels(monkeypatch, tmp_path):
    """启动延迟为 0 时执行一轮补备，随后进入长睡眠；取消时正常退出。"""
    calls = {"n": 0}

    async def _fake_spawn(target):  # noqa: ANN001
        calls["n"] += 1
        return 0

    monkeypatch.setattr(settings, "backup_target_path", str(tmp_path))
    monkeypatch.setattr(settings, "backup_startup_delay_minutes", 0)
    monkeypatch.setattr(settings, "backup_tick_hours", 1)
    monkeypatch.setattr(settings, "backup_on_startup", True)
    monkeypatch.setattr(settings, "backup_min_interval_hours", 0)
    monkeypatch.setattr(backup_service, "_spawn_backup", _fake_spawn)

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(backup_service._startup_backup_loop(), timeout=0.5)

    assert calls["n"] == 1


async def test_startup_loop_swallows_spawn_exceptions(monkeypatch, tmp_path):
    """单轮执行抛异常只记日志，循环继续（下一次睡眠被取消而非崩溃）。"""
    calls = {"n": 0}

    async def _boom(target):  # noqa: ANN001
        calls["n"] += 1
        raise RuntimeError("备份脚本异常")

    monkeypatch.setattr(settings, "backup_target_path", str(tmp_path))
    monkeypatch.setattr(settings, "backup_startup_delay_minutes", 0)
    monkeypatch.setattr(settings, "backup_tick_hours", 1)
    monkeypatch.setattr(settings, "backup_on_startup", True)
    monkeypatch.setattr(settings, "backup_min_interval_hours", 0)
    monkeypatch.setattr(backup_service, "_spawn_backup", _boom)

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(backup_service._startup_backup_loop(), timeout=0.5)

    assert calls["n"] == 1


# ── 成功备份目录的判定边界 ──


def test_latest_success_ignores_plain_files_named_like_backup(tmp_path):
    """同名普通文件不是目录，不计为备份。"""
    (tmp_path / "2026-08-20_030000").write_text("not a dir", encoding="utf-8")

    assert backup_service.latest_success_backup_time(tmp_path) is None


def test_latest_success_returns_newest_with_any_timezone(tmp_path):
    """多份成功备份取最新；返回 naive datetime（与 backup_service 约定一致）。"""
    _make_backup(tmp_path, "2026-08-20_030000")
    _make_backup(tmp_path, "2026-08-25_120000")

    latest = backup_service.latest_success_backup_time(tmp_path)

    assert latest == datetime(2026, 8, 25, 12, 0, 0)
