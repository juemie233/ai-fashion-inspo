#!/usr/bin/env bash
# 一键跑全部质量门禁（与 .github/workflows/ci.yml 对齐：本地先绿，再推）。
#   bash scripts/ci/test.sh          全部门禁
#   bash scripts/ci/test.sh --cov    后端额外输出行级覆盖率报告（term-missing）
#   bash scripts/ci/test.sh --e2e    跑完 14 项后再跑 Playwright（需后端在跑，可选/nightly）
#
# 门禁清单（顺序即输出编号）：
#    1. 后端 pytest 全量 + 覆盖率棘轮 ≥82.9% + 关键模块地板（临时库/临时存储）
#    2. 后端 ruff（零告警）
#    3. 后端 vulture（死代码，--min-confidence 80 必须 0 条）
#    4. 后端 mypy 棘轮（只查已清零的模块，列表只增不减）
#    5. 迁移链自检（空库 upgrade head → 结构必须与 ORM 一致）
#    6. 真实库漂移（ORM ↔ 现有库；无库环境自动跳过）
#    7. 脚本规范（只读连接 / CLI 入口）
#    8. 前端 vue-tsc 类型检查
#    9. 前端 vitest
#   10. 前端 eslint（带 --max-warnings 棘轮：只能降不能升，当前 34）
#   11. 前端死代码基线（ts-prune，只拦新增）
#   12. 前端格式（只查本次改动文件；全库检查会因约 45 个历史文件永远为红）
#   13. 移动端类型检查（tsc）+ 单元测试（jest-expo + RNTL，含覆盖率门槛）
#   14. 子项目最低测试（browser-extension 纯函数；face-service 存储层，无 venv 时跳过）
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
for arg in "$@"; do
  case "$arg" in
    --cov) COV="--cov" ;;
    --e2e) RUN_E2E=1 ;;
    *) echo "未知参数：$arg（支持 --cov / --e2e）" >&2; exit 2 ;;
  esac
done

echo "===== 1/14 后端测试 + 覆盖率棘轮（≥82.9%）+ 关键模块地板 ====="
cd "$ROOT/backend"
# 覆盖率底线是**棘轮**：只能往上调。当前 82.93%（2026-11-04 实测），本轮提到 82.9。
# 注意 backend/.coveragerc 的 precision=2：不设它时覆盖率按整数四舍五入比较，
# 门槛会「看起来没过、退出码却是 0」（门禁放水）。
# --cov 参数只影响报告详略（不传则只看被跳过的文件），阈值始终生效。
if [[ "$COV" == "--cov" ]]; then
  "$PY" -m pytest --cov --cov-fail-under=82.9 --cov-report=term-missing
else
  "$PY" -m pytest --cov --cov-fail-under=82.9 --cov-report=term:skip-covered
fi
# 全局覆盖率会掩盖关键模块的薄弱：worker/出图/搜索/AI 设置单独设地板
"$PY" scripts/check_module_coverage.py

echo ""
echo "===== 2/14 后端 lint（ruff，零告警：app + 脚本目录）====="
cd "$ROOT/backend"
# 脚本目录也纳入：2026-10-28 在 fetch_xhs_following.py 里发现「def 被注释吞掉」的
# 真损坏（模块能导入、函数却不存在），正是「脚本不 lint」才会潜伏下来的问题
"$PY" -m ruff check app scripts ../scripts

echo ""
echo "===== 3/14 后端死代码（vulture，置信度 80 必须 0 条）====="
cd "$ROOT/backend"
"$PY" -m vulture app --min-confidence 80

echo ""
echo "===== 4/14 后端类型（mypy 棘轮）====="
cd "$ROOT/backend"
"$PY" scripts/check_types.py

echo ""
echo "===== 5/14 迁移链自检（空库 → 与 ORM 一致）====="
cd "$ROOT/backend"
"$PY" scripts/check_fresh_migrations.py

echo ""
echo "===== 6/14 库结构漂移（ORM ↔ 现有库）====="
cd "$ROOT/backend"
"$PY" scripts/check_alembic_drift.py

echo ""
echo "===== 7/14 脚本规范（只读连接 / CLI 入口）====="
cd "$ROOT"
"$PY" scripts/ci/check_scripts.py

echo ""
echo "===== 8/14 前端类型检查（vue-tsc）====="
cd "$ROOT/web"
node node_modules/vue-tsc/bin/vue-tsc.js --noEmit

echo ""
echo "===== 9/14 前端测试（vitest + 覆盖率）====="
cd "$ROOT/web"
node node_modules/vitest/vitest.mjs run --coverage

echo ""
echo "===== 10/14 前端 lint（eslint，棘轮 34）====="
cd "$ROOT/web"
node node_modules/eslint/bin/eslint.js . --max-warnings 34

echo ""
echo "===== 11/14 前端死代码基线（ts-prune）====="
cd "$ROOT"
node scripts/ci/check-web-dead-code.mjs

echo ""
echo "===== 12/14 前端格式（只查改动文件）====="
cd "$ROOT"
node scripts/ci/check-web-format.mjs

echo ""
echo "===== 13/14 移动端（tsc + jest）====="
cd "$ROOT/mobile"
# 移动端没有独立的 lint 脚本，类型检查用 tsc；测试含「非 <Text> 节点下不许有裸字符串」
# 的断言，专门防只有原生 RN 才会拦的白屏（见 CLAUDE.md）
node node_modules/typescript/bin/tsc --noEmit
node node_modules/jest/bin/jest.js --ci --coverage

echo ""
echo "===== 14/14 子项目最低测试（浏览器插件 / 人脸子服务）====="
cd "$ROOT"
node --test browser-extension/test-extract-images.mjs
if [[ -x "$ROOT/face-service/.venv/Scripts/python.exe" ]]; then
  cd "$ROOT/face-service"
  .venv/Scripts/python.exe -m unittest -q test_storage.py
else
  echo "[skip] face-service/.venv 不存在；CI 的 subprojects job 会覆盖该测试"
fi

if [[ "$RUN_E2E" == "1" ]]; then
  echo ""
  echo "===== 附加：端到端 E2E（需后端在跑）====="
  cd "$ROOT/web"
  node node_modules/@playwright/test/cli.js test --reporter=list
fi

echo ""
if [[ "$RUN_E2E" == "1" ]]; then
  echo "✅ 14 项门禁 + E2E 全部通过"
else
  echo "✅ 14 项门禁全部通过"
fi
