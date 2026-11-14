"""L2 系统测试：API 建任务 → 真 worker 子进程执行 → API 读到结果。

Phase 2（见 `docs/overview/测试体系与系统测试方案-2026-11-14.md` §6）：
这是单测无法覆盖的**进程边界**——API 进程只写 task_queue，worker 进程认领执行、
把标签/分析日志写回同一 SQLite，再由 API 查询出来。

上游 Ollama 用 `conftest.py` 里的 HTTP 桩（返回固定「法式/米色风衣」分析 JSON），
所以本用例验证的是：任务状态机、跨进程 DB 读写、JSON 解析→标签落库 这条链路，
而不是模型质量。
"""

import time

import pytest

pytestmark = pytest.mark.system

_TERMINAL = {"success", "failed", "cancelled"}


def _png(color: tuple[int, int, int]) -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (48, 48), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _wait_task(api, task_id: int, *, timeout: float = 180.0) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        response = api.get(f"/api/tasks/{task_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last.get("status") in _TERMINAL:
            return last
        time.sleep(0.5)
    raise AssertionError(f"任务 #{task_id} 未在 {timeout}s 内到终态：{last}")


def test_batch_analyze_runs_in_worker_and_result_is_visible(api, worker_process):
    # 1) 上传一张素材（每次不同颜色，避免跨用例内容去重）
    upload = api.post(
        "/api/inspirations",
        files={"file": ("worker-smoke.png", _png((90, 140, 60)), "image/png")},
        data={"source_type": "manual_upload"},
    )
    assert upload.status_code == 201, upload.text
    inspiration_id = upload.json()["id"]

    # 2) API 进程创建批量分析任务（旧版数组格式：单模型单提示词）
    created = api.post("/api/ai/batch-analyze", json=[inspiration_id])
    assert created.status_code == 200, created.text
    task_id = created.json()["task_id"]

    # 3) 真 worker 认领并执行，任务到终态
    task = _wait_task(api, task_id)
    assert task["status"] == "success", task
    assert task["done"] >= 1

    # 4) 结果在 API 可见：素材详情带上了桩模型返回的标签
    detail = api.get(f"/api/inspirations/{inspiration_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    tag_names = {tag["tag"]["name"] for tag in body.get("tags", [])}
    assert tag_names, f"worker 执行后素材没有标签：{body}"
    assert any("风衣" in name or "米色" in name for name in tag_names), tag_names

    # 5) 分析历史里能查到这次执行（worker 写的 AIAnalysisLog）
    history = api.get("/api/ai/history", params={"inspiration_id": inspiration_id, "size": 10})
    assert history.status_code == 200, history.text
    logs = history.json().get("items", [])
    assert logs, "分析历史为空：worker 可能没有写 AIAnalysisLog"
    assert logs[0]["status"] == "success"
