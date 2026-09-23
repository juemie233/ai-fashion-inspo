@echo off
rem ============================================================
rem OpenViking Index Sync - one-click launcher (Windows)
rem Usage: double-click scripts\sync_openviking.bat
rem        scripts\sync_openviking.bat --full
rem        scripts\sync_openviking.bat --dry-run
rem
rem It locates Git Bash and drives scripts\sync_openviking.sh,
rem which incrementally syncs project code + docs + DB schema
rem into the OpenViking index (viking://resources/fashion-inspo/).
rem   (no flag)   incremental: upload only content that changed
rem   --full      ignore the state file, force a full upload
rem   --dry-run   preview only: no upload, no state write
rem Tests are excluded from the index.
rem Idempotent (upsert), safe to run repeatedly.
rem
rem NOTE 1: keep this file ASCII-only. Line 1 switches the console
rem         to code page 65001 so the UTF-8 Chinese output printed
rem         by the bash/Python side renders correctly; Chinese text
rem         in this .bat would break that rendering.
rem NOTE 2: cmd.exe requires CRLF line endings in .bat files. Do
rem         not let an editor rewrite this file with bare LF.
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

"%BASH%" "%SCRIPT_DIR%sync_openviking.sh" %*
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
