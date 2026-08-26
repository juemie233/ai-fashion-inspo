@echo off
REM AI fashion-inspo - daily backup wrapper for Windows Task Scheduler (schtasks).
REM
REM Register (run manually; project convention: do not auto-register):
REM   schtasks /Create /SC DAILY /TN "FashionInspo-Backup" /TR "full-path-to-this-bat" /ST 03:00 /F
REM Query:   schtasks /Query  /TN "FashionInspo-Backup"
REM Run now: schtasks /Run    /TN "FashionInspo-Backup"
REM Delete:  schtasks /Delete /TN "FashionInspo-Backup" /F

setlocal

REM Backup target (a separate physical SSD to survive project-disk failure). Edit as needed.
set "BACKUP_TARGET=E:\fashion-inspo-backups"

REM Project root = parent of the scripts directory holding this file.
set "PROJECT_DIR=%~dp0.."

REM Locate Git Bash (common install locations; add more if needed).
set "BASH_EXE="
if exist "D:\Program Files (x86)\Git\bin\bash.exe" set "BASH_EXE=D:\Program Files (x86)\Git\bin\bash.exe"
if exist "C:\Program Files\Git\bin\bash.exe" set "BASH_EXE=C:\Program Files\Git\bin\bash.exe"
if exist "C:\Program Files (x86)\Git\bin\bash.exe" set "BASH_EXE=C:\Program Files (x86)\Git\bin\bash.exe"

if not defined BASH_EXE (
  echo [backup_task] Git Bash ^(bash.exe^) not found. Edit this script to set its path.
  exit /b 1
)

REM Convert backslashes to forward slashes for Git Bash.
set "PROJECT_BASH=%PROJECT_DIR:\=/%"
set "TARGET_BASH=%BACKUP_TARGET:\=/%"
set "SCRIPT_PATH=%PROJECT_BASH%/scripts/backup_data.sh"

echo [backup_task] %date% %time% starting backup to %BACKUP_TARGET%
"%BASH_EXE%" -c "bash '%SCRIPT_PATH%' '%TARGET_BASH%'"
set "RC=%errorlevel%"
echo [backup_task] finished with exit code %RC%
endlocal & exit /b %RC%
