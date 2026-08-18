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
rem 人脸识别子服务（face-service，独立 Python 3.10 环境，端口 18889）
rem   不在 supervisor 管辖内，本脚本单独「先杀后启」并验证 /health。
rem ==============================================

rem 切到项目根目录（脚本位于 scripts\ 下）
cd /d "%~dp0.."

set BACKEND_PORT=18888
set FRONTEND_PORT=17777
set FACE_PORT=18889
set FACE_LOG=logs\face-service.log
set LOG_DIR=logs

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo ==============================================
echo   一键重启前后端 + worker + 人脸识别服务
echo ==============================================

rem ── 0. 停止 supervisor（守护进程；级联停止三服务）──
echo.
echo ^>^>^> [0/5] 停止 supervisor ...
for /f "usebackq tokens=*" %%p in (`powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*supervisor.py*' } | Select-Object -ExpandProperty ProcessId"`) do (
  taskkill /F /T /PID %%p >nul 2>&1
  if not errorlevel 1 echo   已终止 supervisor 进程树 PID %%p
)
ping -n 2 127.0.0.1 >nul

rem ── 1. 停止后端 ──
echo.
echo ^>^>^> [1/5] 停止后端（端口 %BACKEND_PORT%）...
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
echo ^>^>^> [2/5] 停止前端（端口 %FRONTEND_PORT%）...
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
echo ^>^>^> [3/5] 停止 worker ...
for /f "usebackq tokens=*" %%p in (`powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*app.worker*' } | Select-Object -ExpandProperty ProcessId"`) do (
  taskkill /F /T /PID %%p >nul 2>&1
  if not errorlevel 1 echo   已终止 worker 进程 PID %%p
)
ping -n 2 127.0.0.1 >nul

rem ── 4. 停止并启动人脸识别服务（face-service，独立于 supervisor）──
echo.
echo ^>^>^> [4/5] 重启人脸识别服务（端口 %FACE_PORT%）...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%FACE_PORT%" ^| findstr LISTENING') do (
  taskkill /F /T /PID %%p >nul 2>&1
  if not errorlevel 1 echo   已终止人脸服务进程树 PID %%p
)
rem 兜底：按命令行匹配 face-service 目录下的 uvicorn
for /f "usebackq tokens=*" %%p in (`powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*face-service*uvicorn*' } | Select-Object -ExpandProperty ProcessId"`) do (
  taskkill /F /T /PID %%p >nul 2>&1
  if not errorlevel 1 echo   已终止人脸服务进程 PID %%p
)
ping -n 2 127.0.0.1 >nul

set FACE_OK=0
set FACE_REQUIRED=1
if not exist "face-service\.venv\Scripts\python.exe" (
  echo   ⚠️  未检测到 face-service\.venv（Python 3.10 虚拟环境），跳过人脸服务
  set FACE_REQUIRED=0
) else (
  rem 显式补充 CUDA 运行时目录（onnxruntime-gpu 依赖 cublas/cudnn），
  rem 避免在环境变量设置前打开的旧终端里回退到 CPU 推理
  if exist "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8\bin" (
    set "PATH=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8\bin;%PATH%"
  )
  start "face-service" /B /D "%CD%\face-service" cmd /c ""%CD%\face-service\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port %FACE_PORT% > "%CD%\%FACE_LOG%" 2>&1"
  echo   人脸服务已启动（日志: %FACE_LOG%）
)

rem ── 5. 启动 supervisor（由它拉起三服务）──
echo.
echo ^>^>^> [5/5] 启动 supervisor ...
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

rem 人脸服务健康检查（/health；未部署时跳过）
if "!FACE_REQUIRED!"=="0" goto :face_skip
for /l %%i in (1,1,60) do (
  if !FACE_OK!==0 (
    curl -s "http://localhost:%FACE_PORT%/health" >nul 2>&1
    if not errorlevel 1 set FACE_OK=1
  )
  if !FACE_OK!==1 goto :face_ready
  ping -n 2 127.0.0.1 >nul
)
:face_ready
if "!FACE_OK!"=="1" (
  echo   ✅ 人脸服务已就绪: http://localhost:%FACE_PORT%
) else (
  echo   ❌ 人脸服务未就绪，请检查 %FACE_LOG%
)
goto :face_done
:face_skip
echo   ⏭️  人脸服务未部署（跳过验证）
:face_done

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
rem （人脸服务未部署时不要求就绪，不阻塞退出码）
set ALL_CORE_OK=0
if "!BACKEND_OK!!FRONTEND_OK!"=="11" set ALL_CORE_OK=1
if !ALL_CORE_OK!==0 exit /b 1
if !FACE_REQUIRED!==0 exit /b 0
if "!FACE_OK!"=="1" exit /b 0
exit /b 1
