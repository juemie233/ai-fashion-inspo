@echo off
rem ============================================================
rem OpenViking Index Sync - one-click launcher (Windows)
rem Usage: double-click scripts\sync_openviking.bat
rem It locates Git Bash and drives scripts\sync_openviking.sh,
rem which syncs project code + docs + DB schema into the
rem OpenViking index (viking://resources/fashion-inspo/).
rem Idempotent (upsert), safe to run repeatedly.
rem ============================================================
setlocal
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
set "BASH="

where bash >nul 2>nul
if not errorlevel 1 set "BASH=bash"

if not defined BASH if exist "%ProgramFiles%\Git\bin\bash.exe" set "BASH=%ProgramFiles%\Git\bin\bash.exe"
if not defined BASH if exist "%ProgramFiles(x86)%\Git\bin\bash.exe" set "BASH=%ProgramFiles(x86)%\Git\bin\bash.exe"
if not defined BASH if exist "D:\Program Files (x86)\Git\bin\bash.exe" set "BASH=D:\Program Files (x86)\Git\bin\bash.exe"
if not defined BASH (
    echo [ERROR] Git Bash not found. Please install Git for Windows.
    pause
    exit /b 1
)

"%BASH%" "%SCRIPT_DIR%sync_openviking.sh"
set "CODE=%errorlevel%"
if not "%CODE%"=="0" (
    echo.
    echo [ERROR] Sync failed with exit code %CODE%. See output above.
    pause
    exit /b %CODE%
)

echo.
echo [OK] OpenViking sync finished.
pause
