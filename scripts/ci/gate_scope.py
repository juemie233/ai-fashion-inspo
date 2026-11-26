#!/usr/bin/env python3
"""门禁改动范围识别：按本次改动的文件决定 15 项门禁里哪些需要跑。

背景
----
`scripts/ci/test.sh` 原先无条件跑全部 15 项：只改后端一个 service 也会跑
vue-tsc / vitest / jest，本地一次门禁 8~10 分钟，其中大半与改动无关。
本脚本把「改动文件 → 作用域 → 需要执行的门禁步骤」的映射收敛到一处，
供本地门禁与 GitHub Actions 共用，避免两边各写一套判断而漂移。

安全原则
--------
1. 只做「按路径裁剪」，不做「按概率跳过」：命中后端 app 就必然跑后端全套。
2. 无法识别的文件（仓库根新增文件、未知目录）一律**保守回退全量**。
3. 工作区未提交/未跟踪文件也计入（本地门禁常在提交前跑）。
4. `--all` 永远可强制全量；CI 的 workflow_dispatch 也走全量。

用法
----
    # 本地（默认对比 origin/master + 工作区改动）
    python scripts/ci/gate_scope.py
    # 只看识别结果，不跑门禁（test.sh --dry-run 同义）
    python scripts/ci/gate_scope.py --format=json
    # 指定基准 ref / 只看暂存区 / 显式文件列表
    python scripts/ci/gate_scope.py --base=origin/master
    python scripts/ci/gate_scope.py --staged
    python scripts/ci/gate_scope.py --files=backend/app/main.py,web/src/App.vue
    # CI 用：输出 GitHub Actions outputs
    python scripts/ci/gate_scope.py --base=abc123 --no-worktree --format=github-output
    # 自检（门禁第 7 项会跑）
    python scripts/ci/gate_scope.py --self-test
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

STEP_NAMES: dict[int, str] = {
    1: "后端 pytest 日常集 + 覆盖率棘轮 + 关键模块地板",
    2: "后端 ruff",
    3: "后端 vulture",
    4: "后端 mypy 棘轮",
    5: "迁移链自检",
    6: "库结构漂移",
    7: "脚本规范（含门禁自检）",
    8: "前端 vue-tsc",
    9: "前端 vitest",
    10: "前端 eslint",
    11: "前端死代码基线",
    12: "前端格式",
    13: "移动端 tsc + jest",
    14: "子项目最低测试",
    15: "文档生成物一致性",
}
ALL_STEPS = frozenset(STEP_NAMES)

TAG_NAMES: dict[str, str] = {
    "backend_tests": "backend/tests",
    "backend_app": "backend/app",
    "backend_db": "backend/alembic 或 models/database",
    "backend_req": "backend 依赖清单",
    "backend_test_config": "backend 测试配置",
    "backend_tooling": "backend 工具配置",
    "backend_scripts": "backend/scripts",
    "backend_misc": "backend 其它文件",
    "rust": "backend/rust（Rust 试点）",
    "web": "web/",
    "mobile": "mobile/",
    "shared": "shared/",
    "browser_ext": "browser-extension/",
    "face_service": "face-service/",
    "docs": "docs/",
    "scripts": "scripts/",
    "ci_config": ".github/ 或 scripts/ci/",
    "root_docs": "仓库说明文档",
    "tooling": "仓库工具配置",
    "unknown": "无法识别的文件",
}

# 每个作用域会触发的门禁步骤。原则：宁可多跑一项，也不漏掉会被改动影响的门。
TAG_STEPS: dict[str, frozenset[int]] = {
    "backend_tests": frozenset({1}),
    "backend_app": frozenset({1, 2, 3, 4, 15}),
    "backend_db": frozenset({1, 2, 3, 4, 5, 6, 15}),
    "backend_req": frozenset({1, 2, 3, 4, 5, 6, 15}),
    "backend_test_config": frozenset({1}),
    "backend_tooling": frozenset({2, 3, 4}),
    "backend_scripts": frozenset({2, 7}),
    "backend_misc": frozenset(),
    "rust": frozenset(),
    "web": frozenset({8, 9, 10, 11, 12}),
    "shared": frozenset({8, 9, 10, 13}),
    "mobile": frozenset({13}),
    "browser_ext": frozenset({14}),
    "face_service": frozenset({14}),
    "docs": frozenset({15}),
    "scripts": frozenset({2, 7}),
    "ci_config": frozenset({7}),
    "root_docs": frozenset(),
    "tooling": frozenset(),
}

_BACKEND_GROUPS = frozenset({"backend_tests", "backend_app", "backend_db", "backend_req", "backend_tooling"})
_SCRIPT_GROUPS = frozenset({"scripts", "backend_scripts", "ci_config"})
_WEB_GROUPS = frozenset({"web", "shared"})
_MOBILE_GROUPS = frozenset({"mobile", "shared"})
_SUB_GROUPS = frozenset({"browser_ext", "face_service"})

_ROOT_DOC_FILES = frozenset({"CLAUDE.md", "README.md", "README.en.md", "TODO.md", "LICENSE"})
_TOOLING_FILES = frozenset(
    {".gitignore", ".gitattributes", ".mailmap", ".dsh-ignore", ".editorconfig", ".prettierrc"}
)


def classify(path: str) -> str:
    """把一个仓库相对路径归类到作用域标签（顺序敏感：先匹配更具体的目录）。

    Args:
        path: 仓库相对路径（允许反斜杠，函数内部统一成正斜杠）。

    Returns:
        作用域标签，取值见 ``TAG_NAMES``。
    """
    p = path.replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    if not p:
        return "unknown"
    if p.startswith("backend/rust/"):
        return "rust"
    if p.startswith("backend/tests/"):
        return "backend_tests"
    if p.startswith("backend/alembic/"):
        return "backend_db"
    if p.startswith("backend/app/models/"):
        return "backend_db"
    if p in {"backend/app/database.py", "backend/app/db_migrations.py"}:
        return "backend_db"
    if p.startswith("backend/app/"):
        return "backend_app"
    if p.startswith("backend/scripts/"):
        return "backend_scripts"
    if p in {"backend/requirements.txt", "backend/requirements-dev.txt"}:
        return "backend_req"
    if p in {"backend/pytest.ini", "backend/.coveragerc"}:
        return "backend_test_config"
    if p == "backend/pyproject.toml":
        return "backend_tooling"
    if p.startswith("backend/"):
        return "backend_misc"
    if p.startswith("web/"):
        return "web"
    if p.startswith("mobile/"):
        return "mobile"
    if p.startswith("shared/"):
        return "shared"
    if p.startswith("browser-extension/"):
        return "browser_ext"
    if p.startswith("face-service/"):
        return "face_service"
    if p.startswith("docs/"):
        return "docs"
    if p.startswith("scripts/"):
        return "scripts"
    if p.startswith(".github/"):
        return "ci_config"
    if p in _ROOT_DOC_FILES:
        return "root_docs"
    if p.startswith((".claude/", ".vscode/", ".idea/")):
        return "tooling"
    if p in _TOOLING_FILES:
        return "tooling"
    return "unknown"


@dataclass
class Plan:
    """一次门禁执行计划。"""

    changed_files: list[str] = field(default_factory=list)
    tags: dict[str, list[str]] = field(default_factory=dict)
    steps: set[int] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)
    conservative: bool = False
    full: bool = False
    empty: bool = False

    def github_outputs(self) -> dict[str, bool]:
        """映射成 CI 的 job 开关（与本地 15 步分开，避免脚本改动拉起整个后端依赖安装）。"""
        tags = set(self.tags)
        if self.full or self.conservative:
            return {
                "backend": True,
                "web": True,
                "mobile": True,
                "subprojects": True,
                "browser_ext": True,
                "face_service": True,
                "scripts": False,
                "docs": False,
                "full": True,
                "empty": False,
            }
        has_backend = bool(tags & _BACKEND_GROUPS)
        has_scripts = bool(tags & _SCRIPT_GROUPS)
        has_web = bool(tags & _WEB_GROUPS)
        has_mobile = bool(tags & _MOBILE_GROUPS)
        has_sub = bool(tags & _SUB_GROUPS)
        has_docs = "docs" in tags
        return {
            "backend": has_backend,
            "web": has_web,
            "mobile": has_mobile,
            "subprojects": has_sub,
            "browser_ext": "browser_ext" in tags,
            "face_service": "face_service" in tags,
            # 轻量脚本 job：后端 job 没被触发时才单独跑，避免为脚本改动装一整套后端依赖
            "scripts": has_scripts and not has_backend,
            # 文档生成物一致性很便宜（纯 AST），后端 job 已包含第 15 步时不再单独跑
            "docs": has_docs and not has_backend,
            "full": False,
            "empty": self.empty,
        }


def build_plan(paths: list[str], *, force_all: bool = False) -> Plan:
    """由改动文件列表构建执行计划。

    Args:
        paths: 仓库相对路径列表。
        force_all: 为 True 时忽略路径，直接全量。

    Returns:
        Plan 实例；``unknown`` 作用域会触发 conservative 全量并附警告。
    """
    if force_all:
        return Plan(changed_files=list(paths), steps=set(ALL_STEPS), full=True)
    if not paths:
        return Plan(empty=True)

    tags: dict[str, list[str]] = {}
    for path in paths:
        tags.setdefault(classify(path), []).append(path)

    plan = Plan(changed_files=list(paths), tags=tags)
    steps: set[int] = set()
    for tag in tags:
        steps |= set(TAG_STEPS.get(tag, frozenset()))
    plan.steps = steps

    if "unknown" in tags:
        plan.conservative = True
        plan.steps = set(ALL_STEPS)
        plan.warnings.append(
            "检测到无法识别的文件，保守回退全量门禁："
            + "、".join(tags["unknown"][:5])
            + (" 等" if len(tags["unknown"]) > 5 else "")
        )
    if "rust" in tags:
        plan.warnings.append(
            "backend/rust（Rust 试点）不在 15 项门禁内：如有改动请手动 cargo test / clippy"
            "（见 backend/rust/media-worker/README.md）"
        )
    if "backend_misc" in tags:
        plan.warnings.append(
            "backend 其它文件（如 .env.example / model_configs.json）没有自动门禁："
            "请人工确认是否需要同步文档或配置项参考"
        )
    return plan


def _human_scope(tags: set[str]) -> str:
    return "、".join(TAG_NAMES[t] for t in sorted(tags) if t in TAG_NAMES)


def skip_reason(step: int) -> str:
    """生成「本步骤为什么被跳过」的中文原因。"""
    trigger_tags = {tag for tag, steps in TAG_STEPS.items() if step in steps}
    return "本次改动不涉及" + _human_scope(trigger_tags)


# ── git 改动采集 ────────────────────────────────────────────────────────


def _git(*args: str) -> str:
    # core.quotepath=false：中文路径直接按 UTF-8 输出，不转成 "\346\226\207" 这种
    # C 风格转义（否则 classify 认不出目录，会误判成 unknown 而回退全量）。
    result = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "git 命令失败").strip())
    return result.stdout


def _decode_git_path(token: str) -> str:
    """解码 git 的 C 风格路径转义（文件名含引号/换行等极端情况兜底）。

    ``core.quotepath=false`` 只解决非 ASCII 路径；含双引号/控制字符的文件名仍会被
    整体加引号并用 ``\\ooo`` 八进制转义。这里按字节还原再按 UTF-8 解码。
    """
    if len(token) < 2 or not (token.startswith('"') and token.endswith('"')):
        return token
    body = token[1:-1]
    out = bytearray()
    i = 0
    simple = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11, '"': 34, "\\": 92}
    while i < len(body):
        char = body[i]
        if char == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt in "01234567":
                out.append(int(body[i + 1 : i + 4], 8))
                i += 4
                continue
            if nxt in simple:
                out.append(simple[nxt])
                i += 2
                continue
            i += 2
            continue
        out.extend(char.encode("utf-8"))
        i += 1
    return out.decode("utf-8", "replace")


def _split_lines(text: str) -> list[str]:
    return [_decode_git_path(line.strip()) for line in text.splitlines() if line.strip()]


def detect_base(explicit: str | None) -> str | None:
    """探测对比基准 ref；都找不到时返回 None（仅工作区模式）。"""
    candidates = [explicit] if explicit else ["origin/master", "origin/main"]
    for ref in candidates:
        if ref is None:
            continue
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return ref
    return None


def collect_changed_files(
    *, base: str | None = None, staged: bool = False, include_worktree: bool = True
) -> list[str]:
    """采集改动文件。

    Args:
        base: 对比基准 ref；None 表示只采集暂存区/工作区。
        staged: 只采集已暂存改动。
        include_worktree: 是否包含未暂存与未跟踪文件（本地门禁默认包含）。

    Returns:
        去重后的仓库相对路径列表。
    """
    files: list[str] = []
    if staged:
        files += _split_lines(_git("diff", "--cached", "--name-only", "--no-renames"))
        return sorted(set(files))
    if base is not None:
        files += _split_lines(_git("diff", "--name-only", "--no-renames", f"{base}...HEAD"))
    if include_worktree:
        files += _split_lines(_git("diff", "--name-only", "--no-renames", "HEAD"))
        files += _split_lines(_git("ls-files", "--others", "--exclude-standard"))
    return sorted(set(files))


# ── 同状态缓存 ──────────────────────────────────────────────────────────


def collect_cache_inputs() -> tuple[str, str, dict[str, str]]:
    """采集「当前工作状态」的缓存输入。

    Returns:
        ``(HEAD, 完整 diff 文本, {未跟踪文件路径: 内容 sha256})``。
        已跟踪文件的改动由 ``git diff HEAD --binary`` 覆盖（含暂存/未暂存），
        未跟踪文件逐个算内容摘要（只看文件名不够，同名文件内容也会变）。
    """
    head = _git("rev-parse", "HEAD").strip()
    diff = _git("diff", "HEAD", "--binary")
    untracked: dict[str, str] = {}
    for path in _split_lines(_git("ls-files", "--others", "--exclude-standard")):
        try:
            data = (ROOT / path).read_bytes()
        except OSError:
            data = b"<unreadable>"
        untracked[path] = hashlib.sha256(data).hexdigest()
    return head, diff, untracked


def compute_cache_key(
    head: str,
    diff: str,
    untracked: dict[str, str],
    extra: dict[str, str] | None = None,
) -> str:
    """计算同状态缓存键（纯函数，便于单测）。

    相同 HEAD + 相同改动内容 + 相同门禁实现版本 ⇒ 键相同，可直接复用上次
    「全部通过」的结论；任一文件内容变化都会换键，不会误跳过。
    """
    digest = hashlib.sha256()
    digest.update(b"fashion-gate-cache-v1\0")
    digest.update(head.encode("utf-8", "replace"))
    digest.update(b"\0")
    digest.update(diff.encode("utf-8", "replace"))
    digest.update(b"\0")
    for path in sorted(untracked):
        digest.update(path.encode("utf-8", "replace"))
        digest.update(b"\0")
        digest.update(untracked[path].encode("utf-8", "replace"))
        digest.update(b"\0")
    for key in sorted(extra or {}):
        digest.update(key.encode("utf-8", "replace"))
        digest.update(b"=")
        digest.update(str((extra or {})[key]).encode("utf-8", "replace"))
        digest.update(b"\0")
    return digest.hexdigest()


def _impl_hash() -> str:
    """门禁实现自身的指纹：改了 test.sh / gate_scope.py 就让旧缓存失效。"""
    digest = hashlib.sha256()
    for rel in ("scripts/ci/test.sh", "scripts/ci/gate_scope.py"):
        try:
            digest.update((ROOT / rel).read_bytes())
        except OSError:
            digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()[:32]


def current_cache_key() -> str:
    """读当前仓库状态并算出缓存键（CLI ``--cache-key`` 用）。"""
    head, diff, untracked = collect_cache_inputs()
    return compute_cache_key(head, diff, untracked, {"impl": _impl_hash()})


# ── 输出 ────────────────────────────────────────────────────────────────


def _bash_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def render_bash(plan: Plan) -> str:
    """输出可被 ``eval`` 消费的变量：GATE_STEP_n / GATE_NAME_n / GATE_SKIP_REASON_n。"""
    lines = [
        "GATE_FULL=1" if plan.full else "GATE_FULL=0",
        "GATE_CONSERVATIVE=1" if plan.conservative else "GATE_CONSERVATIVE=0",
        "GATE_EMPTY=1" if plan.empty else "GATE_EMPTY=0",
        f"GATE_CHANGED_COUNT={len(plan.changed_files)}",
        f"GATE_RUN_COUNT={len(plan.steps)}",
    ]
    for step, name in STEP_NAMES.items():
        run = step in plan.steps
        reason = "" if run else (skip_reason(step) if not plan.empty else "本次没有待检查改动")
        lines.append(f"GATE_STEP_{step}={'1' if run else '0'}")
        lines.append(f"GATE_NAME_{step}={_bash_quote(name)}")
        lines.append(f"GATE_SKIP_REASON_{step}={_bash_quote(reason)}")
    warnings = "\n".join(plan.warnings)
    lines.append(f"GATE_WARNINGS={_bash_quote(warnings)}")
    return "\n".join(lines)


def render_text(plan: Plan) -> str:
    """人类可读的计划说明（--dry-run 直接打印这份）。"""
    if plan.empty:
        return (
            "未检测到待检查改动（相对基准无提交、工作区/暂存区也没有改动）。\n"
            "如需强制全量门禁：bash scripts/ci/test.sh --all"
        )
    lines: list[str] = []
    lines.append(f"改动文件：{len(plan.changed_files)} 个")
    for path in plan.changed_files[:40]:
        lines.append(f"  - {path}")
    if len(plan.changed_files) > 40:
        lines.append(f"  - ……其余 {len(plan.changed_files) - 40} 个")
    lines.append("")
    lines.append(f"执行 {len(plan.steps)}/15 项门禁：")
    for step in sorted(plan.steps):
        lines.append(f"  [√] {step:>2} {STEP_NAMES[step]}")
    skipped = set(STEP_NAMES) - plan.steps
    if skipped:
        lines.append(f"跳过 {len(skipped)} 项：")
        for step in sorted(skipped):
            lines.append(f"  [ ] {step:>2} {STEP_NAMES[step]}（{skip_reason(step)}）")
    if plan.warnings:
        lines.append("")
        lines.append("提示：")
        for warning in plan.warnings:
            lines.append(f"  ⚠ {warning}")
    return "\n".join(lines)


def render_json(plan: Plan) -> str:
    """机器可读输出。"""
    return json.dumps(
        {
            "changed_files": plan.changed_files,
            "tags": plan.tags,
            "steps": [
                {
                    "id": step,
                    "name": STEP_NAMES[step],
                    "run": step in plan.steps,
                    "reason": "" if step in plan.steps else skip_reason(step),
                }
                for step in sorted(STEP_NAMES)
            ],
            "warnings": plan.warnings,
            "conservative": plan.conservative,
            "full": plan.full,
            "empty": plan.empty,
            "github_outputs": plan.github_outputs(),
        },
        ensure_ascii=False,
        indent=2,
    )


def render_github_output(plan: Plan) -> str:
    """GitHub Actions outputs（追加到 $GITHUB_OUTPUT）。"""
    return "\n".join(f"{key}={str(value).lower()}" for key, value in plan.github_outputs().items())


# ── 自检 ────────────────────────────────────────────────────────────────


def self_test() -> int:
    """内置断言：分类映射与典型场景的门禁计划。返回失败数。"""
    failures: list[str] = []
    checks = 0

    def check(name: str, actual: object, expected: object) -> None:
        nonlocal checks
        checks += 1
        if actual != expected:
            failures.append(f"{name}: 期望 {expected!r}，实际 {actual!r}")

    cases = {
        "backend/app/main.py": "backend_app",
        "backend/app/models/inspiration.py": "backend_db",
        "backend/app/database.py": "backend_db",
        "backend/alembic/versions/x.py": "backend_db",
        "backend/tests/test_unit_x.py": "backend_tests",
        "backend/tests/fixtures/api_contract.json": "backend_tests",
        "backend/scripts/check_x.py": "backend_scripts",
        "backend/requirements.txt": "backend_req",
        "backend/requirements-dev.txt": "backend_req",
        "backend/pytest.ini": "backend_test_config",
        "backend/pyproject.toml": "backend_tooling",
        "backend/.env.example": "backend_misc",
        "backend/rust/media-worker/src/main.rs": "rust",
        "web/src/App.vue": "web",
        "mobile/app/index.tsx": "mobile",
        "shared/types/api.ts": "shared",
        "browser-extension/background.js": "browser_ext",
        "face-service/test_storage.py": "face_service",
        "docs/overview/API参考.md": "docs",
        "scripts/ci/test.sh": "scripts",
        ".github/workflows/ci.yml": "ci_config",
        "./CLAUDE.md": "root_docs",
        "README.md": "root_docs",
        ".gitignore": "tooling",
        "daily-commit-types.sh": "unknown",
        "some-new-root-file.yaml": "unknown",
    }
    for path, tag in cases.items():
        check(f"classify({path})", classify(path), tag)

    def steps(paths: list[str], force_all: bool = False) -> set[int]:
        return build_plan(paths, force_all=force_all).steps

    check("backend-app", steps(["backend/app/main.py"]), {1, 2, 3, 4, 15})
    check("backend-model", steps(["backend/app/models/x.py"]), {1, 2, 3, 4, 5, 6, 15})
    check("backend-tests-only", steps(["backend/tests/test_x.py"]), {1})
    check("backend-req", steps(["backend/requirements.txt"]), {1, 2, 3, 4, 5, 6, 15})
    check("tests-config", steps(["backend/pytest.ini"]), {1})
    check("backend-tooling", steps(["backend/pyproject.toml"]), {2, 3, 4})
    check("backend-scripts", steps(["backend/scripts/x.py"]), {2, 7})
    check("web-only", steps(["web/src/App.vue"]), {8, 9, 10, 11, 12})
    check("mobile-only", steps(["mobile/app/x.tsx"]), {13})
    check("shared", steps(["shared/types/api.ts"]), {8, 9, 10, 13})
    check("subprojects", steps(["browser-extension/x.js"]), {14})
    check("docs-only", steps(["docs/overview/API参考.md"]), {15})
    check("scripts-only", steps(["scripts/ci/test.sh"]), {2, 7})
    check("ci-config", steps([".github/workflows/ci.yml"]), {7})
    check("root-docs", steps(["README.md"]), set())
    check("rust-only", steps(["backend/rust/media-worker/src/main.rs"]), set())
    check("unknown-is-full", steps(["mystery.bin"]), set(STEP_NAMES))
    check("force-all", steps([], force_all=True), set(STEP_NAMES))

    empty_plan = build_plan([])
    check("empty", empty_plan.steps, set())
    check("empty_plan.github", empty_plan.github_outputs()["backend"], False)

    # 中文路径曾被 git 转义成 "\346\226\207" 导致整份计划误判 unknown → 全量
    check("decode-octal", _decode_git_path('"docs/\\346\\226\\207.md"'), "docs/文.md")
    check("decode-quote", _decode_git_path('"a\\"b.txt"'), 'a"b.txt')
    check("decode-plain", _decode_git_path("web/src/App.vue"), "web/src/App.vue")

    plan = build_plan(["backend/app/main.py", "web/src/App.vue"])
    check("mixed-backend-web", plan.steps, {1, 2, 3, 4, 8, 9, 10, 11, 12, 15})
    check("mixed.github-backend", plan.github_outputs()["backend"], True)
    check("mixed.github-web", plan.github_outputs()["web"], True)
    check("mixed.github-mobile", plan.github_outputs()["mobile"], False)

    web_plan = build_plan(["web/src/App.vue"])
    check("web.github-backend", web_plan.github_outputs()["backend"], False)
    check("web.github-web", web_plan.github_outputs()["web"], True)
    check("web.github-scripts", web_plan.github_outputs()["scripts"], False)

    scripts_plan = build_plan(["scripts/ci/test.sh"])
    check("scripts.github-scripts", scripts_plan.github_outputs()["scripts"], True)
    check("scripts.github-backend", scripts_plan.github_outputs()["backend"], False)

    docs_plan = build_plan(["docs/overview/API参考.md"])
    check("docs.github-docs", docs_plan.github_outputs()["docs"], True)
    check("docs.github-backend", docs_plan.github_outputs()["backend"], False)

    unknown_plan = build_plan(["mystery.bin"])
    check("unknown.conservative", unknown_plan.conservative, True)
    check("unknown.warning", len(unknown_plan.warnings) > 0, True)

    cache_key = compute_cache_key("head", "diff", {"new.txt": "a" * 64}, {"impl": "v1"})
    check("cache-stable", compute_cache_key("head", "diff", {"new.txt": "a" * 64}, {"impl": "v1"}), cache_key)
    check(
        "cache-head-changes",
        compute_cache_key("head2", "diff", {"new.txt": "a" * 64}, {"impl": "v1"}) != cache_key,
        True,
    )
    check(
        "cache-diff-changes",
        compute_cache_key("head", "diff2", {"new.txt": "a" * 64}, {"impl": "v1"}) != cache_key,
        True,
    )
    check(
        "cache-untracked-changes",
        compute_cache_key("head", "diff", {"new.txt": "b" * 64}, {"impl": "v1"}) != cache_key,
        True,
    )
    check(
        "cache-impl-changes",
        compute_cache_key("head", "diff", {"new.txt": "a" * 64}, {"impl": "v2"}) != cache_key,
        True,
    )

    if failures:
        print("门禁计划自检失败：")
        for line in failures:
            print(f"  ✗ {line}")
        return len(failures)
    print(f"门禁计划自检通过（{checks} 项断言）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="按本次改动范围决定 15 项质量门禁中哪些需要执行（只读，不跑门禁）"
    )
    parser.add_argument("--base", default=None, help="对比基准 ref，默认 origin/master → origin/main → 仅工作区")
    parser.add_argument("--staged", action="store_true", help="只检查 staged 改动（pre-commit 场景）")
    parser.add_argument("--no-worktree", action="store_true", help="不把未暂存/未跟踪文件计入（CI 用）")
    parser.add_argument("--files", default=None, help="显式改动文件，逗号分隔（跳过 git diff）")
    parser.add_argument("--all", action="store_true", help="强制全量 15 项")
    parser.add_argument(
        "--format",
        choices=("text", "bash", "json", "github-output"),
        default="text",
        help="输出格式：text 供人读；bash 供 test.sh eval；json / github-output 给 CI",
    )
    parser.add_argument("--self-test", action="store_true", help="跑内置分类/计划断言后退出")
    parser.add_argument(
        "--cache-key",
        action="store_true",
        help="打印当前工作状态的缓存键后退出（test.sh 判断「同一状态已通过」用）",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.cache_key:
        print(current_cache_key())
        return 0

    warnings: list[str] = []
    if args.files:
        paths = [part.strip() for part in args.files.split(",") if part.strip()]
        plan = build_plan(paths, force_all=args.all)
    else:
        base = None if args.all else detect_base(args.base)
        if base is None and not args.all:
            warnings.append("未找到 origin/master / origin/main，退化为「只看工作区改动」")
        try:
            paths = collect_changed_files(
                base=base, staged=args.staged, include_worktree=not args.no_worktree
            )
        except RuntimeError as exc:
            paths = []
            warnings.append(f"采集 git 改动失败，保守回退全量：{exc}")
        plan = build_plan(paths, force_all=args.all)
    plan.warnings = warnings + plan.warnings

    if args.format == "json":
        print(render_json(plan))
    elif args.format == "bash":
        print(render_bash(plan))
    elif args.format == "github-output":
        print(render_github_output(plan))
    else:
        print(render_text(plan))
    return 0


if __name__ == "__main__":
    sys.exit(main())
