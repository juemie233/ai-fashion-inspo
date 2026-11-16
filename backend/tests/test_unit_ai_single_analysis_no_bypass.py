"""R2（task-9）回归守卫：单图 AI 分析不得再走 API 进程内旁路。

2026-11-16 起 ``POST /api/ai/analyze/{id}`` 改为创建 ``batch_analyze`` worker 任务
（priority=10、force_retry=True），API 进程内存分析队列（``_active_analyses`` /
``_pending_queue`` / ``_run_analysis``）及其 5 个队列端点已删除。

为什么需要本文件：路由层用例会 mock ``create_batch_analyze_task``，如果有人为图省事
再把 ``asyncio.create_task(_run_analysis(...))`` 加回来，现有用例仍可能通过——那会
重新绕过 task_queue 的审计/重试/看门狗与 GPU 互斥（R2 的核心约束）。这里用
「端点探测 + AST 静态断言」把旁路与旧端点一起锁死。

只读断言，不改生产代码。
"""

import ast
from pathlib import Path

import pytest

_ROUTERS = Path(__file__).resolve().parents[1] / "app" / "routers"

# 内存队列/旁路符号：作为代码标识符（Name / Import / Attribute）出现即违规；
# 注释与 docstring 里的说明文字不受影响（AST 不把字符串内容当 Name）。
_FORBIDDEN_SYMBOLS = frozenset(
    {
        "_active_analyses",
        "_analysis_semaphore",
        "_analysis_tasks",
        "_pending_queue",
        "_run_analysis",
        "_task_by_id",
        "get_in_memory_queue_ids",
    }
)


def _identifiers(path: Path) -> tuple[set[str], ast.Module]:
    """返回源码中出现的代码标识符集合与 AST（不含注释/docstring 文本）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
    return names, tree


@pytest.mark.parametrize("filename", ["ai_analysis.py", "ai_shared.py"])
def test_router_sources_have_no_in_memory_analysis_bypass(filename: str) -> None:
    """路由源码不得再出现内存分析队列符号或 API 进程内 create_task 分析。"""
    names, tree = _identifiers(_ROUTERS / filename)

    leaked = sorted(names & _FORBIDDEN_SYMBOLS)
    assert not leaked, (
        f"{filename} 仍包含 API 进程内存分析队列/旁路符号：{leaked}；"
        "单图分析必须只通过 create_batch_analyze_task 进入 worker 队列（task-9/R2）"
    )

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_task"
        ):
            raise AssertionError(
                f"{filename} 第 {node.lineno} 行出现 create_task 调用："
                "禁止在 API 进程内旁路执行分析，必须创建 batch_analyze 任务"
                "（否则绕过 task_queue 审计/重试/看门狗与 GPU 互斥）"
            )


def test_legacy_in_memory_queue_endpoints_are_gone(client) -> None:
    """旧内存队列端点已删除：调用方拿到 404，而不是静默恢复旧行为。"""
    for path in ("/api/ai/queue/pending", "/api/ai/active-analyses"):
        response = client.get(path)
        assert response.status_code == 404, (
            f"GET {path} 应返回 404（端点已删除），实际 {response.status_code}"
        )

    for path in ("/api/ai/queue/pause", "/api/ai/queue/resume"):
        response = client.post(path)
        assert response.status_code == 404, (
            f"POST {path} 应返回 404（端点已删除），实际 {response.status_code}"
        )

    response = client.delete("/api/ai/queue/not-a-real-id")
    assert response.status_code == 404, (
        f"DELETE /api/ai/queue/{{id}} 应返回 404（端点已删除），实际 {response.status_code}"
    )
