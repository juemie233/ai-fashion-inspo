#!/bin/bash
# AI 穿搭素材库 — 查看各服务健康状态（后端 / 前端 / worker + 资源占用）
# 用法: bash scripts/status.sh
cd "$(dirname "$0")/.."
python scripts/status.py
