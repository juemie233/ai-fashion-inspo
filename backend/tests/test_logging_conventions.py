"""日志约定回归：except 里记录非预期异常必须带堆栈（P1-1）。

背景（2026-11-02 日志全量扫描 P1-1）：全仓 ``logger.exception`` 只有 6 处、
``logger.error(..., exc_info=True)`` 0 处，43 处 ``logger.error`` 绝大多数是
``f"... {e}"``，只留一句字符串——同一个错误只看得到「失败: xxx」，看不到
调用栈，排查只能靠复现。

约定：``except`` 处理器内记录「非预期异常」统一用 ``logger.exception(...)``
（或 ``logger.error(..., exc_info=True)``）；预期内的降级用
``logger.warning`` / ``logger.debug``，不要用 ERROR。

本测试用 AST 静态扫描，破坏约定会直接失败，避免再退回「无堆栈 ERROR」。
"""

import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"

# 允许的例外（文件相对路径 -> 原因）。目前为空；确有必要时才登记，
# 并在原因里写清楚「为什么这条 ERROR 不需要堆栈」。
ALLOWLIST: dict[str, str] = {}

LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}


def _iter_python_files() -> list[Path]:
    return sorted(path for path in APP_DIR.rglob("*.py") if "__pycache__" not in path.parts)


def _is_log_call(node: ast.Call) -> bool:
    return isinstance(node.func, ast.Attribute) and node.func.attr in LOG_METHODS


def _has_exc_info(call: ast.Call) -> bool:
    return any(keyword.arg == "exc_info" for keyword in call.keywords)


def test_except_内_logger_error_必须带堆栈() -> None:
    """except 处理器里的 ``logger.error`` 必须带 ``exc_info`` 或改用 ``exception``。"""
    violations: list[str] = []
    for path in _iter_python_files():
        relative = path.relative_to(APP_DIR.parent).as_posix()
        if relative in ALLOWLIST:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # 语法错误交给 ruff/pytest，不在这里重复报
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            calls = [sub for sub in ast.walk(node) if isinstance(sub, ast.Call) and _is_log_call(sub)]
            has_exception_call = any(call.func.attr == "exception" for call in calls)
            for call in calls:
                if call.func.attr != "error" or _has_exc_info(call):
                    continue
                if has_exception_call:
                    continue
                message = ast.unparse(call.args[0]) if call.args else "<无消息>"
                violations.append(f"{relative}:{call.lineno}  {message[:80]}")
    assert not violations, (
        "以下 except 处理器用 logger.error 记录但没带堆栈，应改为 logger.exception：\n"
        + "\n".join(f"  - {item}" for item in violations)
    )


# 允许保持静默的极少数「日志系统自身」模块：
# - config.py：加载 .env 时 logging 可能尚未配置，且这里失败已由调用方兜底；
# - logging_config.py：日志 filter 内部不能写日志（会递归）；
# - utils/daily_log.py：日志 handler 的 emit 失败路径，写日志会再次失败/递归。
SILENT_ALLOWLIST = {
    "app/config.py",
    "app/logging_config.py",
    "app/utils/daily_log.py",
}


def test_静默except必须分类记录() -> None:
    """除允许的日志系统自身模块外，``except`` 不能只有 pass/continue/return。

    P1-2：全仓 107 个静默点无法区分「预期降级」和「吞掉 bug」。约定为：
    预期降级路径至少记一条 ``logger.debug``（默认级别不输出，需要时
    ``LOG_LEVEL=DEBUG`` 打开）；非预期异常用 ``logger.exception``。
    """
    violations: list[str] = []
    for path in _iter_python_files():
        relative = path.relative_to(APP_DIR.parent).as_posix()
        if relative in SILENT_ALLOWLIST:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or not node.body:
                continue
            silent = all(
                isinstance(statement, (ast.Pass, ast.Continue, ast.Break))
                or isinstance(statement, ast.Return)
                for statement in node.body
            )
            if not silent:
                continue
            calls = [
                sub for sub in ast.walk(node) if isinstance(sub, ast.Call) and _is_log_call(sub)
            ]
            if not calls:
                violations.append(f"{relative}:{node.lineno}")
    assert not violations, (
        "以下 except 处理器静默吞掉异常（加 logger.debug 分类或 logger.exception）：\n"
        + "\n".join(f"  - {item}" for item in violations)
    )
