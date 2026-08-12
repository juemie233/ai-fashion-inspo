#!/bin/bash
# AI 穿搭素材库 — 一键重启前后端
# 用法: bash scripts/restart.sh
#
# 解决 Windows 上 uvicorn --reload 不可靠的问题：
#   - --reload 的文件监听对子目录变更有时不触发，导致运行旧代码
#   - 强制杀进程时容易残留 multiprocessing worker 子进程占用端口
# 本脚本可靠地终止所有相关进程（含孤儿 worker）后重启。

cd "$(dirname "$0")/.."

BACKEND_PORT=18888
FRONTEND_PORT=17777
LOG_DIR="logs"

mkdir -p "$LOG_DIR"

echo "=============================================="
echo "  一键重启前后端"
echo "=============================================="

# ── 1. 停止后端 ──
echo ""
echo ">>> [1/4] 停止后端 (端口 $BACKEND_PORT) ..."

# 1a. 杀掉监听端口的进程树（可能是 worker，taskkill /T 会级联杀掉其父 reloader）
killed=0
for pid in $(netstat -ano 2>/dev/null | grep ":$BACKEND_PORT" | grep -i LISTENING | awk '{print $NF}' | sort -u); do
  if [ -n "$pid" ]; then
    taskkill //F //T //PID "$pid" >/dev/null 2>&1 && { echo "  已终止进程树 PID $pid"; killed=1; }
  fi
done

# 1b. 兜底：按命令行匹配 reloader 主进程（uvicorn --reload 的父进程），
#     防止 worker 已死但 reloader 仍残留的情况
for pid in $(powershell -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -like '*uvicorn app.main*' } | Select-Object -ExpandProperty ProcessId" 2>/dev/null | grep -Eo '[0-9]+'); do
  taskkill //F //T //PID "$pid" >/dev/null 2>&1 && { echo "  已终止 reloader PID $pid"; killed=1; }
done

sleep 1

# 验证端口已释放
if netstat -ano 2>/dev/null | grep ":$BACKEND_PORT" | grep -qi LISTENING; then
  echo "  ⚠️  警告：端口 $BACKEND_PORT 可能仍被占用"
else
  echo "  ✅ 后端已停止"
fi

# ── 2. 停止前端 ──
echo ""
echo ">>> [2/4] 停止前端 (端口 $FRONTEND_PORT) ..."
for pid in $(netstat -ano 2>/dev/null | grep ":$FRONTEND_PORT" | grep -i LISTENING | awk '{print $NF}' | sort -u); do
  if [ -n "$pid" ]; then
    taskkill //F //T //PID "$pid" >/dev/null 2>&1 && echo "  已终止进程树 PID $pid"
  fi
done
sleep 1
if netstat -ano 2>/dev/null | grep ":$FRONTEND_PORT" | grep -qi LISTENING; then
  echo "  ⚠️  警告：端口 $FRONTEND_PORT 可能仍被占用"
else
  echo "  ✅ 前端已停止"
fi

# ── 3. 启动后端 ──
echo ""
echo ">>> [3/4] 启动后端 ..."
cd backend
nohup python -m uvicorn app.main:app --host 0.0.0.0 --port "$BACKEND_PORT" --reload \
  > "../$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!
cd ..
echo "  后端 PID: $BACKEND_PID (日志: $LOG_DIR/backend.log)"

# ── 4. 启动前端 ──
echo ""
echo ">>> [4/4] 启动前端 ..."
cd web
nohup npx vite --host 0.0.0.0 --port "$FRONTEND_PORT" \
  > "../$LOG_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!
cd ..
echo "  前端 PID: $FRONTEND_PID (日志: $LOG_DIR/frontend.log)"

# ── 验证 ──
echo ""
echo ">>> 等待服务启动..."
sleep 5

if curl -s "http://localhost:$BACKEND_PORT/api/health" >/dev/null 2>&1; then
  echo "  ✅ 后端已就绪: http://localhost:$BACKEND_PORT"
  curl -s "http://localhost:$BACKEND_PORT/api/health" && echo ""
else
  echo "  ❌ 后端未就绪，请检查 $LOG_DIR/backend.log"
fi

if curl -s "http://localhost:$FRONTEND_PORT" >/dev/null 2>&1; then
  echo "  ✅ 前端已就绪: http://localhost:$FRONTEND_PORT"
else
  echo "  ❌ 前端未就绪，请检查 $LOG_DIR/frontend.log"
fi

echo ""
echo "=============================================="
echo "  完成。日志目录: $LOG_DIR/"
echo "=============================================="
