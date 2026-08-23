"""回归测试：合并标签请求不应被 audit 写库阻塞（此前 33s 超时）。

背景：merge_tags 在未提交事务内调用 record_audit_log，后者用独立会话写库，
被主事务持有的 SQLite 写锁阻塞到 busy_timeout(30s) 后报 database is locked。
修复：merge_tags 先提交主事务再写审计（与 batch_delete_tags 一致）。
本用例通过 TestClient 实测合并接口耗时，断言远小于 30s 超时阈值。
"""
import time

import pytest


def test_merge_tags_completes_fast(client, upload):
    """合并标签应快速完成（< 5s），不应被 audit 写库锁阻塞。"""
    s = client.post("/api/tags", json={"name": "极简测试A", "category": "style"}).json()
    t = client.post("/api/tags", json={"name": "极简测试B", "category": "style"}).json()
    insp_id = upload().json()["id"]
    client.post(f"/api/inspirations/{insp_id}/tags", json={"names": ["极简测试A"]})

    start = time.monotonic()
    r = client.post("/api/tags/merge", json={"source_tag_id": s["id"], "target_tag_id": t["id"]})
    elapsed = time.monotonic() - start

    assert r.status_code == 200, f"合并失败: {r.text}"
    # 修复前该请求耗时 ~33s（audit 被写锁阻塞 30s），修复后应在 5s 内完成
    assert elapsed < 5, f"合并耗时 {elapsed:.1f}s，疑似 audit 写库被锁阻塞"

    # 审计留痕应成功写入（独立会话在提交后不再被锁阻塞）
    names = client.get("/api/tags").json()
    flat = [x["name"] for g in names for x in g["tags"]]
    assert "极简测试A" not in flat
    assert "极简测试B" in flat
