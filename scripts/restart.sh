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
echo "  一键重启前后端 + worker"
echo "=============================================="

# ── 1. 停止后端 ──
echo ""
echo ">>> [1/6] 停止后端 (端口 $BACKEND_PORT) ..."

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
echo ">>> [2/6] 停止前端 (端口 $FRONTEND_PORT) ..."
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

# ── 3. 停止 worker ──
echo ""
echo ">>> [3/6] 停止 worker ..."
for pid in $(powershell -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -like '*app.worker*' } | Select-Object -ExpandProperty ProcessId" 2>/dev/null | grep -Eo '[0-9]+'); do
  taskkill //F //T //PID "$pid" >/dev/null 2>&1 && echo "  已终止 worker 进程 PID $pid"
done
sleep 1

# ── 4. 启动后端 ──
echo ""
echo ">>> [4/6] 启动后端 ..."
cd backend
nohup python -m uvicorn app.main:app --host 0.0.0.0 --port "$BACKEND_PORT" --reload \
  > "../$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!
cd ..
echo "  后端 PID: $BACKEND_PID (日志: $LOG_DIR/backend.log)"

# ── 4. 启动前端 ──
echo ""
echo ">>> [5/6] 启动前端 ..."
if [ ! -d web/node_modules ]; then
  echo "  ❌ 未检测到 web/node_modules，请先执行: cd web && npm install"
  exit 1
fi
cd web
nohup npm run dev -- --host 0.0.0.0 --port "$FRONTEND_PORT" \
  > "../$LOG_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!
cd ..
echo "  前端 PID: $FRONTEND_PID (日志: $LOG_DIR/frontend.log)"

# ── 6. 启动 worker ──
echo ""
echo ">>> [6/6] 启动 worker ..."
cd backend
nohup python -m app.worker > "../$LOG_DIR/worker.log" 2>&1 &
WORKER_PID=$!
cd ..
echo "  worker PID: $WORKER_PID (日志: $LOG_DIR/worker.log)"

# ── 验证（轮询，最多 30 秒）──
echo ""
echo ">>> 等待服务启动..."
BACKEND_OK=0
FRONTEND_OK=0
for _ in $(seq 1 30); do
  [ "$BACKEND_OK" -eq 1 ] || curl -s "http://localhost:$BACKEND_PORT/api/health" >/dev/null 2>&1 && BACKEND_OK=1
  [ "$FRONTEND_OK" -eq 1 ] || curl -s "http://localhost:$FRONTEND_PORT" >/dev/null 2>&1 && FRONTEND_OK=1
  if [ "$BACKEND_OK" -eq 1 ] && [ "$FRONTEND_OK" -eq 1 ]; then break; fi
  sleep 1
done

if [ "$BACKEND_OK" -eq 1 ]; then
  echo "  ✅ 后端已就绪: http://localhost:$BACKEND_PORT"
  curl -s "http://localhost:$BACKEND_PORT/api/health" && echo ""
else
  echo "  ❌ 后端未就绪，请检查 $LOG_DIR/backend.log"
fi

if [ "$FRONTEND_OK" -eq 1 ]; then
  echo "  ✅ 前端已就绪: http://localhost:$FRONTEND_PORT"
else
  echo "  ❌ 前端未就绪，请检查 $LOG_DIR/frontend.log"
fi

# worker 无 HTTP 端口，通过日志确认是否已启动
sleep 2
if grep -q "worker 已启动" "$LOG_DIR/worker.log" 2>/dev/null; then
  echo "  ✅ worker 已启动（日志: $LOG_DIR/worker.log）"
else
  echo "  ⚠️  worker 可能未就绪，请检查 $LOG_DIR/worker.log"
fi

echo ""
echo "=============================================="
echo "  完成。日志目录: $LOG_DIR/"
echo "=============================================="

# 任一服务未就绪则返回非零退出码，供自动化判断
if [ "$BACKEND_OK" -eq 1 ] && [ "$FRONTEND_OK" -eq 1 ]; then
  exit 0
else
  exit 1
fi
