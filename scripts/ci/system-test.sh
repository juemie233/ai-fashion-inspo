#!/usr/bin/env bash
# L2 系统测试入口（手动/按需；不并入默认门禁）。
#
# 与 `scripts/ci/test.sh` 的区别：
# - 会起**临时端口 + 临时 DB/存储**的真 uvicorn（以及后续的 worker 子进程），
#   不触碰真实 18888/17777/18889、真实 storage 与 backend/fashion_inspo.db；
# - 默认不跑（pytest addopts 已排除 `-m system`），只在需要时手动触发；
# - 失败时保留临时目录路径（脚本会打印），便于看 uvicorn 日志。
#
# 用法：
#   bash scripts/ci/system-test.sh                # 全部系统用例
#   bash scripts/ci/system-test.sh -k smoke       # 只跑某类
#   PYTHON_BIN="D:/.../python.exe" bash scripts/ci/system-test.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"

if [ -n "${PYTHON_BIN:-}" ]; then
  PYTHON=("$PYTHON_BIN")
elif command -v py >/dev/null 2>&1; then
  # Windows：优先用项目约定的 3.12 launcher
  PYTHON=(py -3.12)
else
  PYTHON=(python)
fi

cd "$BACKEND_DIR"

echo "[system-test] python: ${PYTHON[*]}"
echo "[system-test] 说明：临时 DB/存储 + 临时端口；不触碰真实服务与真实数据"

"${PYTHON[@]}" -m pytest tests/system \
  -m system \
  -n 0 \
  -p no:cacheprovider \
  --no-cov \
  -q \
  "$@"
