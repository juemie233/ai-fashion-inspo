"""storage_audit_service 单测（feat/rust-media-worker 分支新增的 Rust sidecar 调用）。

覆盖：
- ``find_storage_audit_binary``：未配置 / 配置路径不存在 → None；同目录
  ``storage_audit(.exe)`` 命中，且扩展名优先顺序、连字符变体按源码约定；
- ``run_storage_audit``：manifest 入参、CLI 参数、临时目录落在 storage_root
  下且结束后清理；返回码非 0 / 报告缺失或损坏 → None；超时 → kill + wait
  后返回 None；
- ``config`` 新增 ``media_worker_*`` 配置：默认值与 Field 越界拒绝。

全部 monkeypatch ``asyncio.create_subprocess_exec`` / ``asyncio.wait_for``，
不依赖真实 Rust 二进制、不触网、不读真实存储。
"""

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings, settings
from app.services import storage_audit_service


class _FakeAuditProcess:
    """假 storage-audit 进程：communicate 时按需写报告，并记录 kill/wait 调用。"""

    def __init__(self, *, report=None, raw_report=None, returncode=0, stderr=b""):
        self.report = report
        self.raw_report = raw_report
        self.report_path = None
        self.returncode = returncode
        self.stderr = stderr
        self.killed = False
        self.waited = False

    async def communicate(self):
        if self.report_path is not None:
            if self.raw_report is not None:
                self.report_path.write_text(self.raw_report, encoding="utf-8")
            elif self.report is not None:
                self.report_path.write_text(
                    json.dumps(self.report, ensure_ascii=False), encoding="utf-8"
                )
        return b"", self.stderr

    def kill(self):
        self.killed = True

    async def wait(self):
        self.waited = True
        return self.returncode


def _install_fake_exec(monkeypatch, process):
    """替换 create_subprocess_exec：记录 argv/manifest/temp 目录，返回假进程。"""
    record: dict = {}

    async def fake_exec(*argv, **kwargs):
        record["argv"] = argv
        record["kwargs"] = kwargs
        # manifest 在 create_subprocess_exec 之前已写入临时目录，趁 run 未返回先读
        record["manifest"] = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        record["temp_dir"] = Path(argv[2]).parent
        process.report_path = Path(argv[argv.index("--output") + 1])
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return record


@pytest.fixture
def audit_env(tmp_path, monkeypatch):
    """把 storage_root 指到 tmp_path，造 media-worker 与其同目录的 storage_audit。"""
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    worker = tmp_path / "media-worker.exe"
    worker.write_bytes(b"stub")
    audit = tmp_path / "storage_audit.exe"
    audit.write_bytes(b"stub")
    monkeypatch.setattr(settings, "storage_root", storage_root)
    monkeypatch.setattr(settings, "media_worker_bin", str(worker))
    return {"root": storage_root, "binary": audit, "worker": worker}


# ── find_storage_audit_binary：MEDIA_WORKER_BIN 同目录约定 ──


def test_find_binary_none_when_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "media_worker_bin", "")
    assert storage_audit_service.find_storage_audit_binary() is None


def test_find_binary_none_when_blank(monkeypatch):
    monkeypatch.setattr(settings, "media_worker_bin", "   ")
    assert storage_audit_service.find_storage_audit_binary() is None


def test_find_binary_none_when_configured_worker_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_worker_bin", str(tmp_path / "media-worker.exe"))
    assert storage_audit_service.find_storage_audit_binary() is None


def test_find_binary_finds_sibling_exe(tmp_path, monkeypatch):
    audit = tmp_path / "storage_audit.exe"
    audit.write_bytes(b"stub")
    monkeypatch.setattr(settings, "media_worker_bin", str(tmp_path / "media-worker.exe"))
    assert storage_audit_service.find_storage_audit_binary() == audit


def test_find_binary_prefers_exe_over_no_suffix(tmp_path, monkeypatch):
    """源码候选顺序 .exe 在前：两者都在时命中 storage_audit.exe。"""
    (tmp_path / "storage_audit.exe").write_bytes(b"stub")
    (tmp_path / "storage_audit").write_bytes(b"stub")
    monkeypatch.setattr(settings, "media_worker_bin", str(tmp_path / "media-worker.exe"))
    assert (
        storage_audit_service.find_storage_audit_binary()
        == tmp_path / "storage_audit.exe"
    )


def test_find_binary_falls_back_to_no_suffix(tmp_path, monkeypatch):
    audit = tmp_path / "storage_audit"
    audit.write_bytes(b"stub")
    monkeypatch.setattr(settings, "media_worker_bin", str(tmp_path / "media-worker.exe"))
    assert storage_audit_service.find_storage_audit_binary() == audit


def test_find_binary_supports_hyphen_variant(tmp_path, monkeypatch):
    """Rust 包名连字符形态 storage-audit 也能命中（候选顺序最后）。"""
    audit = tmp_path / "storage-audit.exe"
    audit.write_bytes(b"stub")
    monkeypatch.setattr(settings, "media_worker_bin", str(tmp_path / "media-worker.exe"))
    assert storage_audit_service.find_storage_audit_binary() == audit


# ── run_storage_audit：成功路径的参数/清理契约 ──


async def test_run_storage_audit_success_passes_manifest_and_cleans_tempdir(
    audit_env, monkeypatch
):
    report = {"missing": ["images/2026-11/a.jpg"], "orphans": [{"path": "images/b.jpg", "size": 3}]}
    process = _FakeAuditProcess(report=report)
    record = _install_fake_exec(monkeypatch, process)

    result = await storage_audit_service.run_storage_audit(
        ["images/2026-11/a.jpg", "videos/2026-11/c.mp4"]
    )

    assert result == report
    argv = record["argv"]
    assert argv[0] == str(audit_env["binary"])
    assert argv[1] == str(audit_env["root"])
    # manifest 路径由 service 内部临时目录生成，内容必须覆盖全部 expected 路径
    assert record["manifest"] == {
        "expected": [
            {"path": "images/2026-11/a.jpg"},
            {"path": "videos/2026-11/c.mp4"},
        ]
    }
    assert argv[3:] == (
        "--dirs",
        "images,thumbnails,videos",
        "--output",
        str(process.report_path),
    )
    # stdin 关掉（sidecar 只读 manifest 文件），stdout/stderr 供诊断
    assert record["kwargs"]["stdin"] is asyncio.subprocess.DEVNULL
    assert record["kwargs"]["stdout"] is asyncio.subprocess.PIPE
    assert record["kwargs"]["stderr"] is asyncio.subprocess.PIPE
    # 临时目录必须在 storage_root 下（避免跨盘），run 返回后已清理
    temp_dir = record["temp_dir"]
    assert temp_dir.parent == audit_env["root"]
    assert temp_dir.name.startswith("storage-audit-")
    assert not temp_dir.exists()


async def test_run_storage_audit_returns_none_when_binary_absent(tmp_path, monkeypatch):
    """未配置二进制时直接 None，绝不能起进程（回退由调用方负责）。"""
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    monkeypatch.setattr(settings, "media_worker_bin", "")
    called: list[tuple] = []

    async def fake_exec(*argv, **kwargs):
        called.append(argv)
        raise AssertionError("未配置二进制时不应创建子进程")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    assert await storage_audit_service.run_storage_audit(["images/a.jpg"]) is None
    assert called == []


# ── run_storage_audit：失败/超时全部回退 None（不向上抛） ──


async def test_run_storage_audit_nonzero_exit_returns_none(audit_env, monkeypatch):
    process = _FakeAuditProcess(
        returncode=2, stderr=b"storage-audit failed: permission denied"
    )
    _install_fake_exec(monkeypatch, process)

    assert await storage_audit_service.run_storage_audit(["images/a.jpg"]) is None


async def test_run_storage_audit_missing_report_returns_none(audit_env, monkeypatch):
    """返回码 0 但没写报告文件（OError 路径）→ None，不抛。"""
    process = _FakeAuditProcess()  # report/raw_report 均为 None → 不落盘
    _install_fake_exec(monkeypatch, process)

    assert await storage_audit_service.run_storage_audit(["images/a.jpg"]) is None


async def test_run_storage_audit_broken_report_returns_none(audit_env, monkeypatch):
    """报告 JSON 损坏 → None，不抛。"""
    process = _FakeAuditProcess(raw_report="{not-a-valid-json")
    _install_fake_exec(monkeypatch, process)

    assert await storage_audit_service.run_storage_audit(["images/a.jpg"]) is None


async def test_run_storage_audit_timeout_kills_process(audit_env, monkeypatch):
    """超时：kill + wait 收尾后返回 None，超时值取自配置。"""
    process = _FakeAuditProcess()
    _install_fake_exec(monkeypatch, process)
    recorded: dict = {}

    async def fake_wait_for(coro, timeout):
        recorded["timeout"] = timeout
        coro.close()  # 避免 coroutine never awaited 警告
        raise TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    assert await storage_audit_service.run_storage_audit(["images/a.jpg"]) is None
    assert recorded["timeout"] == settings.media_worker_audit_timeout_seconds
    assert process.killed is True
    assert process.waited is True


class _HangingAuditProcess:
    """communicate 永不返回的假进程（验证真实 asyncio.wait_for 超时路径）。"""

    returncode = None

    def __init__(self):
        self.killed = False
        self.waited = False

    async def communicate(self):
        await asyncio.sleep(3600)

    def kill(self):
        self.killed = True

    async def wait(self):
        self.waited = True
        return self.returncode


async def test_run_storage_audit_real_wait_for_timeout(audit_env, monkeypatch):
    """真实 asyncio.wait_for 超时：捕获内建 TimeoutError，kill + wait 后返回 None。"""
    monkeypatch.setattr(settings, "media_worker_audit_timeout_seconds", 0.01)
    process = _HangingAuditProcess()

    async def fake_exec(*argv, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    # 外层 5s 保护：实现若读错超时字段（等 120s）则测试失败而不是挂死
    result = await asyncio.wait_for(
        storage_audit_service.run_storage_audit(["images/a.jpg"]), timeout=5
    )
    assert result is None
    assert process.killed is True
    assert process.waited is True


# ── config：media_worker_* 默认值与边界 ──


def test_media_worker_config_defaults():
    fresh = Settings(_env_file=None)
    assert fresh.media_worker_bin == ""
    assert fresh.media_worker_archive_threshold == 100
    assert fresh.media_worker_archive_timeout_seconds == 300
    assert fresh.media_worker_audit_timeout_seconds == 120


def test_media_worker_config_accepts_boundaries():
    fresh = Settings(
        _env_file=None,
        media_worker_archive_threshold=1,
        media_worker_archive_timeout_seconds=5,
        media_worker_audit_timeout_seconds=1800,
    )
    assert fresh.media_worker_archive_threshold == 1
    assert fresh.media_worker_archive_timeout_seconds == 5
    assert fresh.media_worker_audit_timeout_seconds == 1800


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("media_worker_archive_threshold", 0),
        ("media_worker_archive_threshold", 1001),
        ("media_worker_archive_timeout_seconds", 4.9),
        ("media_worker_archive_timeout_seconds", 1800.5),
        ("media_worker_audit_timeout_seconds", 4),
        ("media_worker_audit_timeout_seconds", 1801),
    ],
)
def test_media_worker_config_rejects_out_of_range(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})
