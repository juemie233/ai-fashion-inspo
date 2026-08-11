#!/bin/bash
# AI 穿搭灵感库 — 代码行数统计
# 用法: bash scripts/count_lines.sh

cd "$(dirname "$0")/.."

echo ""
echo "   === AI 穿搭灵感库 - 代码量统计 ==="
echo ""

backend=$(find backend -name "*.py" -not -path "*/__pycache__/*" -exec cat {} + | wc -l)
echo "   后端 Python            $(printf '%6s' $backend) 行"

web=$(find web/src -type f \( -name "*.ts" -o -name "*.vue" -o -name "*.css" \) -exec cat {} + | wc -l)
echo "   Web 前端 (Vue/TS/CSS)  $(printf '%6s' $web) 行"

mobile=$(find mobile -type f \( -name "*.tsx" -o -name "*.ts" -o -name "*.json" \) -exec cat {} + | wc -l)
echo "   移动端 (React Native)  $(printf '%6s' $mobile) 行"

ext=$(find browser-extension -type f \( -name "*.js" -o -name "*.html" -o -name "*.css" -o -name "*.json" \) -exec cat {} + | wc -l)
echo "   浏览器插件             $(printf '%6s' $ext) 行"

scripts_count=$(find scripts -name "*.py" -not -name "count_lines.py" -exec cat {} + | wc -l)
echo "   脚本                   $(printf '%6s' $scripts_count) 行"

shared=$(find shared -type f -name "*.ts" -exec cat {} + | wc -l)
echo "   共享类型               $(printf '%6s' $shared) 行"

docs=$(find . -maxdepth 1 -type f \( -name "*.md" -o -name ".gitignore" \) -exec cat {} + 2>/dev/null | wc -l)
docs_backend=$(find backend -maxdepth 1 -type f \( -name "*.txt" -o -name ".env" -o -name ".gitignore" \) -exec cat {} + 2>/dev/null | wc -l)
echo "   文档/配置              $(printf '%6s' $((docs + docs_backend))) 行"

total=$(find . -type f \( -name "*.py" -o -name "*.ts" -o -name "*.tsx" -o -name "*.vue" -o -name "*.css" -o -name "*.js" -o -name "*.html" -o -name "*.json" -o -name "*.md" \) -not -path "*/node_modules/*" -not -path "*/__pycache__/*" -not -path "*/dist/*" -not -path "*/.git/*" -not -path "*/package-lock.json" -exec cat {} + | wc -l)
echo "   ─────────────────────────────────"
echo "   总计                   $(printf '%6s' $total) 行"
echo ""

files=$(find . -type f \( -name "*.py" -o -name "*.ts" -o -name "*.tsx" -o -name "*.vue" -o -name "*.css" -o -name "*.js" -o -name "*.html" -o -name "*.json" -o -name "*.md" \) -not -path "*/node_modules/*" -not -path "*/__pycache__/*" -not -path "*/dist/*" -not -path "*/.git/*" -not -path "*/package-lock.json" | wc -l)
echo "   ${files} 个文件"
echo ""
