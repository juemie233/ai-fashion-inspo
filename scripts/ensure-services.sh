#!/bin/bash
# AI 穿搭素材库 — 幂等地确保服务在运行（供 Claude Code SessionStart 钩子调用）
# 用法: bash scripts/ensure-services.sh
#
# 与 restart.sh 的区别：
#   - restart.sh：先杀后启（全量重启），适合手动「彻底重启」。
#   - 本脚本：健康检查后只启动缺失的服务，已在运行则跳过，天然幂等。
#
# 后端「不」使用 uvicorn --reload：Windows 下 --reload 在文件变更重载时
# 会以 OSError [WinError 87] 崩溃，导致后端挂掉（详见 restart.sh 说明）。
# 通过「原子锁」保证并发调用（如多个会话同时启动）时只有一个实例真正执行，
# 其余实例直接退出，避免并发重启互相抢端口、杀进程导致服务崩溃。

cd "$(dirname "$0")/.."

BACKEND_PORT=18888
FRONTEND_PORT=17777
AGENTMEMORY_PORT=3111
LOG_DIR="logs"

mkdir -p "$LOG_DIR"

LOCK_DIR="$LOG_DIR/ensure-services.lock"
LOCK_STALE_SECONDS=180

# ── 原子锁：mkdir 原子性保证只有一个实例能拿到锁 ──
acquire_lock() {
  local now ts
  now=$(date +%s)
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "$now" > "$LOCK_DIR/ts"
    return 0
  fi
  # 锁已存在：超过阈值视为「上次执行被强杀」留下的陈旧锁，清理后重试一次
  ts=$(cat "$LOCK_DIR/ts" 2>/dev/null || echo 0)
  if [ $(( now - ts )) -gt "$LOCK_STALE_SECONDS" ]; then
    rm -rf "$LOCK_DIR"
    if mkdir "$LOCK_DIR" 2>/dev/null; then
      echo "$now" > "$LOCK_DIR/ts"
      return 0
    fi
  fi
  return 1
}

if ! acquire_lock; then
  echo "[ensure-services] 已有实例在运行，本次跳过"
  exit 0
fi

# 无论正常/异常退出都释放锁
trap 'rm -rf "$LOCK_DIR"' EXIT

# ── 健康检查辅助 ──
port_listening() {
  netstat -ano 2>/dev/null | grep ":$1" | grep -qi LISTENING
}

backend_healthy() {
  curl -s --max-time 3 "http://localhost:$BACKEND_PORT/api/health" >/dev/null 2>&1
}

frontend_healthy() {
  curl -s --max-time 3 "http://localhost:$FRONTEND_PORT" >/dev/null 2>&1
}

worker_running() {
  local found
  found=$(powershell -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -like '*app.worker*' } | Select-Object -ExpandProperty ProcessId" 2>/dev/null | grep -Eo '[0-9]+' | head -1)
  [ -n "$found" ]
}

agentmemory_running() {
  port_listening "$AGENTMEMORY_PORT"
}

echo "=============================================="
echo "  确保服务在运行（幂等）"
echo "=============================================="

# ── 后端 ──
echo ""
echo ">>> 检查后端 (端口 $BACKEND_PORT) ..."
if backend_healthy; then
  echo "  ✅ 后端已在运行，跳过"
else
  echo "  后端未运行，启动中..."
  cd backend
  # PYTHONUTF8=1 让中文日志以 UTF-8 落盘，避免 Windows 默认 GBK 导致日志乱码
  PYTHONUTF8=1 nohup python -m uvicorn app.main:app --host 0.0.0.0 --port "$BACKEND_PORT" \
    > "../$LOG_DIR/backend.log" 2>&1 &
  cd ..
  echo "  后端 PID: $! (日志: $LOG_DIR/backend.log)"
fi

# ── 前端 ──
echo ""
echo ">>> 检查前端 (端口 $FRONTEND_PORT) ..."
if frontend_healthy; then
  echo "  ✅ 前端已在运行，跳过"
else
  if [ ! -d web/node_modules ]; then
    echo "  ❌ 未检测到 web/node_modules，请先执行: cd web && npm install"
  else
    echo "  前端未运行，启动中..."
    cd web
    nohup npm run dev -- --host 0.0.0.0 --port "$FRONTEND_PORT" \
      > "../$LOG_DIR/frontend.log" 2>&1 &
    cd ..
    echo "  前端 PID: $! (日志: $LOG_DIR/frontend.log)"
  fi
fi

# ── worker ──
echo ""
echo ">>> 检查 worker ..."
if worker_running; then
  echo "  ✅ worker 已在运行，跳过"
else
  echo "  worker 未运行，启动中..."
  cd backend
  PYTHONUTF8=1 nohup python -m app.worker > "../$LOG_DIR/worker.log" 2>&1 &
  cd ..
  echo "  worker PID: $! (日志: $LOG_DIR/worker.log)"
fi

# ── agentmemory ──
echo ""
echo ">>> 检查 agentmemory ..."
if command -v agentmemory >/dev/null 2>&1; then
  if agentmemory_running; then
    echo "  ✅ agentmemory 已在运行，跳过"
  else
    echo "  agentmemory 未运行，启动中..."
    nohup agentmemory > "$HOME/.agentmemory/agentmemory.log" 2>&1 &
    echo "  agentmemory 已后台启动 (日志: ~/.agentmemory/agentmemory.log)"
  fi
else
  echo "  未安装 agentmemory CLI，跳过"
fi

# ── 等待就绪 ──
echo ""
echo ">>> 等待服务启动..."
BACKEND_OK=0
FRONTEND_OK=0
for _ in $(seq 1 30); do
  [ "$BACKEND_OK" -eq 1 ] || backend_healthy && BACKEND_OK=1
  [ "$FRONTEND_OK" -eq 1 ] || frontend_healthy && FRONTEND_OK=1
  if [ "$BACKEND_OK" -eq 1 ] && [ "$FRONTEND_OK" -eq 1 ]; then break; fi
  sleep 1
done

if backend_healthy; then
  echo "  ✅ 后端已就绪: http://localhost:$BACKEND_PORT"
else
  echo "  ❌ 后端未就绪，请检查 $LOG_DIR/backend.log"
fi

if frontend_healthy; then
  echo "  ✅ 前端已就绪: http://localhost:$FRONTEND_PORT"
else
  echo "  ❌ 前端未就绪，请检查 $LOG_DIR/frontend.log"
fi

echo ""
echo "=============================================="
echo "  完成。日志目录: $LOG_DIR/"
echo "=============================================="
