#!/bin/bash
# AI 穿搭素材库 — 前端项目打包脚本
# 用法:
#   bash scripts/build_web.sh              # 仅构建，产物在 web/dist
#   bash scripts/build_web.sh --zip        # 构建后把 web/dist 打成 dist-<时间戳>.zip
#   bash scripts/build_web.sh --no-install # 跳过「缺 node_modules 时 npm install」
#
# 说明:
#   - 构建命令 = web/package.json 的 "build": vue-tsc && vite build
#     （先做 TS 类型检查，再产出生产构建，输出目录默认 web/dist）
#   - 依赖缺失时自动 npm install；前端由 supervisor 以 vite dev server 运行，
#     本脚本产物供生产部署 / 迁移 / 归档，不用于本地开发热更。
#   - 只构建打包，不停启服务（项目约定：服务启停由用户手动执行）。

set -u

# Windows 下管道默认 GBK，强制 UTF-8 输出避免中文日志乱码
export PYTHONUTF8=1

cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd -W 2>/dev/null || pwd)"
WEB_DIR="$PROJECT_ROOT/web"

ZIP=0
AUTO_INSTALL=1
while [ $# -gt 0 ]; do
  case "$1" in
    --zip) ZIP=1; shift ;;
    --no-install) AUTO_INSTALL=0; shift ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "未知参数: $1"; exit 1 ;;
  esac
done

echo "=============================================="
echo "  前端项目打包"
echo "=============================================="
echo "项目根:  $PROJECT_ROOT"
echo "前端目录: $WEB_DIR"

if [ ! -d "$WEB_DIR" ]; then
  echo "错误：未找到 web/ 目录（$WEB_DIR）"
  exit 1
fi

# ── 0. 前置检查：node / npm ──
if ! command -v node >/dev/null 2>&1; then
  echo "错误：未找到 node，请先安装 Node.js 20+ 并加入 PATH"
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "错误：未找到 npm，请先安装 Node.js 20+"
  exit 1
fi
NODE_VER="$(node -v 2>/dev/null)"
echo "node:     $NODE_VER"

# ── 1. 依赖检查 ──
echo ""
echo ">>> [1/3] 检查依赖 ..."
if [ ! -d "$WEB_DIR/node_modules" ]; then
  if [ "$AUTO_INSTALL" -eq 1 ]; then
    echo "  未检测到 web/node_modules，执行 npm install ..."
    ( cd "$WEB_DIR" && npm install ) || { echo "  ❌ npm install 失败"; exit 1; }
  else
    echo "  未检测到 web/node_modules（且已用 --no-install 跳过）。"
    echo "  请先执行: cd web && npm install"
    exit 1
  fi
else
  echo "  ✅ node_modules 已存在"
fi

# ── 2. 构建 ──
echo ""
echo ">>> [2/3] 构建前端（vue-tsc 类型检查 + vite build）..."
( cd "$WEB_DIR" && npm run build )
BUILD_RC=$?
if [ "$BUILD_RC" -ne 0 ]; then
  echo "  ❌ 前端构建失败（npm run build 退出码 $BUILD_RC）"
  exit 1
fi

DIST_DIR="$WEB_DIR/dist"
if [ ! -d "$DIST_DIR" ]; then
  echo "  ⚠️  未找到产物目录 $DIST_DIR，请检查 vite 的 outDir 配置"
  exit 1
fi

echo ""
echo ">>> 产物目录: $DIST_DIR"
du -sh "$DIST_DIR" 2>/dev/null | sed 's/^/  总大小: /'
echo "  入口文件:"
FILES="$(ls "$DIST_DIR" 2>/dev/null | sed 's/^/    /')"
echo "$FILES"

# ── 3. 可选：打包 zip ──
if [ "$ZIP" -eq 1 ]; then
  echo ""
  echo ">>> [3/3] 打包 zip ..."
  STAMP="$(date +%Y-%m-%d_%H%M%S)"
  ZIP_OUT="$PROJECT_ROOT/dist-${STAMP}.zip"
  if command -v powershell >/dev/null 2>&1; then
    powershell -NoProfile -Command \
      "Compress-Archive -Path '$DIST_DIR' -DestinationPath '$ZIP_OUT' -Force"
  else
    ( cd "$WEB_DIR" && zip -r "$ZIP_OUT" dist >/dev/null )
  fi
  if [ -f "$ZIP_OUT" ]; then
    echo "  ✅ 已打包: $ZIP_OUT"
    du -sh "$ZIP_OUT" 2>/dev/null | sed 's/^/  总大小: /'
  else
    echo "  ❌ zip 打包失败（请检查 Compress-Archive / zip 是否可用）"
    exit 1
  fi
fi

echo ""
echo "=============================================="
echo "  打包完成"
echo "=============================================="
echo "产物: $DIST_DIR"
[ "$ZIP" -eq 1 ] && echo "zip:  $ZIP_OUT"
echo ""
exit 0
