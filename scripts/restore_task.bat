@echo off
REM AI fashion-inspo - restore wrapper. Restore is a rare, interactive operation.
REM
REM Usage:
REM   restore_task.bat <backup-dir> [--force] [--allow-overwrite] [--from-sql]
REM   restore_task.bat                 (no args restores the latest SUCCESS backup)
REM
REM Examples:
REM   restore_task.bat E:\fashion-inspo-backups\2026-08-26_102630
REM   restore_task.bat E:\fashion-inspo-backups\2026-08-26_102630 --from-sql
REM
REM The script does NOT stop/start services. Stop the backend/worker first.

setlocal

REM Where backups live (must match backup_task.bat). Used only when no arg given.
set "BACKUP_ROOT=E:\fashion-inspo-backups"

set "PROJECT_DIR=%~dp0.."

set "BASH_EXE="
if exist "D:\Program Files (x86)\Git\bin\bash.exe" set "BASH_EXE=D:\Program Files (x86)\Git\bin\bash.exe"
if exist "C:\Program Files\Git\bin\bash.exe" set "BASH_EXE=C:\Program Files\Git\bin\bash.exe"
if exist "C:\Program Files (x86)\Git\bin\bash.exe" set "BASH_EXE=C:\Program Files (x86)\Git\bin\bash.exe"

if not defined BASH_EXE (
  echo [restore_task] Git Bash ^(bash.exe^) not found. Edit this script to set its path.
  exit /b 1
)

set "PROJECT_BASH=%PROJECT_DIR:\=/%"
set "SCRIPT_PATH=%PROJECT_BASH%/scripts/restore_data.sh"

REM If a backup dir was supplied, pass it (and any extra flags) straight through.
if not "%~1"=="" (
  set "BACKUP_BASH=%~1"
  set "BACKUP_BASH=%BACKUP_BASH:\=/%"
  echo [restore_task] restoring from %BACKUP_BASH%
  "%BASH_EXE%" -c "bash '%SCRIPT_PATH%' '%BACKUP_BASH%' %2 %3 %4 %5"
  goto :done
)

REM No arg: find the latest backup with a SUCCESS marker under BACKUP_ROOT.
echo [restore_task] no backup dir given, looking for latest SUCCESS backup under %BACKUP_ROOT% ...
set "LATEST="
for /f "delims=" %%d in ('dir /b /ad /o-n "%BACKUP_ROOT%" 2^>nul') do (
  if not defined LATEST if exist "%BACKUP_ROOT%\%%d\SUCCESS" set "LATEST=%BACKUP_ROOT%\%%d"
)
if not defined LATEST (
  echo [restore_task] no SUCCESS backup found under %BACKUP_ROOT%.
  echo [restore_task] pass the backup directory explicitly.
  exit /b 1
)
set "LATEST_BASH=%LATEST:\=/%"
echo [restore_task] latest: %LATEST_BASH%
"%BASH_EXE%" -c "bash '%SCRIPT_PATH%' '%LATEST_BASH%'"

:done
set "RC=%errorlevel%"
echo [restore_task] finished with exit code %RC%
endlocal & exit /b %RC%
