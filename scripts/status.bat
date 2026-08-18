@echo off
chcp 936 >nul
rem AI 穿搭素材库 — 查看各服务健康状态（后端 / 前端 / worker + 资源占用）
rem 用法: scripts\status.bat
rem status.py 固定输出 UTF-8，切回 65001 保证中文显示正常
chcp 65001 >nul
cd /d "%~dp0.."
python scripts\status.py
