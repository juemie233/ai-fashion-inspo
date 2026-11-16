"""架构边界守卫测试：用 AST 静态扫描锁住 app ↔ scripts 的依赖方向。

背景（2026-11-16「后端领域第一轮审计」task-2 的结论）：

- f2 共享实现已从 ``backend/scripts/f2_*`` 迁到 ``app/services/f2/``，应用层统一
  ``from app.services import f2``；f2 新逻辑不得再写回 scripts，也不得反向 import。
- 历史的 app ↔ scripts 双向依赖已收敛到唯一例外：``app/scrapers/xiaohongshu.py``
  在函数内延迟 import ``scripts.fetch_xhs_following``（复用关注列表解析）。被 import 的
  ``scripts/fetch_xhs_following.py`` 又 import ``app.config`` / ``app.utils.ip_location``，
  所以这里**必须保持函数内延迟导入**，挪到模块顶层会形成 import 环，启动即失败。
- router 层仍有 23 个文件直接 import ``app.models`` / ``sqlalchemy``（超出
  「router → service → model」边界）。本轮不做全量重排，先用「只减不增」棘轮
  防止继续扩散。

本测试只守**静态 import**。不在覆盖范围（属另外的依赖形态）：

- 运行时 subprocess 依赖：``app/services/backup_service.py`` 调
  ``scripts/ops/backup_data.sh``（运维域，方向是 app → scripts/ops）；
- 动态 import（``importlib.import_module`` / ``__import__``）。

失败时怎么办：

1. 报「新增了 app→scripts 依赖」：实现放 ``backend/app/**``；需要 CLI 时让脚本反向
   import app（scripts → app 是允许的方向）；
2. 报「必须写在函数体内」：把该 import 移回函数内部，保持延迟导入；
3. 报「白名单已过期」：从 ``SCRIPTS_IMPORT_WHITELIST`` 删除对应条目；
4. 报 router 直连 DB 数量上涨：把 SQL/ORM 逻辑移到 ``backend/app/services/``，
   router 只做参数校验、鉴权与 HTTP 转译；确属例外须经 Lead 评审后再调高基线。
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

# 路径锚点：本文件位于 backend/tests/，故 parents[0]=tests、parents[1]=backend。
BACKEND_DIR = Path(__file__).resolve().parents[1]
APP_DIR = BACKEND_DIR / "app"
F2_DIR = APP_DIR / "services" / "f2"
ROUTERS_DIR = APP_DIR / "routers"

# 唯一允许的 app→scripts import：文件（相对 backend）→ 允许的导入目标前缀。
# 这是**登记过的例外**，不是许可先例；新增条目必须先在架构文档说明原因。
SCRIPTS_IMPORT_WHITELIST: dict[str, str] = {
    "app/scrapers/xiaohongshu.py": "scripts.fetch_xhs_following",
}

# router 直连 DB（import app.models / sqlalchemy）的文件数棘轮基线。
# 2026-11-16 实测 23；只减不增——数量下降后请把基线一起调低，避免门禁放水。
ROUTER_DB_COUPLING_BASELINE = 23

_PYCACHE = "__pycache__"


def _backend_relative(path: Path) -> str:
    """把绝对路径转成相对 ``backend/`` 的 POSIX 路径，断言里更易读。"""
    return path.relative_to(BACKEND_DIR).as_posix()


def _read_sources(root: Path) -> list[tuple[str, str]]:
    """递归读取目录下的 .py 文件，返回 [(相对 backend 的路径, 源码文本)]。"""
    sources: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*.py")):
        if _PYCACHE in path.parts:
            continue
        sources.append((_backend_relative(path), path.read_text(encoding="utf-8")))
    return sources


def _is_scripts_module(name: str) -> bool:
    """判断模块名是否指向 backend/scripts 包。"""
    return name == "scripts" or name.startswith("scripts.")


def _scripts_import_targets(node: ast.AST) -> list[str]:
    """若 node 是 import scripts 语句，返回其导入目标（如 scripts.fetch_xhs_following）。"""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names if _is_scripts_module(alias.name)]
    if isinstance(node, ast.ImportFrom):
        # level != 0 是相对导入，指向不了顶层的 scripts 包。
        if node.level != 0 or not node.module:
            return []
        if node.module == "scripts":
            return [f"scripts.{alias.name}" for alias in node.names]
        if node.module.startswith("scripts."):
            return [f"{node.module}.{alias.name}" for alias in node.names]
    return []


def _iter_scripts_imports(source: str, filename: str) -> Iterable[tuple[int, str, bool]]:
    """产出 ``(行号, 导入目标, 是否位于函数体内)``，只包含 import scripts 的语句。

    「是否位于函数体内」决定它是不是延迟导入：模块顶层（含顶层 if/try/class 体内）
    的 import 在进程启动时就会执行，无法规避循环依赖。
    """
    tree = ast.parse(source, filename=filename)

    def walk(node: ast.AST, *, in_function: bool) -> Iterable[tuple[int, str, bool]]:
        for child in ast.iter_child_nodes(node):
            child_in_function = in_function or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            for target in _scripts_import_targets(child):
                yield child.lineno, target, child_in_function
            yield from walk(child, in_function=child_in_function)

    return walk(tree, in_function=False)


def _matches_target(target: str, expected: str) -> bool:
    """白名单目标前缀匹配（允许白名单模块下的子模块/具体符号）。"""
    return target == expected or target.startswith(f"{expected}.")


def _scan_scripts_imports(
    sources: Iterable[tuple[str, str]], whitelist: dict[str, str]
) -> tuple[list[str], set[str]]:
    """扫描源码集合，返回 (违规描述列表, 实际命中的白名单文件集合)。

    Args:
        sources: ``[(相对 backend 的路径, 源码文本)]``。
        whitelist: ``{相对路径: 允许的导入目标前缀}``；传空字典表示禁止任何 scripts import。

    Returns:
        违规描述列表（空列表 = 通过）与实际用到的白名单条目（用于过期检测）。
    """
    violations: list[str] = []
    whitelist_used: set[str] = set()
    for rel_path, source in sources:
        expected = whitelist.get(rel_path)
        for lineno, target, in_function in _iter_scripts_imports(source, rel_path):
            if expected is None:
                violations.append(
                    f"{rel_path}:{lineno} 新增了 app→scripts 依赖（{target}）。"
                    "应用层实现请放 backend/app/**；需要 CLI 时让 scripts 反向 import app。"
                )
                continue
            whitelist_used.add(rel_path)
            if not _matches_target(target, expected):
                violations.append(f"{rel_path}:{lineno} 白名单只允许 {expected}，实际导入了 {target}。")
            if not in_function:
                violations.append(
                    f"{rel_path}:{lineno} 的 {target} 必须写在函数体内（延迟导入）："
                    "scripts/fetch_xhs_following.py 反向 import app.config/app.utils，"
                    "放到模块顶层会形成 import 环。"
                )
    stale = sorted(set(whitelist) - whitelist_used)
    if stale:
        violations.append(f"白名单已过期：{stale} 不再 import scripts，请从 SCRIPTS_IMPORT_WHITELIST 删除。")
    return violations, whitelist_used


def _is_under(name: str, root: str) -> bool:
    """判断模块名是否等于 root 或位于其子模块下。"""
    return name == root or name.startswith(f"{root}.")


def _db_import_targets(node: ast.AST) -> list[str]:
    """若 node 直接 import 数据层（app.models / sqlalchemy），返回其目标模块名。"""
    if isinstance(node, ast.ImportFrom):
        if node.level != 0 or not node.module:
            return []
        if _is_under(node.module, "app.models") or _is_under(node.module, "sqlalchemy"):
            return [node.module]
    if isinstance(node, ast.Import):
        return [
            alias.name
            for alias in node.names
            if _is_under(alias.name, "sqlalchemy") or _is_under(alias.name, "app.models")
        ]
    return []


def _router_db_coupled_files(sources: Iterable[tuple[str, str]]) -> list[str]:
    """返回直接 import 数据层的文件（相对路径），用于 router→model 棘轮。"""
    coupled: list[str] = []
    for rel_path, source in sources:
        tree = ast.parse(source, filename=rel_path)
        if any(_db_import_targets(node) for node in ast.walk(tree)):
            coupled.append(rel_path)
    return coupled


def test_app_to_scripts_imports_are_whitelisted_and_deferred() -> None:
    """app/** 不得 import scripts；唯一白名单必须保持函数内延迟导入。"""
    sources = _read_sources(APP_DIR)
    assert sources, f"未在 {APP_DIR} 下找到 Python 文件，路径可能已变化，请同步更新本测试。"

    violations, _ = _scan_scripts_imports(sources, SCRIPTS_IMPORT_WHITELIST)
    assert not violations, "app→scripts 架构边界违规：\n" + "\n".join(f"- {item}" for item in violations)


def test_f2_package_does_not_import_scripts() -> None:
    """f2 实现已归位 app/services/f2，包内不得反向依赖 backend/scripts。"""
    sources = _read_sources(F2_DIR)
    assert sources, f"未在 {F2_DIR} 下找到 Python 文件，路径可能已变化，请同步更新本测试。"

    violations, _ = _scan_scripts_imports(sources, whitelist={})
    assert not violations, "app/services/f2 不得 import scripts：\n" + "\n".join(
        f"- {item}" for item in violations
    )


def test_router_db_coupling_ratchet() -> None:
    """router 直连 DB 的文件数只减不增（当前基线 23）。"""
    coupled = _router_db_coupled_files(_read_sources(ROUTERS_DIR))
    current = len(coupled)

    assert current <= ROUTER_DB_COUPLING_BASELINE, (
        f"router 直连 DB 的文件数由 {ROUTER_DB_COUPLING_BASELINE} 涨到 {current}：\n"
        + "\n".join(f"- {path}" for path in coupled)
        + "\n整改：SQL/ORM 逻辑移到 backend/app/services/，router 只做参数校验、鉴权与"
        "HTTP 转译；确属例外先经 Lead 评审再把基线调高。"
    )


class TestGuardLogic:
    """自检：证明上面的扫描器真能发现问题，避免守卫测试形同虚设。"""

    def test_detects_new_app_scripts_import(self) -> None:
        violations, _ = _scan_scripts_imports(
            [("app/fake.py", "from scripts import fetch_xhs_following\n")],
            whitelist={},
        )
        assert violations, "顶层 from scripts import 应被判定为违规。"

    def test_allows_whitelisted_deferred_import(self) -> None:
        source = "def fetch():\n    from scripts import fetch_xhs_following as fx\n"
        violations, used = _scan_scripts_imports(
            [("app/fake.py", source)],
            whitelist={"app/fake.py": "scripts.fetch_xhs_following"},
        )
        assert violations == []
        assert used == {"app/fake.py"}

    def test_rejects_whitelisted_import_at_module_level(self) -> None:
        violations, _ = _scan_scripts_imports(
            [("app/fake.py", "from scripts import fetch_xhs_following\n")],
            whitelist={"app/fake.py": "scripts.fetch_xhs_following"},
        )
        assert any("模块顶层" in item for item in violations), (
            "白名单文件里放到模块顶层的 import 必须被判定为违规。"
        )

    def test_reports_stale_whitelist(self) -> None:
        violations, _ = _scan_scripts_imports(
            [("app/fake.py", "import os\n")],
            whitelist={"app/fake.py": "scripts.fetch_xhs_following"},
        )
        assert any("白名单已过期" in item for item in violations), (
            "白名单文件已不再 import scripts 时，应提示删除过期条目。"
        )

    def test_router_db_import_detection(self) -> None:
        coupled = _router_db_coupled_files(
            [
                ("app/routers/a.py", "from sqlalchemy import select\n"),
                ("app/routers/b.py", "from app.models.tag import Tag\n"),
                ("app/routers/c.py", "from fastapi import APIRouter\n"),
            ]
        )
        assert coupled == ["app/routers/a.py", "app/routers/b.py"]
