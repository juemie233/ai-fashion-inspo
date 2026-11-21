"""admin_stats_service.integrity_check 的 Rust sidecar 分支与异常回退单测。

覆盖（feat/rust-media-worker 分支新增逻辑）：
- ``run_storage_audit`` 抛异常 / 返回 None → 回退 Python 扫描，
  ``scanner == "python"``、HTTP 200，且 Python 扫描仍能检出 missing；
- Rust 报告 missing=[] / orphans=[] → 计数全 0、``scanner == "rust"``；
- orphans 脏条目（非 dict / 缺 path / path 非 str）被过滤，缺 size 按 0 计；
- Rust 报告的 missing 路径在 DB 无映射时 ``inspiration_ids == []``。

Rust 成功映射 DB 素材 ID 的用例已由 ``tests/test_trash.py`` 的
``test_integrity_check_maps_rust_report`` 覆盖，这里不重复。
"""

import pytest

from app.config import settings
from app.services import storage_audit_service


def _get_integrity(client) -> dict:
    resp = client.get("/api/admin/integrity-check")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_integrity_check_falls_back_when_sidecar_raises(client, upload, monkeypatch):
    """sidecar 启动异常不能 500；回退 Python 后仍检出磁盘缺失文件。"""
    insp_id = upload().json()["id"]
    detail = client.get(f"/api/inspirations/{insp_id}").json()
    file_path = detail["file_path"]
    target = settings.storage_root / file_path
    assert target.is_file()
    target.unlink()

    async def boom(paths):
        raise RuntimeError("sidecar 启动失败")

    monkeypatch.setattr(storage_audit_service, "run_storage_audit", boom)

    body = _get_integrity(client)
    assert body["scanner"] == "python"
    assert body["missing_count"] >= 1
    mapped = next(item for item in body["missing_files"] if item["file_path"] == file_path)
    assert mapped["inspiration_ids"] == [insp_id]


async def test_integrity_check_falls_back_when_sidecar_returns_none(
    client, upload, monkeypatch
):
    """未配置 sidecar（返回 None）→ 正常走 Python，不算错误。"""
    upload()

    async def unavailable(paths):
        return None

    monkeypatch.setattr(storage_audit_service, "run_storage_audit", unavailable)

    body = _get_integrity(client)
    assert body["scanner"] == "python"
    assert body["missing_count"] == 0
    assert body["orphan_count"] == 0


async def test_integrity_check_rust_empty_report(client, upload, monkeypatch):
    """Rust 报告为空列表：计数全 0，垃圾桶不变量仍由 Python 提供。"""
    upload()

    async def empty_report(paths):
        return {"missing": [], "orphans": []}

    monkeypatch.setattr(storage_audit_service, "run_storage_audit", empty_report)

    body = _get_integrity(client)
    assert body["scanner"] == "rust"
    assert body["missing_files"] == []
    assert body["missing_count"] == 0
    assert body["orphan_files"] == []
    assert body["orphan_count"] == 0
    assert body["orphan_total_size_bytes"] == 0
    assert body["trash_invariants"] == []


async def test_integrity_check_rust_filters_dirty_orphan_entries(
    client, upload, monkeypatch
):
    """orphans 里的脏条目（非 dict / 缺 path / path 非 str）必须被过滤。"""
    upload()

    async def dirty_report(paths):
        return {
            "missing": [],
            "orphans": [
                {"path": "images/orphan.jpg", "size": 123},
                "not-a-dict",
                {"size": 9},
                {"path": 123, "size": 7},
                {"path": "images/orphan2.png"},
            ],
        }

    monkeypatch.setattr(storage_audit_service, "run_storage_audit", dirty_report)

    body = _get_integrity(client)
    assert body["scanner"] == "rust"
    assert body["orphan_files"] == [
        {"file_path": "images/orphan.jpg", "size_bytes": 123},
        {"file_path": "images/orphan2.png", "size_bytes": 0},
    ]
    assert body["orphan_count"] == 2
    assert body["orphan_total_size_bytes"] == 123


async def test_integrity_check_rust_missing_without_db_mapping(client, upload, monkeypatch):
    """Rust 报的 missing 路径若在 DB 无映射，inspiration_ids 为空列表。"""
    upload()

    async def report(paths):
        return {"missing": ["images/not-in-db.jpg"], "orphans": []}

    monkeypatch.setattr(storage_audit_service, "run_storage_audit", report)

    body = _get_integrity(client)
    assert body["scanner"] == "rust"
    assert body["missing_count"] == 1
    assert body["missing_files"] == [
        {"file_path": "images/not-in-db.jpg", "inspiration_ids": []}
    ]


@pytest.mark.parametrize("report", [{"missing": []}, {"orphans": []}])
async def test_integrity_check_rust_report_tolerates_missing_keys(
    client, upload, monkeypatch, report
):
    """Rust 报告缺 missing 或 orphans 键时按空列表处理，不 KeyError。"""
    upload()

    async def partial_report(paths):
        return report

    monkeypatch.setattr(storage_audit_service, "run_storage_audit", partial_report)

    body = _get_integrity(client)
    assert body["scanner"] == "rust"
    assert body["missing_count"] == 0
    assert body["orphan_count"] == 0


# ── 疑似健壮性缺口复现（契约外输入；修复后自动 xpass） ──
#
# 背景：Rust 分支的映射阶段（把 report 映射成 missing_files/orphan_files）
# 不在异常回退的 try 内。orphans 分支过滤了「非 dict / path 非 str」，但
# missing 分支完全没有过滤；orphans.size 也没有类型兜底。sidecar 输出契约
# 外脏数据时巡检接口会 500（而非回退 Python）。正常 Rust 二进制不会产出
# 这类数据，故列为健壮性缺口而非主链路 bug；修复后这两条会 xpass。


@pytest.mark.xfail(
    reason="sidecar 报告 missing 元素不可哈希（list）时映射阶段 TypeError → 500；"
    "missing 分支缺少与 orphans 对称的类型过滤",
    strict=True,
)
async def test_integrity_check_dirty_missing_entry_should_not_500(
    client, upload, monkeypatch
):
    upload()

    async def dirty_report(paths):
        return {"missing": [["not", "a", "path"]], "orphans": []}

    monkeypatch.setattr(storage_audit_service, "run_storage_audit", dirty_report)

    resp = client.get("/api/admin/integrity-check")
    assert resp.status_code == 200


@pytest.mark.xfail(
    reason="sidecar 报告 orphans.size 非数字（None）时 int() TypeError → 500；"
    "orphan_files 缺 size 数字兜底",
    strict=True,
)
async def test_integrity_check_dirty_orphan_size_should_not_500(
    client, upload, monkeypatch
):
    upload()

    async def dirty_report(paths):
        return {"missing": [], "orphans": [{"path": "images/orphan.jpg", "size": None}]}

    monkeypatch.setattr(storage_audit_service, "run_storage_audit", dirty_report)

    resp = client.get("/api/admin/integrity-check")
    assert resp.status_code == 200
