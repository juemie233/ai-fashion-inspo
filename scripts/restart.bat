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
rem
rem 自「服务守护」改造后，进程由 supervisor 统一管理：
rem   先停 supervisor（级联停三服务）→ 兜底杀残留进程 → 启动 supervisor
rem   （supervisor 再拉起三服务）。
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

rem ── 0. 停止 supervisor（守护进程；级联停止三服务）──
echo.
echo ^>^>^> [0/4] 停止 supervisor ...
for /f "usebackq tokens=*" %%p in (`powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*supervisor.py*' } | Select-Object -ExpandProperty ProcessId"`) do (
  taskkill /F /T /PID %%p >nul 2>&1
  if not errorlevel 1 echo   已终止 supervisor 进程树 PID %%p
)
ping -n 2 127.0.0.1 >nul

rem ── 1. 停止后端 ──
echo.
echo ^>^>^> [1/4] 停止后端（端口 %BACKEND_PORT%）...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%BACKEND_PORT%" ^| findstr LISTENING') do (
  taskkill /F /T /PID %%p >nul 2>&1
  if not errorlevel 1 echo   已终止进程树 PID %%p
)

rem 兜底：按命令行匹配 uvicorn reloader 主进程 + spawn worker 子进程
for /f "usebackq tokens=*" %%p in (`powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and ($_.CommandLine -like '*uvicorn app.main*' -or $_.CommandLine -like '*spawn_main*') } | Select-Object -ExpandProperty ProcessId"`) do (
  taskkill /F /T /PID %%p >nul 2>&1
  if not errorlevel 1 echo   已终止 uvicorn 进程 PID %%p
)

ping -n 2 127.0.0.1 >nul

netstat -ano | findstr ":%BACKEND_PORT%" | findstr LISTENING >nul 2>&1
if errorlevel 1 (
  echo   ✅ 后端已停止
) else (
  echo   ⚠️  警告：端口 %BACKEND_PORT% 可能仍被占用
)

rem ── 2. 停止前端 ──
echo.
echo ^>^>^> [2/4] 停止前端（端口 %FRONTEND_PORT%）...
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
echo ^>^>^> [3/4] 停止 worker ...
for /f "usebackq tokens=*" %%p in (`powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*app.worker*' } | Select-Object -ExpandProperty ProcessId"`) do (
  taskkill /F /T /PID %%p >nul 2>&1
  if not errorlevel 1 echo   已终止 worker 进程 PID %%p
)
ping -n 2 127.0.0.1 >nul

rem ── 4. 启动 supervisor（由它拉起三服务）──
echo.
echo ^>^>^> [4/4] 启动 supervisor ...
if not exist "web\node_modules" (
  echo   ❌ 未检测到 web\node_modules，请先执行: cd web ^&^& npm install
  exit /b 1
)
start "fashion-supervisor" /B cmd /c "python scripts\supervisor.py > logs\supervisor-bootstrap.log 2>&1"
echo   supervisor 日志: %LOG_DIR%\supervisor.log

rem ── 验证（轮询，最多 60 秒）──
echo.
echo ^>^>^> 等待服务启动...
set BACKEND_OK=0
set FRONTEND_OK=0
for /l %%i in (1,1,60) do (
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

rem worker 无 HTTP 端口，通过进程存在确认（轮询，最多 20 秒）
set WORKER_OK=0
for /l %%i in (1,1,20) do (
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
