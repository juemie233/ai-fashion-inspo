#!/bin/bash
# AI 穿搭灵感库 — 一键保存 Git
# 用法: bash scripts/git_save.sh "提交描述"

cd "$(dirname "$0")/.."

MESSAGE="${1:-chore: 阶段性保存}"

echo ">>> 暂存所有变更..."
git add -A

echo ">>> 提交中..."
git commit -m "$MESSAGE

Co-Authored-By: AI 助手 <noreply@anthropic.com>" 2>&1

echo ""
echo ">>> 最近 3 次提交:"
git log --oneline -3
