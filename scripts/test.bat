@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

rem ==============================================
rem AI 穿搭素材库 — 一键运行自动化测试（Windows 批处理版）
rem 用法: scripts\test.bat
rem       scripts\test.bat --cov   后端额外输出行级覆盖率报告
rem
rem 说明：与 test.sh 一致，用 node 直接调用本地二进制，规避
rem npx/npm 包装脚本在受限 PowerShell 下的执行策略问题。
rem ==============================================

rem 切到项目根目录（脚本位于 scripts\ 下）
cd /d "%~dp0.."

set "COV=%~1"

echo ===== 后端测试（pytest）=====
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
echo ===== 前端类型检查（vue-tsc）=====
cd web
node node_modules\vue-tsc\bin\vue-tsc.js --noEmit
set "TEST_EXIT=%errorlevel%"
cd ..
if not "%TEST_EXIT%"=="0" goto :fail

echo.
echo ===== 前端测试（vitest）=====
cd web
node node_modules\vitest\vitest.mjs run
set "TEST_EXIT=%errorlevel%"
cd ..
if not "%TEST_EXIT%"=="0" goto :fail

echo.
echo ✅ 全部测试通过
exit /b 0

:fail
echo.
echo ❌ 测试失败（退出码 %TEST_EXIT%）
exit /b 1
