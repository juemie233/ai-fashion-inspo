#!/usr/bin/env bash
# 一键跑全部质量门禁（与 .github/workflows/ci.yml 对齐：本地先绿，再推）。
#   bash scripts/test.sh          全部门禁
#   bash scripts/test.sh --cov    后端额外输出行级覆盖率报告（term-missing）
#
# 门禁清单（顺序即输出编号）：
#    1. 后端 pytest 全量 + 覆盖率棘轮 ≥69%（临时库/临时存储，不碰真实数据）
#    2. 后端 ruff（零告警）
#    3. 后端 vulture（死代码，--min-confidence 80 必须 0 条）
#    4. 后端 mypy 棘轮（只查已清零的模块，列表只增不减）
#    5. 迁移链自检（空库 upgrade head → 结构必须与 ORM 一致）
#    6. 真实库漂移（ORM ↔ 现有库；无库环境自动跳过）
#    7. 前端 vue-tsc 类型检查
#    8. 前端 vitest
#    9. 前端 eslint（带 --max-warnings 棘轮：只能降不能升，当前 34）
#   10. 前端死代码基线（ts-prune，只拦新增）
#   11. 前端格式（只查本次改动文件；全库检查会因 45 个历史文件永远为红）
#
# 依赖：Python 3.12+（可用 PYTHON_BIN 覆盖，如 PYTHON_BIN=py）、Node 20+、web/node_modules。
# 前端一律用 node 直接调用本地二进制，规避 npx/npm 包装脚本在受限 PowerShell 下的执行策略问题。
#
# ⚠️ 「✅」只在最后一行打印：以前它排在两个门禁之前，门禁失败时屏幕上反而先看到「通过」。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON_BIN:-python}"
COV="${1:-}"

echo "===== 1/11 后端测试 + 覆盖率棘轮（≥78%）====="
cd "$ROOT/backend"
# 覆盖率底线是**棘轮**：只能往上调。当前 78.65%（2026-10-21 实测）。
# 注意 backend/.coveragerc 的 precision=2：不设它时覆盖率按整数四舍五入比较，
# 门槛会「看起来没过、退出码却是 0」（门禁放水）。
# --cov 参数只影响报告详略（不传则只看被跳过的文件），阈值始终生效。
if [[ "$COV" == "--cov" ]]; then
  "$PY" -m pytest --cov --cov-fail-under=78 --cov-report=term-missing
else
  "$PY" -m pytest --cov --cov-fail-under=78 --cov-report=term:skip-covered
fi

echo ""
echo "===== 2/11 后端 lint（ruff，零告警）====="
cd "$ROOT/backend"
"$PY" -m ruff check app

echo ""
echo "===== 3/11 后端死代码（vulture，置信度 80 必须 0 条）====="
cd "$ROOT/backend"
"$PY" -m vulture app --min-confidence 80

echo ""
echo "===== 4/11 后端类型（mypy 棘轮）====="
cd "$ROOT/backend"
"$PY" scripts/check_types.py

echo ""
echo "===== 5/11 迁移链自检（空库 → 与 ORM 一致）====="
cd "$ROOT/backend"
"$PY" scripts/check_fresh_migrations.py

echo ""
echo "===== 6/11 库结构漂移（ORM ↔ 现有库）====="
cd "$ROOT/backend"
"$PY" scripts/check_alembic_drift.py

echo ""
echo "===== 7/11 前端类型检查（vue-tsc）====="
cd "$ROOT/web"
node node_modules/vue-tsc/bin/vue-tsc.js --noEmit

echo ""
echo "===== 8/11 前端测试（vitest）====="
cd "$ROOT/web"
node node_modules/vitest/vitest.mjs run

echo ""
echo "===== 9/11 前端 lint（eslint，棘轮 34）====="
cd "$ROOT/web"
node node_modules/eslint/bin/eslint.js . --max-warnings 34

echo ""
echo "===== 10/11 前端死代码基线（ts-prune）====="
cd "$ROOT"
node scripts/check-web-dead-code.mjs

echo ""
echo "===== 11/11 前端格式（只查改动文件）====="
cd "$ROOT"
node scripts/check-web-format.mjs

echo ""
echo "✅ 11 项门禁全部通过"
