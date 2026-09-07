@echo off
REM AI 穿搭素材库 — 前端项目打包脚本（Windows 入口，委托给 bash 执行 build_web.sh）
REM 用法:
REM   scripts\build_web.bat           只构建，产物在 web\dist
REM   scripts\build_web.bat --zip     构建后把 web\dist 打成 dist-<时间戳>.zip
REM   scripts\build_web.bat --no-install  跳过「缺 node_modules 时 npm install」
REM 说明: 需 Git Bash（bash 在 PATH）或已安装 Bash；否则回退直接调用 npm run build。

setlocal
cd /d "%~dp0.."

where bash >nul 2>nul
if %errorlevel%==0 (
  echo 使用 Git Bash 执行 scripts\build_web.sh %*
  bash scripts\build_web.sh %*
  goto :end
)

echo 未找到 bash，回退为直接调用 npm run build ...
if not exist "web\node_modules" (
  echo 未检测到 web\node_modules，执行 npm install ...
  pushd web
  call npm install
  if errorlevel 1 (popd & echo npm install 失败 & goto :end)
  popd
)
pushd web
call npm run build
if errorlevel 1 (popd & echo 前端构建失败 & goto :end)
popd
echo 产物目录: web\dist
if "%~1"=="--zip" (
  powershell -NoProfile -Command "Compress-Archive -Path 'web\dist' -DestinationPath ('dist-' + (Get-Date -Format 'yyyy-MM-dd_HHmmss') + '.zip') -Force"
)

:end
endlocal
exit /b %errorlevel%