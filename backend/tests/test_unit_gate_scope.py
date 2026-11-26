"""门禁改动范围识别（``scripts/ci/gate_scope.py``）的单元测试。

这个脚本决定「本地/CI 要跑哪几项门禁」，判断错一次就可能把该跑的质量门漏掉
（或退回每次全量、失去优化意义）。所以把作用域映射、典型场景计划、保守回退
与 CI job 开关都固化成用例；脚本自身也带 ``--self-test``，供门禁第 7 步在
「只改 scripts」时也能自检。
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "gate_scope", ROOT / "scripts" / "ci" / "gate_scope.py"
)
assert _SPEC is not None and _SPEC.loader is not None
gate_scope = importlib.util.module_from_spec(_SPEC)
# dataclass 在运行时按 __module__ 反查模块，必须先注册再 exec_module
sys.modules["gate_scope"] = gate_scope
_SPEC.loader.exec_module(gate_scope)


def test_classify_backend_scopes():
    assert gate_scope.classify("backend/app/main.py") == "backend_app"
    assert gate_scope.classify("backend/app/models/inspiration.py") == "backend_db"
    assert gate_scope.classify("backend/app/database.py") == "backend_db"
    assert gate_scope.classify("backend/alembic/versions/x.py") == "backend_db"
    assert gate_scope.classify("backend/tests/test_unit_x.py") == "backend_tests"
    assert gate_scope.classify("backend/scripts/check_x.py") == "backend_scripts"
    assert gate_scope.classify("backend/requirements-dev.txt") == "backend_req"
    assert gate_scope.classify("backend/pytest.ini") == "backend_test_config"
    assert gate_scope.classify("backend/pyproject.toml") == "backend_tooling"
    assert gate_scope.classify("backend/.env.example") == "backend_misc"
    assert gate_scope.classify("backend/rust/media-worker/src/main.rs") == "rust"


def test_classify_frontend_and_other_scopes():
    assert gate_scope.classify("web/src/App.vue") == "web"
    assert gate_scope.classify("mobile/app/index.tsx") == "mobile"
    assert gate_scope.classify("shared/types/api.ts") == "shared"
    assert gate_scope.classify("browser-extension/background.js") == "browser_ext"
    assert gate_scope.classify("face-service/test_storage.py") == "face_service"
    assert gate_scope.classify("docs/overview/API参考.md") == "docs"
    assert gate_scope.classify("scripts/ci/test.sh") == "scripts"
    assert gate_scope.classify(".github/workflows/ci.yml") == "ci_config"
    assert gate_scope.classify("CLAUDE.md") == "root_docs"
    assert gate_scope.classify(".gitignore") == "tooling"
    # 未识别路径必须归 unknown，由计划层保守回退全量
    assert gate_scope.classify("mystery-root-file.bin") == "unknown"


def test_decode_git_path_handles_octal_unicode_and_quotes():
    """git 会把中文路径转义成 \\346\\226\\207；不还原会整份计划误判 unknown。"""
    assert gate_scope._decode_git_path('"docs/\\346\\226\\207.md"') == "docs/文.md"
    assert gate_scope._decode_git_path('"a\\"b.txt"') == 'a"b.txt'
    assert gate_scope._decode_git_path("web/src/App.vue") == "web/src/App.vue"


def test_compute_cache_key_is_stable_and_sensitive_to_each_input():
    """同状态缓存键：任一输入变化都必须换键，避免把旧结论误当成新状态。"""
    base = gate_scope.compute_cache_key(
        "head", "diff", {"new.txt": "a" * 64}, {"impl": "v1"}
    )

    assert (
        gate_scope.compute_cache_key("head", "diff", {"new.txt": "a" * 64}, {"impl": "v1"})
        == base
    )
    assert gate_scope.compute_cache_key("head2", "diff", {"new.txt": "a" * 64}, {"impl": "v1"}) != base
    assert gate_scope.compute_cache_key("head", "diff2", {"new.txt": "a" * 64}, {"impl": "v1"}) != base
    assert gate_scope.compute_cache_key("head", "diff", {"new.txt": "b" * 64}, {"impl": "v1"}) != base
    assert gate_scope.compute_cache_key("head", "diff", {"new.txt": "a" * 64}, {"impl": "v2"}) != base
    # 未跟踪文件路径也要参与键：重命名但内容相同同样应视为状态变化
    assert gate_scope.compute_cache_key("head", "diff", {"other.txt": "a" * 64}, {"impl": "v1"}) != base


def test_plan_backend_app_runs_core_steps_only():
    plan = gate_scope.build_plan(["backend/app/main.py"])

    assert plan.steps == {1, 2, 3, 4, 15}
    assert plan.conservative is False


def test_plan_backend_models_also_runs_db_steps():
    plan = gate_scope.build_plan(["backend/app/models/inspiration.py"])

    assert plan.steps == {1, 2, 3, 4, 5, 6, 15}


def test_plan_backend_tests_only_runs_pytest():
    assert gate_scope.build_plan(["backend/tests/test_unit_x.py"]).steps == {1}


def test_plan_web_only_never_runs_backend_or_mobile():
    plan = gate_scope.build_plan(["web/src/App.vue"])

    assert plan.steps == {8, 9, 10, 11, 12}
    assert 1 not in plan.steps and 13 not in plan.steps


def test_plan_shared_types_fan_out_to_web_and_mobile():
    assert gate_scope.build_plan(["shared/types/api.ts"]).steps == {8, 9, 10, 13}


def test_plan_docs_only_runs_docs_step():
    assert gate_scope.build_plan(["docs/overview/API参考.md"]).steps == {15}


def test_plan_scripts_only_runs_ruff_and_script_check():
    assert gate_scope.build_plan(["scripts/ci/test.sh"]).steps == {2, 7}
    assert gate_scope.build_plan(["backend/scripts/x.py"]).steps == {2, 7}


def test_plan_unknown_file_falls_back_to_full_and_warns():
    plan = gate_scope.build_plan(["mystery-root-file.bin"])

    assert plan.steps == set(gate_scope.STEP_NAMES)
    assert plan.conservative is True
    assert plan.warnings


def test_plan_force_all_overrides_even_empty_changes():
    plan = gate_scope.build_plan([], force_all=True)

    assert plan.steps == set(gate_scope.STEP_NAMES)
    assert plan.full is True


def test_plan_empty_changes_runs_nothing_and_is_not_full():
    plan = gate_scope.build_plan([])

    assert plan.steps == set()
    assert plan.empty is True
    assert plan.full is False
    assert plan.github_outputs()["backend"] is False


def test_github_outputs_backend_does_not_trigger_scripts_or_docs_jobs():
    plan = gate_scope.build_plan(["backend/app/main.py"])
    outputs = plan.github_outputs()

    assert outputs["backend"] is True
    assert outputs["scripts"] is False
    assert outputs["docs"] is False
    assert outputs["web"] is False


def test_github_outputs_scripts_only_uses_light_scripts_job():
    outputs = gate_scope.build_plan(["scripts/ci/test.sh"]).github_outputs()

    assert outputs["scripts"] is True
    assert outputs["backend"] is False


def test_github_outputs_docs_only_uses_docs_job():
    outputs = gate_scope.build_plan(["docs/overview/API参考.md"]).github_outputs()

    assert outputs["docs"] is True
    assert outputs["backend"] is False


def test_github_outputs_subprojects_split_by_component():
    outputs = gate_scope.build_plan(["browser-extension/background.js"]).github_outputs()
    assert outputs["subprojects"] is True
    assert outputs["browser_ext"] is True
    assert outputs["face_service"] is False

    outputs = gate_scope.build_plan(["face-service/test_storage.py"]).github_outputs()
    assert outputs["subprojects"] is True
    assert outputs["browser_ext"] is False
    assert outputs["face_service"] is True


def test_github_outputs_force_full_runs_all_jobs():
    outputs = gate_scope.build_plan([], force_all=True).github_outputs()

    assert outputs["full"] is True
    assert outputs["backend"] is True
    assert outputs["web"] is True
    assert outputs["mobile"] is True
    assert outputs["subprojects"] is True


def test_render_bash_is_eval_safe_and_contains_all_steps():
    plan = gate_scope.build_plan(["web/src/App.vue"])
    rendered = gate_scope.render_bash(plan)
    namespace: dict[str, object] = {}
    exec(rendered, namespace)  # noqa: S102 - 解析自家输出，验证 bash eval 写法可用

    assert int(namespace["GATE_STEP_9"]) == 1  # bash 里是字符串，Python exec 会当数字
    assert int(namespace["GATE_STEP_1"]) == 0
    assert int(namespace["GATE_RUN_COUNT"]) == 5
    for step in gate_scope.STEP_NAMES:
        assert f"GATE_STEP_{step}=" in rendered
        assert f"GATE_NAME_{step}=" in rendered


def test_render_text_lists_run_and_skip_reasons():
    text = gate_scope.render_text(gate_scope.build_plan(["backend/tests/test_x.py"]))

    assert "执行 1/15 项门禁" in text
    assert "跳过 14 项" in text
    assert "后端 pytest" in text


def test_self_test_passes():
    assert gate_scope.self_test() == 0
