"""L2 系统测试 · 出图链路（Phase 4）：API 建批次 → worker 调 ComfyUI 桩 → 候选落盘 → 确认入库。

ComfyUI 用 `conftest.py` 里的 HTTP 桩（/system_stats、/prompt、/history、/view），
所以本用例验证的是真实进程边界上的链路：

1. API 创建批次（写 generation_runs + 任务队列）；
2. 真 worker 认领 image_gen 任务，走显存守门 → 提交工作流 → 轮询 → 从 /view 取图；
3. 候选图真实落盘到「临时 storage」；
4. 确认入库后素材库能按 `generation_run_id` 反查到这批素材。
"""

import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.system

_TERMINAL = {"success", "failed", "cancelled"}


def _get_task(api, task_id: int) -> dict:
    response = api.get(f"/api/tasks/{task_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _wait_task(api, task_id: int, *, timeout: float = 180.0) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        last = _get_task(api, task_id)
        if last.get("status") in _TERMINAL:
            return last
        time.sleep(0.3)
    raise AssertionError(f"任务 #{task_id} 未在 {timeout}s 内到终态：{last}")


def test_generate_run_worker_comfyui_and_confirm_import(api, worker_process, system_root: Path):
    created = api.post(
        "/api/generate/runs",
        json={"prompt": "法式通勤 米色风衣 全身照", "count": 2},
    )
    assert created.status_code == 200, created.text
    payload = created.json()
    run_id = payload["run_id"]
    task_id = payload["task_id"]

    task = _wait_task(api, task_id)
    assert task["status"] == "success", task

    detail = api.get(f"/api/generate/runs/{run_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["run"]["status"] == "done", body["run"]
    candidates = body["items"]
    assert len(candidates) == 2, candidates

    # 候选图真实落在临时 storage 的批次目录里
    batch_dir = system_root / "storage" / body["run"]["batch_dir"]
    for candidate in candidates:
        assert (batch_dir / candidate["filename"]).exists(), candidate
        assert candidate["imported"] is False

    # 确认入库：素材库按 generation_run_id 反查应得到同样条数
    filenames = [candidate["filename"] for candidate in candidates]
    confirmed = api.post(
        f"/api/generate/runs/{run_id}/confirm", json={"filenames": filenames}
    )
    assert confirmed.status_code == 200, confirmed.text
    confirmed_body = confirmed.json()
    assert confirmed_body["imported"] == 2, confirmed_body
    assert len(confirmed_body["ids"]) == 2

    listing = api.get("/api/inspirations", params={"generation_run_id": run_id, "size": 10})
    assert listing.status_code == 200, listing.text
    assert {item["id"] for item in listing.json()["items"]} == set(confirmed_body["ids"])

    # 二次确认应幂等跳过（不重复入库）
    again = api.post(f"/api/generate/runs/{run_id}/confirm", json={"filenames": filenames})
    assert again.status_code == 200, again.text
    assert again.json()["imported"] == 0
    assert again.json()["skipped"] == 2
