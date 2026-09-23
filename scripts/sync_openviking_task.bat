@echo off
REM OpenViking index sync wrapper for Windows Task Scheduler (schtasks).
REM
REM Incremental: only files whose content changed since the last run are
REM uploaded, plus a regenerated decision ledger (docs/decisions/) and the
REM two generated DB schema docs (database/*.md).
REM
REM Register (run manually; project convention: do not auto-register):
REM   schtasks /Create /SC DAILY /TN "FashionInspo-OpenVikingSync" /ST 04:30 /F /TR "\"<full-path-to-this-bat>\""
REM Query:   schtasks /Query  /TN "FashionInspo-OpenVikingSync"
REM Run now: schtasks /Run    /TN "FashionInspo-OpenVikingSync"
REM Delete:  schtasks /Delete /TN "FashionInspo-OpenVikingSync" /F
REM
REM PREREQUISITE: the OpenViking server must be listening on 127.0.0.1:1933.
REM Start it with the desktop one-click launcher (or openviking-server). If it is
REM down this wrapper exits non-zero and logs the reason -- it never starts or
REM stops the server itself (project convention: user owns service lifecycle).
REM
REM NOTE: keep this file ASCII-only and CRLF-terminated, like backup_task.bat.

setlocal

REM Project root = parent of the scripts directory holding this file.
set "PROJECT_DIR=%~dp0.."

REM Log file (logs/ is git-ignored).
set "LOG_DIR=%PROJECT_DIR%\logs"
set "LOG_FILE=%LOG_DIR%\sync_openviking.log"

REM Locate Git Bash (common install locations; add more if needed).
set "BASH_EXE="
if exist "D:\Program Files (x86)\Git\bin\bash.exe" set "BASH_EXE=D:\Program Files (x86)\Git\bin\bash.exe"
if exist "C:\Program Files\Git\bin\bash.exe" set "BASH_EXE=C:\Program Files\Git\bin\bash.exe"
if exist "C:\Program Files (x86)\Git\bin\bash.exe" set "BASH_EXE=C:\Program Files (x86)\Git\bin\bash.exe"

if not defined BASH_EXE (
  echo [sync_task] Git Bash ^(bash.exe^) not found. Edit this script to set its path.
  exit /b 1
)

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

REM Convert backslashes to forward slashes for Git Bash.
set "PROJECT_BASH=%PROJECT_DIR:\=/%"
set "SCRIPT_PATH=%PROJECT_BASH%/scripts/sync_openviking.sh"

REM Warm up the embedding model first: after an idle gap Ollama unloads it, and a
REM cold load makes the first embedding burst slow enough to fail at the client
REM (see the 2026-09-23 retry storm) -- warming costs a second and removes that
REM cold path entirely. Ollama must already be listening, so probe it first.
echo [sync_task] %date% %time% warming up embedding model >> "%LOG_FILE%"
set "WARMUP_JSON=%TEMP%\ov_sync_warmup.json"
echo {"model":"qwen3-embedding:0.6b","input":"warmup"}>"%WARMUP_JSON%"
curl -s -o nul -m 300 -X POST -H "Content-Type: application/json" --data "@%WARMUP_JSON%" http://127.0.0.1:11434/api/embed >> "%LOG_FILE%" 2>&1
del "%WARMUP_JSON%" >nul 2>&1

echo [sync_task] %date% %time% starting >> "%LOG_FILE%"
"%BASH_EXE%" -c "bash '%SCRIPT_PATH%'" >> "%LOG_FILE%" 2>&1
set "RC=%errorlevel%"
echo [sync_task] finished with exit code %RC% >> "%LOG_FILE%"
endlocal & exit /b %RC%
