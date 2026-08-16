#!/bin/bash
# AI 穿搭素材库 — 幂等地确保服务在运行（供 Claude Code SessionStart 钩子调用）
# 用法: bash scripts/ensure-services.sh
#
# 与 restart.sh 的区别：
#   - restart.sh：先杀后启（全量重启），适合手动「彻底重启」。
#   - 本脚本：健康检查后只启动缺失的服务，已在运行则跳过，天然幂等。
#
# 自「服务守护」改造后，本脚本只负责确保 supervisor 守护进程在运行；
# supervisor 会自行拉起并守护 backend / frontend / worker 三个服务。
# 本脚本最后轮询等待三服务就绪，供 SessionStart 后续流程使用。
#
# 后端「不」使用 uvicorn --reload：Windows 下 --reload 在文件变更重载时
# 会以 OSError [WinError 87] 崩溃，导致后端挂掉（详见 restart.sh 说明）。
# 通过「原子锁」保证并发调用（如多个会话同时启动）时只有一个实例真正执行，
# 其余实例直接退出，避免并发重启互相抢端口、杀进程导致服务崩溃。

cd "$(dirname "$0")/.."

BACKEND_PORT=18888
FRONTEND_PORT=17777
LOG_DIR="logs"

mkdir -p "$LOG_DIR"

LOCK_DIR="$LOG_DIR/ensure-services.lock"
LOCK_STALE_SECONDS=180

# ── 原子锁：mkdir 原子性保证只有一个实例能拿到锁 ──
acquire_lock() {
  local now=0 ts=0
  now=$(date +%s)
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "$now" > "$LOCK_DIR/ts"
    return 0
  fi
  # 锁已存在：超过阈值视为「上次执行被强杀」留下的陈旧锁，清理后重试一次
  ts=$(cat "$LOCK_DIR/ts" 2>/dev/null || echo 0)
  # ts 可能为空或非数字（锁文件写入被中断），兜底为 0 视为新鲜锁，避免算术表达式报错
  case "$ts" in
    ''|*[!0-9]*) ts=0 ;;
  esac
  if [ "$(( now - ts ))" -gt "$LOCK_STALE_SECONDS" ]; then
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

supervisor_running() {
  local found
  found=$(powershell -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -like '*supervisor.py*' } | Select-Object -ExpandProperty ProcessId" 2>/dev/null | grep -Eo '[0-9]+' | head -1)
  [ -n "$found" ]
}

echo "=============================================="
echo "  确保服务在运行（幂等）"
echo "=============================================="

# ── supervisor（唯一的进程管理者）──
echo ""
echo ">>> 检查 supervisor 守护进程 ..."
if supervisor_running; then
  echo "  ✅ supervisor 已在运行，跳过"
else
  echo "  supervisor 未运行，启动中..."
  # supervisor 前台运行，由 nohup 后台化；它会自行拉起并守护三服务
  nohup python scripts/supervisor.py > "$LOG_DIR/supervisor-bootstrap.log" 2>&1 &
  echo "  supervisor PID: $! (日志: $LOG_DIR/supervisor.log)"
fi

# ── 等待三服务就绪（supervisor 负责拉起）──
echo ""
echo ">>> 等待服务启动（由 supervisor 拉起 backend / frontend / worker）..."
BACKEND_OK=0
FRONTEND_OK=0
WORKER_OK=0
for _ in $(seq 1 60); do
  [ "$BACKEND_OK" -eq 1 ] || backend_healthy && BACKEND_OK=1
  [ "$FRONTEND_OK" -eq 1 ] || frontend_healthy && FRONTEND_OK=1
  [ "$WORKER_OK" -eq 1 ] || worker_running && WORKER_OK=1
  if [ "$BACKEND_OK" -eq 1 ] && [ "$FRONTEND_OK" -eq 1 ] && [ "$WORKER_OK" -eq 1 ]; then break; fi
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

if worker_running; then
  echo "  ✅ worker 已就绪（日志: $LOG_DIR/worker.log）"
else
  echo "  ❌ worker 未就绪，请检查 $LOG_DIR/worker.log"
fi

echo ""
echo "=============================================="
echo "  完成。日志目录: $LOG_DIR/"
echo "=============================================="
