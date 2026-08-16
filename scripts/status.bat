@echo off
rem AI 穿搭素材库 — 查看各服务健康状态（后端 / 前端 / worker + 资源占用）
rem 用法: scripts\status.bat
chcp 65001 >nul
cd /d "%~dp0.."
python scripts\status.py
