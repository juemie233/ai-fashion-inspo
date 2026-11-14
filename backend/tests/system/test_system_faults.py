"""L2 系统测试 · 故障注入（Phase 3）：worker 崩溃恢复 & 上游故障不挂死。

验证目标（见 `docs/overview/测试体系与系统测试方案-2026-11-14.md` §4.3/§6）：

1. **worker 执行中崩溃**（强杀进程，模拟断电/被杀）：
   - 任务留在 running（崩溃现场）；
   - 等待心跳超过 stale 阈值后重启 worker → 启动对账把任务重置为 pending 并计一次重试；
   - 重跑成功，`retry_count >= 1`。这是单测无法覆盖的「进程崩溃 → 心跳租约 → 重启恢复」链路。
2. **上游 Ollama 返回 500**：任务不能永久卡 running，必须在有限时间内变成
   failed 或 pending（带退避重试），且 error 有可读原因。

为了缩短验证时间，`conftest.py` 给系统测试把 worker 心跳间隔设为 0.5s、
stale 阈值设为 3s（生产默认 10s / 90s，行为不变）。
"""

import time

import pytest

pytestmark = pytest.mark.system

_TERMINAL = {"success", "failed", "cancelled"}


@pytest.fixture(autouse=True)
def _reset_upstream_stub(fake_ollama):
    """每个故障用例前后都把上游桩恢复成正常模式，避免污染后续用例。"""
    fake_ollama.mode = "ok"
    fake_ollama.hang_seconds = 120.0
    yield
    fake_ollama.mode = "ok"
    fake_ollama.hang_seconds = 120.0


def _png(color: tuple[int, int, int]) -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (48, 48), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _upload(api, color: tuple[int, int, int]) -> str:
    response = api.post(
        "/api/inspirations",
        files={"file": ("fault.png", _png(color), "image/png")},
        data={"source_type": "manual_upload"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _create_analysis_task(api, inspiration_id: str) -> int:
    created = api.post("/api/ai/batch-analyze", json=[inspiration_id])
    assert created.status_code == 200, created.text
    return int(created.json()["task_id"])


def _get_task(api, task_id: int) -> dict:
    response = api.get(f"/api/tasks/{task_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _wait_task(api, task_id: int, predicate, *, timeout: float = 120.0, what: str = "条件") -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        last = _get_task(api, task_id)
        if predicate(last):
            return last
        time.sleep(0.5)
    raise AssertionError(f"任务 #{task_id} 未在 {timeout}s 内满足「{what}」：{last}")


def test_worker_crash_mid_task_is_recovered_by_restart(api, fake_ollama, worker_ctl):
    """强杀 worker → 重启 → stale running 被重置并重跑成功。"""
    # 上游先挂起：保证任务停在 running，而不是几毫秒就执行完
    fake_ollama.mode = "hang"
    fake_ollama.hang_seconds = 120
    worker_ctl.start()

    inspiration_id = _upload(api, (30, 60, 90))
    task_id = _create_analysis_task(api, inspiration_id)

    # 1) 等 worker 认领并进入 running
    running = _wait_task(
        api, task_id, lambda t: t["status"] == "running", what="worker 认领任务"
    )
    assert running["status"] == "running"

    # 2) 模拟断电：强杀 worker 进程（不写终态）
    worker_ctl.kill()
    time.sleep(0.8)
    after_kill = _get_task(api, task_id)
    assert after_kill["status"] == "running", f"崩溃后任务应留在 running：{after_kill}"

    # 3) 等心跳超过 stale 阈值（系统测试里配置为 3s）
    time.sleep(4.0)

    # 4) 上游恢复，重启 worker：启动对账应把 stale running 重置为 pending 并重试
    fake_ollama.mode = "ok"
    worker_ctl.start()

    final = _wait_task(
        api,
        task_id,
        lambda t: t["status"] in _TERMINAL,
        timeout=150,
        what="崩溃恢复后任务到终态",
    )
    assert final["status"] == "success", final
    assert int(final.get("retry_count") or 0) >= 1, f"崩溃恢复应计一次重试：{final}"


def test_upstream_http500_does_not_leave_task_running(api, fake_ollama, worker_ctl):
    """上游 500：任务必须离开 running（failed 或带退避的 pending），且错误可读。"""
    fake_ollama.mode = "http500"
    worker_ctl.start()

    inspiration_id = _upload(api, (200, 30, 30))
    task_id = _create_analysis_task(api, inspiration_id)

    def _left_running(t: dict) -> bool:
        if t["status"] == "failed":
            return True
        # 可恢复错误会被调度重试：retry_count 增加且回到 pending 也算「已处理」
        return t["status"] == "pending" and int(t.get("retry_count") or 0) >= 1

    state = _wait_task(
        api, task_id, _left_running, timeout=90, what="上游 500 后任务被妥善收尾"
    )
    assert state["status"] in {"failed", "pending"}, state
    assert state.get("error"), f"上游故障后任务应带可读 error：{state}"
