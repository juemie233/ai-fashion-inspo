#!/usr/bin/env bash
# 一键跑质量门禁（与 .github/workflows/ci.yml 对齐：本地先绿，再推）。
#
# 默认是**按改动范围裁剪**：只改后端就只跑后端与文档生成物，不跑前端/移动端；
# 只改 docs 就只跑第 15 项。识别逻辑收敛在 scripts/ci/gate_scope.py（有单元测试），
# 无法识别的文件会保守回退全量，`--all` 永远可强制跑满 15 项。
#
# 用法：
#   bash scripts/ci/test.sh                 按改动范围执行（默认）
#   bash scripts/ci/test.sh --all           强制全量 15 项（推送前/发版/大改）
#   bash scripts/ci/test.sh --dry-run       只打印「本次会跑哪几项」，不执行
#   bash scripts/ci/test.sh --base=<ref>    指定对比基准（默认 origin/master → origin/main → 工作区）
#   bash scripts/ci/test.sh --staged        只看暂存区（pre-commit 场景）
#   bash scripts/ci/test.sh --files=a,b     显式指定改动文件（排查/脚本化场景）
#   bash scripts/ci/test.sh --cov           后端额外输出行级覆盖率报告（term-missing）
#   bash scripts/ci/test.sh --slow          后端把 slow 长耗时用例加回本次运行（system 仍排除）
#   bash scripts/ci/test.sh --e2e           跑完门禁后再跑 Playwright（需后端在跑，可选/nightly）
#   bash scripts/ci/test.sh --no-cache      忽略「同一状态已通过」缓存，强制重跑
#
# 重复执行抑制（2026-11-26）：
#   - 运行锁：同一时刻只允许一个门禁进程，后来者排队等待（`GATE_NO_WAIT=1` 立即退出，
#     `GATE_LOCK_WAIT=<秒>` 改等待上限）。防止两个后端 pytest --cov 并发互相擦除
#     coverage 数据，出现「测试全过、覆盖率 50%」的假红。
#   - 同状态缓存：HEAD + 工作区内容 + 门禁实现版本完全相同且上次全部通过时，
#     直接秒过（`--no-cache` 或 `--all` 可强制重跑）。
#   - 迭代期间建议只跑定向测试（pytest 单文件 / vitest 单文件），一轮改完在提交前
#     跑一次本门禁即可，不要每改一处就整跑。
#
# 门禁清单（顺序即输出编号）：
#    1. 后端 pytest 日常集（默认 `not slow and not system`，与 CI 一致；`--slow` 时为 `not system`）
#       + 覆盖率棘轮 ≥82.9% + 关键模块地板（临时库/临时存储）
#    2. 后端 ruff（零告警）
#    3. 后端 vulture（死代码，--min-confidence 80 必须 0 条）
#    4. 后端 mypy 棘轮（只查已清零的模块，列表只增不减）
#    5. 迁移链自检（空库 upgrade head → 结构必须与 ORM 一致）
#    6. 真实库漂移（ORM ↔ 现有库；无库环境自动跳过）
#    7. 脚本规范（只读连接 / CLI 入口）+ 门禁计划自检
#    8. 前端 vue-tsc 类型检查
#    9. 前端 vitest
#   10. 前端 eslint（带 --max-warnings 棘轮：只能降不能升，当前 34）
#   11. 前端死代码基线（ts-prune，只拦新增）+ Vue 组件零引用检查
#   12. 前端格式（只查本次改动文件；全库检查会因约 45 个历史文件永远为红）
#   13. 移动端类型检查（tsc）+ 单元测试（jest-expo + RNTL，含覆盖率门槛）
#   14. 子项目最低测试（browser-extension 纯函数；face-service 存储层，无 venv 时跳过）
#   15. 文档生成物与代码一致（docs/overview 的 API参考 / 数据模型 / 配置项参考）
#
# 各步骤的触发条件（改哪类文件会跑哪些项）见 docs/overview/门禁增量执行方案.md。
#
# 依赖：Python 3.12+（可用 PYTHON_BIN 覆盖，如 PYTHON_BIN=py）、Node 20+、web/node_modules。
# 前端一律用 node 直接调用本地二进制，规避 npx/npm 包装脚本在受限 PowerShell 下的执行策略问题。
#
# ⚠️ 「✅」只在最后一行打印：以前它排在两个门禁之前，门禁失败时屏幕上反而先看到「通过」。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="${PYTHON_BIN:-python}"
COV=""
RUN_E2E=0
SLOW=0
DRY_RUN=0
NO_CACHE=0
PLANNER_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --cov) COV="--cov" ;;
    --slow) SLOW=1 ;;
    --e2e) RUN_E2E=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --no-cache) NO_CACHE=1 ;;
    --all|--staged|--no-worktree) PLANNER_ARGS+=("$arg") ;;
    --base=*|--files=*) PLANNER_ARGS+=("$arg") ;;
    *)
      echo "未知参数：$arg" >&2
      echo "支持：--all / --dry-run / --no-cache / --base=<ref> / --staged / --files=a,b / --cov / --slow / --e2e" >&2
      exit 2
      ;;
  esac
done

# ── 改动范围 → 本次执行计划 ─────────────────────────────────────────────
if [[ "$DRY_RUN" == "1" ]]; then
  "$PY" "$ROOT/scripts/ci/gate_scope.py" ${PLANNER_ARGS[@]+"${PLANNER_ARGS[@]}"}
  exit 0
fi

# ── 运行锁：同一时刻只允许一个门禁 ─────────────────────────────────────
# 两个并发门禁（常见于多个会话各自后台跑）会互相擦除 coverage 数据、抢 CPU，
# 导致假红进而触发更多重跑；这里让后来者排队，把并发变成串行。
LOCK_DIR="$ROOT/.git/dsh-gate.lock"
GATE_LOCK_WAIT="${GATE_LOCK_WAIT:-900}"
release_gate_lock() { rm -rf "$LOCK_DIR" 2>/dev/null || true; }
trap release_gate_lock EXIT INT TERM
acquire_gate_lock() {
  local waited=0 other_pid other_started now
  while true; do
    if mkdir "$LOCK_DIR" 2>/dev/null; then
      echo "$$" > "$LOCK_DIR/pid"
      date +%s > "$LOCK_DIR/started"
      return 0
    fi
    other_pid="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
    other_started="$(cat "$LOCK_DIR/started" 2>/dev/null || true)"
    now="$(date +%s)"
    if [[ -z "$other_pid" ]]; then
      # mkdir 与写 pid 之间的瞬时窗口；2 秒后仍无 pid 视为崩溃残留
      sleep 2
      if [[ -z "$(cat "$LOCK_DIR/pid" 2>/dev/null || true)" ]]; then
        echo "⚠️ 清理无主门禁锁" >&2
        rm -rf "$LOCK_DIR"
        continue
      fi
    fi
    if [[ -n "$other_started" ]] && (( now - other_started > 5400 )); then
      echo "⚠️ 发现陈旧门禁锁（超过 90 分钟），清理后继续" >&2
      rm -rf "$LOCK_DIR"
      continue
    fi
    if [[ "${GATE_NO_WAIT:-0}" == "1" ]]; then
      echo "❌ 另一个门禁正在运行（PID ${other_pid:-未知}）。GATE_NO_WAIT=1，直接退出。" >&2
      exit 3
    fi
    if (( waited == 0 )); then
      echo "⏳ 另一个门禁正在运行（PID ${other_pid:-未知}），等待其结束（最多 ${GATE_LOCK_WAIT}s；GATE_NO_WAIT=1 可立即退出）……" >&2
    fi
    sleep 5
    waited=$((waited + 5))
    if (( waited >= GATE_LOCK_WAIT )); then
      echo "❌ 等待门禁锁超时（${GATE_LOCK_WAIT}s）；如确认无其他门禁在跑，可删除 $LOCK_DIR 后重试。" >&2
      exit 3
    fi
    # 对方 bash 已退出却来不及清理锁（被强杀/断电/测试场景）：直接清理。
    # 锁里存的是 bash 自己的 MSYS pid，kill -0 可靠，且持有者退出后 pid 不会
    # 在几秒内被复用，因此不必等 5 分钟。
    if [[ -n "$other_pid" ]] && ! kill -0 "$other_pid" 2>/dev/null; then
      echo "⚠️ 上一个门禁进程已退出（PID ${other_pid}），清理残留锁" >&2
      rm -rf "$LOCK_DIR"
      continue
    fi
  done
}
acquire_gate_lock

if ! PLAN_OUT="$("$PY" "$ROOT/scripts/ci/gate_scope.py" --format=bash ${PLANNER_ARGS[@]+"${PLANNER_ARGS[@]}"} 2>&1)"; then
  # 识别失败时宁可贵一点也要跑全：门禁的价值是拦住问题，不是省时间
  echo "⚠️ 改动范围识别失败，回退全量门禁：" >&2
  echo "$PLAN_OUT" >&2
  PLAN_OUT="$("$PY" "$ROOT/scripts/ci/gate_scope.py" --all --format=bash)"
fi
eval "$PLAN_OUT"

if [[ -n "${GATE_WARNINGS:-}" ]]; then
  echo "⚠️ 门禁范围提示："
  while IFS= read -r line; do [[ -n "$line" ]] && echo "   - $line"; done <<< "$GATE_WARNINGS"
fi

if [[ "${GATE_EMPTY:-0}" == "1" ]]; then
  echo ""
  echo "本次没有检测到待检查改动（相对基准无提交、工作区/暂存区也没有改动）。"
  echo "如需强制全量门禁：bash scripts/ci/test.sh --all"
  exit 0
fi

RUNNING=0
SKIPPED=0
run_header() {
  RUNNING=$((RUNNING + 1))
  echo ""
  echo "===== [${1}/15] ${2} ====="
}
skip_step() {
  SKIPPED=$((SKIPPED + 1))
  local name_var="GATE_NAME_$1" reason_var="GATE_SKIP_REASON_$1"
  echo ""
  echo "----- [${1}/15] 跳过：${!name_var}"
  echo "      原因：${!reason_var}"
}
step_enabled() {
  local var="GATE_STEP_$1"
  [[ "${!var:-1}" == "1" ]]
}

echo ""
echo "===== 改动范围：${GATE_CHANGED_COUNT:-0} 个文件，按需执行 ${GATE_RUN_COUNT:-15}/15 项门禁 ====="
if [[ "${GATE_FULL:-0}" == "1" ]]; then
  echo "（全量模式：--all / CI workflow_dispatch）"
fi
if [[ "${GATE_RUN_COUNT:-15}" == "0" ]]; then
  echo ""
  echo "本次改动无需执行自动门禁（仅涉及仓库说明 / Rust 试点 / 无自动校验目标等范围）。"
  echo "如需强制全量：bash scripts/ci/test.sh --all"
  exit 0
fi

# ── 同状态缓存：完全相同的改动内容 + 计划 + 门禁实现 → 上次全过则秒过 ──────
CACHE_FILE="$ROOT/.git/dsh-gate-cache.key"
CACHE_KEY=""
if [[ "${GATE_FULL:-0}" != "1" && "$NO_CACHE" != "1" ]]; then
  PLAN_SIG=""
  for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    v="GATE_STEP_$i"
    PLAN_SIG+="${!v:-1}"
  done
  RAW_KEY="$("$PY" "$ROOT/scripts/ci/gate_scope.py" --cache-key 2>/dev/null || true)"
  if [[ -n "$RAW_KEY" ]]; then
    CACHE_KEY="$(printf '%s|slow=%s|e2e=%s|plan=%s' "$RAW_KEY" "$SLOW" "$RUN_E2E" "$PLAN_SIG" | sha256sum | cut -d' ' -f1)"
    if [[ -f "$CACHE_FILE" ]]; then
      PREV_KEY="$(sed -n 1p "$CACHE_FILE" 2>/dev/null || true)"
      PREV_TIME="$(sed -n 2p "$CACHE_FILE" 2>/dev/null || true)"
      if [[ "$PREV_KEY" == "$CACHE_KEY" ]]; then
        echo ""
        echo "✅ 同一改动状态已通过门禁（上次通过：${PREV_TIME:-未知}），本次跳过重复执行。"
        echo "   如需强制重跑：bash scripts/ci/test.sh --no-cache（或 --all）"
        exit 0
      fi
    fi
  fi
fi

# pytest marker 分层：日常集与 CI 一致（不含 slow / system）；`--slow` 只把 slow
# 长耗时用例加回来，system 仍然排除——L2 系统测试会起真 uvicorn/worker 子进程，
# 只能通过 `bash scripts/ci/system-test.sh` 手动跑。
# 这里显式写 marker（而不是依赖 pytest.ini 的 addopts）：历史上 `-m ""` 覆盖过 ini，
# 把 system 带进了第 1 步且与 CI 的测试集/覆盖率分母不一致（2026-11-16 修正）。
if [[ "$SLOW" == "1" ]]; then
  MARKER="not system"
else
  MARKER="not slow and not system"
fi

if step_enabled 1; then
  run_header 1 "后端测试 + 覆盖率棘轮（≥82.9%）+ 关键模块地板"
  cd "$ROOT/backend"
  # 覆盖率底线是**棘轮**：只能往上调。
  # 注意 backend/.coveragerc 的 precision=2：不设它时覆盖率按整数四舍五入比较，
  # 门槛会「看起来没过、退出码却是 0」（门禁放水）。
  # --cov 参数只影响报告详略（不传则只看被跳过的文件），阈值始终生效。
  if [[ "$COV" == "--cov" ]]; then
    TZ=UTC "$PY" -m pytest -m "$MARKER" --cov --cov-fail-under=82.9 --cov-report=term-missing
  else
    TZ=UTC "$PY" -m pytest -m "$MARKER" --cov --cov-fail-under=82.9 --cov-report=term:skip-covered
  fi
  # 全局覆盖率会掩盖关键模块的薄弱：worker/出图/搜索/AI 设置单独设地板
  "$PY" scripts/check_module_coverage.py
else
  skip_step 1
fi

if step_enabled 2; then
  run_header 2 "后端 lint（ruff，零告警：app + 脚本目录）"
  cd "$ROOT/backend"
  # 脚本目录也纳入：2026-10-28 在 fetch_xhs_following.py 里发现「def 被注释吞掉」的
  # 真损坏（模块能导入、函数却不存在），正是「脚本不 lint」才会潜伏下来的问题
  "$PY" -m ruff check app scripts ../scripts
else
  skip_step 2
fi

if step_enabled 3; then
  run_header 3 "后端死代码（vulture，置信度 80 必须 0 条）"
  cd "$ROOT/backend"
  "$PY" -m vulture app --min-confidence 80
else
  skip_step 3
fi

if step_enabled 4; then
  run_header 4 "后端类型（mypy 棘轮）"
  cd "$ROOT/backend"
  "$PY" scripts/check_types.py
else
  skip_step 4
fi

if step_enabled 5; then
  run_header 5 "迁移链自检（空库 → 与 ORM 一致）"
  cd "$ROOT/backend"
  "$PY" scripts/check_fresh_migrations.py
else
  skip_step 5
fi

if step_enabled 6; then
  run_header 6 "库结构漂移（ORM ↔ 现有库）"
  cd "$ROOT/backend"
  "$PY" scripts/check_alembic_drift.py
else
  skip_step 6
fi

if step_enabled 7; then
  run_header 7 "脚本规范（只读连接 / CLI 入口）+ 门禁计划自检"
  cd "$ROOT"
  "$PY" scripts/ci/check_scripts.py
  # 只改 scripts/ 时不会触发 pytest，门禁计划器自身的分类/回退逻辑在这里兜底自检
  "$PY" scripts/ci/gate_scope.py --self-test
else
  skip_step 7
fi

if step_enabled 8; then
  run_header 8 "前端类型检查（vue-tsc）"
  cd "$ROOT/web"
  node node_modules/vue-tsc/bin/vue-tsc.js --noEmit
else
  skip_step 8
fi

if step_enabled 9; then
  run_header 9 "前端测试（vitest + 覆盖率）"
  cd "$ROOT/web"
  node node_modules/vitest/vitest.mjs run --coverage
else
  skip_step 9
fi

if step_enabled 10; then
  run_header 10 "前端 lint（eslint，棘轮 34）"
  cd "$ROOT/web"
  node node_modules/eslint/bin/eslint.js . --max-warnings 34
else
  skip_step 10
fi

if step_enabled 11; then
  run_header 11 "前端死代码基线（ts-prune + Vue 组件引用）"
  cd "$ROOT"
  node scripts/ci/check-web-dead-code.mjs
  node scripts/ci/check-web-unused-components.mjs
else
  skip_step 11
fi

if step_enabled 12; then
  run_header 12 "前端格式（只查改动文件）"
  cd "$ROOT"
  node scripts/ci/check-web-format.mjs
else
  skip_step 12
fi

if step_enabled 13; then
  run_header 13 "移动端（tsc + jest）"
  cd "$ROOT/mobile"
  # 移动端没有独立的 lint 脚本，类型检查用 tsc；测试含「非 <Text> 节点下不许有裸字符串」
  # 的断言，专门防只有原生 RN 才会拦的白屏（见 CLAUDE.md）
  node node_modules/typescript/bin/tsc --noEmit
  node node_modules/jest/bin/jest.js --ci --coverage
else
  skip_step 13
fi

if step_enabled 14; then
  run_header 14 "子项目最低测试（浏览器插件 / 人脸子服务）"
  cd "$ROOT"
  node --test browser-extension/test-extract-images.mjs
  if [[ -x "$ROOT/face-service/.venv/Scripts/python.exe" ]]; then
    cd "$ROOT/face-service"
    .venv/Scripts/python.exe -m unittest -q test_storage.py
  else
    echo "[skip] face-service/.venv 不存在；CI 的 subprojects job 会覆盖该测试"
  fi
else
  skip_step 14
fi

if step_enabled 15; then
  run_header 15 "文档生成物与代码一致（API / 数据模型 / 配置项）"
  cd "$ROOT"
  # 这三份文档由代码生成（scripts/data/gen_docs_from_code.py），手改会被判「与代码不一致」。
  # 放在最后：它最便宜，但前面的门禁更重要——先让真问题先报，别让文档 diff 掩盖测试失败。
  # 报红时按提示重跑：python scripts/data/gen_docs_from_code.py
  "$PY" scripts/data/gen_docs_from_code.py --check
else
  skip_step 15
fi

if [[ "$RUN_E2E" == "1" ]]; then
  echo ""
  echo "===== 附加：端到端 E2E（需后端在跑）====="
  cd "$ROOT/web"
  node node_modules/@playwright/test/cli.js test --reporter=list
fi

# 全部通过才写缓存；任一失败会由 set -e 提前退出，不会留下「假通过」缓存
if [[ -n "${CACHE_KEY:-}" ]]; then
  { echo "$CACHE_KEY"; date '+%Y-%m-%d %H:%M:%S'; } > "$CACHE_FILE"
fi

echo ""
if [[ "$RUN_E2E" == "1" ]]; then
  echo "✅ 门禁通过：执行 ${RUNNING}/15 项，跳过 ${SKIPPED} 项 + E2E"
else
  echo "✅ 门禁通过：执行 ${RUNNING}/15 项，跳过 ${SKIPPED} 项（按改动范围裁剪；全量请加 --all）"
fi
