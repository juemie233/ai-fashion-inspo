@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

rem ==============================================
rem AI 穿搭素材库 — 一键重启前后端（Windows 批处理版）
rem 用法: scripts\restart.bat（建议在 cmd / Windows Terminal 中运行）
rem
rem 说明：后端「不」使用 uvicorn --reload。
rem   Windows 下 --reload 走 multiprocessing.spawn，文件变更触发重载时会以
rem   OSError: [WinError 87] 参数错误 崩溃（后端挂掉）；且强杀时易残留 spawn
rem   worker 子进程占用端口。本脚本靠「先杀后启」拿到最新代码，无需 --reload。
rem 本脚本可靠地终止所有相关进程（含孤儿 worker）后重启。
rem ==============================================

rem 切到项目根目录（脚本位于 scripts\ 下）
cd /d "%~dp0.."

set BACKEND_PORT=18888
set FRONTEND_PORT=17777
set LOG_DIR=logs

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo ==============================================
echo   一键重启前后端 + worker
echo ==============================================

rem ── 1. 停止后端 ──
echo.
echo ^>^>^> [1/6] 停止后端（端口 %BACKEND_PORT%）...

rem 1a. 杀掉监听端口的进程树（可能是 worker，taskkill /T 会级联杀掉其父 reloader）
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%BACKEND_PORT%" ^| findstr LISTENING') do (
  taskkill /F /T /PID %%p >nul 2>&1
  if not errorlevel 1 echo   已终止进程树 PID %%p
)

rem 1b. 兜底：按命令行匹配 uvicorn reloader 主进程 + spawn worker 子进程。
rem     uvicorn --reload 用 multiprocessing spawn 启动 server worker；若 reloader
rem     先被单独杀掉，worker 会变成孤儿继续持有端口（此时 netstat 只显示已死的
rem     reloader PID，导致 1a 无法命中）。必须同时按命令行清理 spawn worker。
for /f "usebackq tokens=*" %%p in (`powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and ($_.CommandLine -like '*uvicorn app.main*' -or $_.CommandLine -like '*spawn_main*') } | Select-Object -ExpandProperty ProcessId"`) do (
  taskkill /F /T /PID %%p >nul 2>&1
  if not errorlevel 1 echo   已终止 uvicorn 进程 PID %%p
)

ping -n 2 127.0.0.1 >nul

rem 验证端口已释放
netstat -ano | findstr ":%BACKEND_PORT%" | findstr LISTENING >nul 2>&1
if errorlevel 1 (
  echo   ✅ 后端已停止
) else (
  echo   ⚠️  警告：端口 %BACKEND_PORT% 可能仍被占用
)

rem ── 2. 停止前端 ──
echo.
echo ^>^>^> [2/6] 停止前端（端口 %FRONTEND_PORT%）...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%FRONTEND_PORT%" ^| findstr LISTENING') do (
  taskkill /F /T /PID %%p >nul 2>&1
  if not errorlevel 1 echo   已终止进程树 PID %%p
)
ping -n 2 127.0.0.1 >nul
netstat -ano | findstr ":%FRONTEND_PORT%" | findstr LISTENING >nul 2>&1
if errorlevel 1 (
  echo   ✅ 前端已停止
) else (
  echo   ⚠️  警告：端口 %FRONTEND_PORT% 可能仍被占用
)

rem ── 3. 停止 worker ──
echo.
echo ^>^>^> [3/6] 停止 worker ...
for /f "usebackq tokens=*" %%p in (`powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*app.worker*' } | Select-Object -ExpandProperty ProcessId"`) do (
  taskkill /F /T /PID %%p >nul 2>&1
  if not errorlevel 1 echo   已终止 worker 进程 PID %%p
)
ping -n 2 127.0.0.1 >nul

rem ── 4. 启动后端 ──
echo.
echo ^>^>^> [4/6] 启动后端 ...
pushd backend
rem PYTHONUTF8=1 让中文日志以 UTF-8 落盘，避免 Windows 默认 GBK 导致日志乱码
rem 默认仅绑定本机（127.0.0.1）：个人单机使用，避免局域网内其他设备访问私人素材。
start "fashion-backend" /B cmd /c "set PYTHONUTF8=1&& python -m uvicorn app.main:app --host 127.0.0.1 --port %BACKEND_PORT% > ..\logs\backend.log 2>&1"
popd
echo   后端日志: %LOG_DIR%\backend.log

rem ── 5. 启动前端 ──
echo.
echo ^>^>^> [5/6] 启动前端 ...
if not exist "web\node_modules" (
  echo   ❌ 未检测到 web\node_modules，请先执行: cd web ^&^& npm install
  exit /b 1
)
pushd web
rem 前端 dev server 同样默认仅本机：它代理后端 API，绑 0.0.0.0 会间接暴露后端
start "fashion-frontend" /B cmd /c "npm run dev -- --host 127.0.0.1 --port %FRONTEND_PORT% > ..\logs\frontend.log 2>&1"
popd
echo   前端日志: %LOG_DIR%\frontend.log

rem ── 6. 启动 worker ──
echo.
echo ^>^>^> [6/6] 启动 worker ...
pushd backend
rem 强制 UTF-8 输出，避免中文日志在 Windows 下被写成 GBK 导致就绪检测失败
start "fashion-worker" /B cmd /c "set PYTHONUTF8=1&& python -m app.worker > ..\logs\worker.log 2>&1"
popd
echo   worker 日志: %LOG_DIR%\worker.log

rem ── 验证（轮询，最多 30 秒）──
echo.
echo ^>^>^> 等待服务启动...
set BACKEND_OK=0
set FRONTEND_OK=0
for /l %%i in (1,1,30) do (
  if !BACKEND_OK!==0 (
    curl -s "http://localhost:%BACKEND_PORT%/api/health" >nul 2>&1
    if not errorlevel 1 set BACKEND_OK=1
  )
  if !FRONTEND_OK!==0 (
    curl -s "http://localhost:%FRONTEND_PORT%" >nul 2>&1
    if not errorlevel 1 set FRONTEND_OK=1
  )
  if !BACKEND_OK!==1 if !FRONTEND_OK!==1 goto :services_ready
  ping -n 2 127.0.0.1 >nul
)
:services_ready

if !BACKEND_OK!==1 (
  echo   ✅ 后端已就绪: http://localhost:%BACKEND_PORT%
  curl -s "http://localhost:%BACKEND_PORT%/api/health"
  echo.
) else (
  echo   ❌ 后端未就绪，请检查 %LOG_DIR%\backend.log
)

if !FRONTEND_OK!==1 (
  echo   ✅ 前端已就绪: http://localhost:%FRONTEND_PORT%
) else (
  echo   ❌ 前端未就绪，请检查 %LOG_DIR%\frontend.log
)

rem worker 无 HTTP 端口，通过检测进程是否存在确认（轮询，最多 15 秒）。
rem 用 PowerShell 按命令行匹配 app.worker，避免 findstr 对 UTF-8 中文日志匹配不可靠。
set WORKER_OK=0
for /l %%i in (1,1,15) do (
  powershell -NoProfile -Command "if (Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*app.worker*' }) { exit 0 } else { exit 1 }" >nul 2>&1
  if not errorlevel 1 set WORKER_OK=1
  if !WORKER_OK!==1 goto :worker_ready
  ping -n 2 127.0.0.1 >nul
)
:worker_ready

if !WORKER_OK!==1 (
  echo   ✅ worker 已启动（日志: %LOG_DIR%\worker.log）
) else (
  echo   ⚠️  worker 可能未就绪，请检查 %LOG_DIR%\worker.log
)

echo.
echo ==============================================
echo   完成。日志目录: %LOG_DIR%\
echo ==============================================

rem 任一服务未就绪则返回非零退出码，供自动化判断
if "!BACKEND_OK!!FRONTEND_OK!"=="11" (
  exit /b 0
) else (
  exit /b 1
)
