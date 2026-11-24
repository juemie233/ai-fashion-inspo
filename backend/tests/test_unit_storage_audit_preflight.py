"""备份前完整性预检脚本 ``backend/scripts/storage_audit_preflight.py`` 单测。

脚本不在包内（backend/scripts 不是 package），用 importlib 按文件路径加载。

覆盖：
- ``parse_args``：默认不输出文件、``--json`` 解析为 Path；
- ``build_report``：字段映射（scanner / missing_count / orphan_count /
  orphan_total_size_bytes / sample 截断 20 / trash_invariant_count），
  以及 Rust 报告缺 scanner 时默认 python；
- ``main``：``--json`` 写文件并返回 0、stderr 有汇总行；不传时 JSON 打到 stdout。

``integrity_check`` 全程 monkeypatch，不触真实 DB 扫描。
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "storage_audit_preflight.py"


@pytest.fixture(scope="module")
def preflight():
    """按文件路径加载预检脚本模块（等价于 ``python scripts/storage_audit_preflight.py``）。"""
    spec = importlib.util.spec_from_file_location(
        "storage_audit_preflight_under_test", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_integrity_result(**overrides) -> dict:
    """构造与 admin_stats_service.integrity_check 返回结构一致的假结果。"""
    result = {
        "scanner": "rust",
        "missing_files": [
            {"file_path": f"images/m{i}.jpg", "inspiration_ids": []} for i in range(25)
        ],
        "missing_count": 25,
        "orphan_files": [
            {"file_path": f"images/o{i}.jpg", "size_bytes": i} for i in range(25)
        ],
        "orphan_count": 25,
        "orphan_total_size_bytes": 300,
        "trash_invariants": [{"rule": "R1", "id": "insp-1"}],
    }
    result.update(overrides)
    return result


def test_parse_args_defaults_to_stdout(preflight, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["storage_audit_preflight.py"])
    assert preflight.parse_args().json is None


def test_parse_args_reads_json_path(preflight, monkeypatch, tmp_path):
    target = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["storage_audit_preflight.py", "--json", str(target)])
    assert preflight.parse_args().json == target


async def test_build_report_maps_fields_and_truncates_samples(preflight, monkeypatch):
    async def fake_integrity_check(db):
        return _fake_integrity_result()

    monkeypatch.setattr(preflight.admin_stats_service, "integrity_check", fake_integrity_check)

    report = await preflight.build_report()

    assert report["scanner"] == "rust"
    assert report["missing_count"] == 25
    assert report["orphan_count"] == 25
    assert report["orphan_total_size_bytes"] == 300
    # sample 只保留前 20 条（报告体积上限）
    assert len(report["missing_sample"]) == 20
    assert report["missing_sample"][0] == {"file_path": "images/m0.jpg", "inspiration_ids": []}
    assert len(report["orphan_sample"]) == 20
    assert report["orphan_sample"][0] == {"file_path": "images/o0.jpg", "size_bytes": 0}
    assert report["trash_invariant_count"] == 1
    assert set(report) == {
        "scanner",
        "missing_count",
        "orphan_count",
        "orphan_total_size_bytes",
        "missing_sample",
        "orphan_sample",
        "trash_invariant_count",
    }


async def test_build_report_defaults_scanner_to_python(preflight, monkeypatch):
    """Rust 报告缺 scanner 字段时默认 python（与 admin_stats_service 回退口径一致）。"""
    async def fake_integrity_check(db):
        result = _fake_integrity_result()
        result.pop("scanner")
        result["trash_invariants"] = []
        return result

    monkeypatch.setattr(preflight.admin_stats_service, "integrity_check", fake_integrity_check)

    report = await preflight.build_report()
    assert report["scanner"] == "python"
    assert report["trash_invariant_count"] == 0


def test_main_writes_json_file_and_reports_to_stderr(
    preflight, monkeypatch, tmp_path, capsys
):
    target = tmp_path / "nested" / "report.json"
    monkeypatch.setattr(sys, "argv", ["storage_audit_preflight.py", "--json", str(target)])

    async def fake_build_report():
        return {
            "scanner": "python",
            "missing_count": 0,
            "orphan_count": 1,
            "orphan_total_size_bytes": 12,
            "missing_sample": [],
            "orphan_sample": [{"file_path": "images/o.jpg", "size_bytes": 12}],
            "trash_invariant_count": 0,
        }

    monkeypatch.setattr(preflight, "build_report", fake_build_report)

    assert preflight.main() == 0
    # 父目录不存在时自动创建，文件内容是格式化 JSON
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["orphan_count"] == 1
    assert payload["orphan_sample"] == [{"file_path": "images/o.jpg", "size_bytes": 12}]
    captured = capsys.readouterr()
    assert captured.out == ""
    assert (
        "预检完成：scanner=python missing=0 orphan=1 orphan_bytes=12 trash_violations=0"
        in captured.err
    )


def test_main_prints_json_to_stdout_without_json_flag(preflight, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["storage_audit_preflight.py"])

    async def fake_build_report():
        return {
            "scanner": "rust",
            "missing_count": 2,
            "orphan_count": 0,
            "orphan_total_size_bytes": 0,
            "missing_sample": [],
            "orphan_sample": [],
            "trash_invariant_count": 1,
        }

    monkeypatch.setattr(preflight, "build_report", fake_build_report)

    assert preflight.main() == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["scanner"] == "rust"
    assert payload["missing_count"] == 2
    assert (
        "预检完成：scanner=rust missing=2 orphan=0 orphan_bytes=0 trash_violations=1"
        in captured.err
    )
