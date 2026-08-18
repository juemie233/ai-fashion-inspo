#!/bin/bash
# AI 穿搭素材库 — 一键重启前后端 + worker + 人脸识别服务
# 用法: bash scripts/restart.sh
#
# 说明：后端「不」使用 uvicorn --reload。
#   Windows 下 --reload 走 multiprocessing.spawn，文件变更触发重载时会以
#   OSError: [WinError 87] 参数错误 崩溃（后端挂掉）；且强杀时易残留 spawn
#   worker 子进程占用端口。本脚本靠「先杀后启」拿到最新代码，无需 --reload。
#
# 自「服务守护」改造后，进程由 supervisor 统一管理：
#   先停 supervisor（级联停三服务）→ 兜底杀残留进程 → 启动 supervisor
#   （supervisor 再拉起三服务）。这样三服务总能拿到最新代码。
# 人脸识别子服务（face-service，独立 Python 3.10 环境，端口 18889）
#   不在 supervisor 管辖内，本脚本单独「先杀后启」并验证 /health。
# 本脚本可靠地终止所有相关进程（含孤儿 worker）后重启。

cd "$(dirname "$0")/.."

BACKEND_PORT=18888
FRONTEND_PORT=17777
FACE_PORT=18889
LOG_DIR="logs"
FACE_LOG="$LOG_DIR/face-service.log"

mkdir -p "$LOG_DIR"

echo "=============================================="
echo "  一键重启前后端 + worker + 人脸识别服务"
echo "=============================================="

# ── 0. 停止 supervisor（守护进程；级联停止三服务）──
echo ""
echo ">>> [0/5] 停止 supervisor ..."
for pid in $(powershell -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -like '*supervisor.py*' } | Select-Object -ExpandProperty ProcessId" 2>/dev/null | grep -Eo '[0-9]+'); do
  taskkill //F //T //PID "$pid" >/dev/null 2>&1 && echo "  已终止 supervisor 进程树 PID $pid"
done
sleep 1

# ── 1. 停止后端 ──
echo ""
echo ">>> [1/5] 停止后端 (端口 $BACKEND_PORT) ..."

# 1a. 杀掉监听端口的进程树（可能是 worker，taskkill /T 会级联杀掉其父 reloader）
killed=0
for pid in $(netstat -ano 2>/dev/null | grep -E ":$BACKEND_PORT( |$)" | grep -i LISTENING | awk '{print $NF}' | sort -u); do
  if [ -n "$pid" ]; then
    taskkill //F //T //PID "$pid" >/dev/null 2>&1 && { echo "  已终止进程树 PID $pid"; killed=1; }
  fi
done

# 1b. 兜底：按命令行匹配 uvicorn reloader 主进程 + spawn worker 子进程。
#     uvicorn --reload 用 multiprocessing spawn 启动 server worker；若 reloader
#     先被单独杀掉，worker 会变成孤儿继续持有端口（此时 netstat 只显示已死的
#     reloader PID，导致 1a 无法命中）。必须同时按命令行清理 spawn worker。
for pid in $(powershell -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -like '*uvicorn app.main*' -or \$_.CommandLine -like '*spawn_main*' } | Select-Object -ExpandProperty ProcessId" 2>/dev/null | grep -Eo '[0-9]+'); do
  taskkill //F //T //PID "$pid" >/dev/null 2>&1 && { echo "  已终止 uvicorn 进程 PID $pid"; killed=1; }
done

sleep 1

# 验证端口已释放
if netstat -ano 2>/dev/null | grep -E ":$BACKEND_PORT( |$)" | grep -qi LISTENING; then
  echo "  ⚠️  警告：端口 $BACKEND_PORT 可能仍被占用"
else
  echo "  ✅ 后端已停止"
fi

# ── 2. 停止前端 ──
echo ""
echo ">>> [2/5] 停止前端 (端口 $FRONTEND_PORT) ..."
for pid in $(netstat -ano 2>/dev/null | grep -E ":$FRONTEND_PORT( |$)" | grep -i LISTENING | awk '{print $NF}' | sort -u); do
  if [ -n "$pid" ]; then
    taskkill //F //T //PID "$pid" >/dev/null 2>&1 && echo "  已终止进程树 PID $pid"
  fi
done
sleep 1
if netstat -ano 2>/dev/null | grep -E ":$FRONTEND_PORT( |$)" | grep -qi LISTENING; then
  echo "  ⚠️  警告：端口 $FRONTEND_PORT 可能仍被占用"
else
  echo "  ✅ 前端已停止"
fi

# ── 3. 停止 worker ──
echo ""
echo ">>> [3/5] 停止 worker ..."
for pid in $(powershell -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -like '*app.worker*' } | Select-Object -ExpandProperty ProcessId" 2>/dev/null | grep -Eo '[0-9]+'); do
  taskkill //F //T //PID "$pid" >/dev/null 2>&1 && echo "  已终止 worker 进程 PID $pid"
done
sleep 1

# ── 4. 停止并启动人脸识别服务（face-service，独立于 supervisor）──
echo ""
echo ">>> [4/5] 重启人脸识别服务 (端口 $FACE_PORT) ..."
for pid in $(netstat -ano 2>/dev/null | grep -E ":$FACE_PORT( |$)" | grep -i LISTENING | awk '{print $NF}' | sort -u); do
  if [ -n "$pid" ]; then
    taskkill //F //T //PID "$pid" >/dev/null 2>&1 && echo "  已终止人脸服务进程树 PID $pid"
  fi
done
for pid in $(powershell -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -like '*face-service*uvicorn*' } | Select-Object -ExpandProperty ProcessId" 2>/dev/null | grep -Eo '[0-9]+'); do
  taskkill //F //T //PID "$pid" >/dev/null 2>&1 && echo "  已终止人脸服务进程 PID $pid"
done
sleep 1

FACE_OK=0
FACE_REQUIRED=1
if [ ! -f face-service/.venv/Scripts/python.exe ]; then
  echo "  ⚠️  未检测到 face-service/.venv（Python 3.10 虚拟环境），跳过人脸服务"
  FACE_REQUIRED=0
else
  # 显式补充 CUDA 运行时目录（onnxruntime-gpu 依赖 cublas/cudnn），
  # 避免在环境变量设置前打开的旧终端里回退到 CPU 推理
  if [ -d "/c/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v12.8/bin" ]; then
    export PATH="/c/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v12.8/bin:$PATH"
  fi
  ( cd face-service && nohup .venv/Scripts/python -m uvicorn app.main:app --host 0.0.0.0 --port "$FACE_PORT" > "../$FACE_LOG" 2>&1 & )
  echo "  人脸服务已启动（日志: $FACE_LOG）"
fi

# ── 5. 启动 supervisor（由它拉起三服务）──
echo ""
echo ">>> [5/5] 启动 supervisor ..."
if [ ! -d web/node_modules ]; then
  echo "  ❌ 未检测到 web/node_modules，请先执行: cd web && npm install"
  exit 1
fi
nohup python scripts/supervisor.py > "$LOG_DIR/supervisor-bootstrap.log" 2>&1 &
echo "  supervisor PID: $! (日志: $LOG_DIR/supervisor.log)"

# ── 验证（轮询，最多 60 秒）──
echo ""
echo ">>> 等待服务启动..."
BACKEND_OK=0
FRONTEND_OK=0
for _ in $(seq 1 60); do
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

# worker 无 HTTP 端口，通过进程存在确认（轮询，最多 20 秒）
WORKER_OK=0
for _ in $(seq 1 20); do
  if powershell -Command "if (Get-CimInstance Win32_Process | Where-Object { \$_.Name -eq 'python.exe' -and \$_.CommandLine -like '*app.worker*' }) { exit 0 } else { exit 1 }" >/dev/null 2>&1; then
    WORKER_OK=1
    break
  fi
  sleep 1
done

if [ "$WORKER_OK" -eq 1 ]; then
  echo "  ✅ worker 已启动（日志: $LOG_DIR/worker.log）"
else
  echo "  ⚠️  worker 可能未就绪，请检查 $LOG_DIR/worker.log"
fi

# 人脸服务健康检查（/health；未部署时跳过）
if [ "$FACE_REQUIRED" -eq 1 ]; then
  for _ in $(seq 1 60); do
    if curl -s "http://localhost:$FACE_PORT/health" >/dev/null 2>&1; then
      FACE_OK=1
      break
    fi
    sleep 1
  done
  if [ "$FACE_OK" -eq 1 ]; then
    echo "  ✅ 人脸服务已就绪: http://localhost:$FACE_PORT"
  else
    echo "  ❌ 人脸服务未就绪，请检查 $FACE_LOG"
  fi
else
  echo "  ⏭️  人脸服务未部署（跳过验证）"
fi

echo ""
echo "=============================================="
echo "  完成。日志目录: $LOG_DIR/"
echo "=============================================="

# 任一服务未就绪则返回非零退出码，供自动化判断
# （人脸服务未部署时不要求就绪，不阻塞退出码）
if [ "$BACKEND_OK" -eq 1 ] && [ "$FRONTEND_OK" -eq 1 ]; then
  if [ "$FACE_REQUIRED" -eq 0 ] || [ "$FACE_OK" -eq 1 ]; then
    exit 0
  fi
fi
exit 1
