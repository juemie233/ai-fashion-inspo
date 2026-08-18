@echo off
setlocal enabledelayedexpansion
chcp 936 >nul

rem ==============================================
rem AI 穿搭素材库 — 一键运行自动化测试（Windows 批处理版）
rem 用法: scripts\test.bat
rem       scripts\test.bat --cov   后端额外输出行级覆盖率报告
rem
rem 说明：与 test.sh 一致，用 node 直接调用本地二进制，规避
rem npx/npm 包装脚本在受限 PowerShell 下的执行策略问题。
rem ==============================================
rem pytest/vitest 输出 UTF-8，切 65001 保证终端显示正常；
rem 其后所有 echo 已 ASCII 化，避免 GBK 字节在 65001 下解析错位
chcp 65001 >nul

rem cd to project root (script located in scripts/)
cd /d "%~dp0.."

set "COV=%~1"

echo ===== backend tests (pytest) =====
cd backend
if /I "%COV%"=="--cov" (
  python -m pytest --cov --cov-report=term-missing
) else (
  python -m pytest
)
set "TEST_EXIT=%errorlevel%"
cd ..
if not "%TEST_EXIT%"=="0" goto :fail

echo.
echo ===== frontend type check (vue-tsc) =====
cd web
node node_modules\vue-tsc\bin\vue-tsc.js --noEmit
set "TEST_EXIT=%errorlevel%"
cd ..
if not "%TEST_EXIT%"=="0" goto :fail

echo.
echo ===== frontend tests (vitest) =====
cd web
node node_modules\vitest\vitest.mjs run
set "TEST_EXIT=%errorlevel%"
cd ..
if not "%TEST_EXIT%"=="0" goto :fail

echo.
echo [OK] All tests passed
exit /b 0

:fail
echo.
echo [X] Tests failed (exit code %TEST_EXIT%)
exit /b 1
