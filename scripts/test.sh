#!/usr/bin/env bash
# 一键运行自动化测试：
#   bash scripts/test.sh          后端 pytest + 前端类型检查 + vitest
#   bash scripts/test.sh --cov    后端额外输出行级覆盖率报告（term-missing）
#
# 依赖：Python 3.12+ / Node 20+，且 web/node_modules 已安装（npm install）。
# 前端用 node 直接调用本地二进制，规避 npx/npm 包装脚本在受限 PowerShell 下的执行策略问题。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COV="${1:-}"

echo "===== 后端测试（pytest）====="
cd "$ROOT/backend"
if [[ "$COV" == "--cov" ]]; then
  python -m pytest --cov --cov-report=term-missing
else
  python -m pytest
fi

echo ""
echo "===== 前端类型检查（vue-tsc）====="
cd "$ROOT/web"
node node_modules/vue-tsc/bin/vue-tsc.js --noEmit

echo ""
echo "===== 前端测试（vitest）====="
node node_modules/vitest/vitest.mjs run

echo ""
echo "✅ 全部测试通过"
