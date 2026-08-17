#!/bin/bash
# AI 穿搭灵感库 — 代码行数统计（独立统计：总行数 = 代码行数 + 空行数 + 注释行数）
# 用法: bash scripts/count_lines.sh

cd "$(dirname "$0")/.."

# 统计函数：$1=文件列表（换行分隔，可为空），$2=注释行正则（awk 语法，行首匹配）
# 输出: 代码行数 空行数 注释行数（空格分隔）
count_style() {
  local files="$1" cre="$2"
  if [ -z "$files" ]; then
    echo "0 0 0"
    return
  fi
  echo "$files" | xargs -r cat 2>/dev/null | awk -v cre="$cre" '
    /^[[:space:]]*$/ { blank++; next }
    cre != "" && $0 ~ cre { comment++; next }
    { code++ }
    END { printf "%d %d %d", code + 0, blank + 0, comment + 0 }
  '
}

# 注释行正则（忽略前导空白；不同语言注释符号取并集；awk 中字面 * 用 [*]）
RE_PY='^[[:space:]]*#'                                          # Python / Shell / gitignore
RE_TS='^[[:space:]]*(//|/[*]|[*]|<!--)'                          # TS / JS / CSS / Vue
RE_HTML='^[[:space:]]*<!--'                                      # HTML / Markdown 注释
RE_BAT='^[[:space:]]*(rem|REM|::)'                               # Windows 批处理

echo ""
echo "   === AI 穿搭灵感库 - 代码量统计（总行数 = 代码 + 空行 + 注释） ==="
echo ""

# 1. 后端 Python（backend 全部 .py，含 backend/scripts）
files=$(find backend -name "*.py" -not -path "*/__pycache__/*")
read code blank comment <<< "$(count_style "$files" "$RE_PY")"
total=$((code + blank + comment))
echo "   后端 Python             总 $(printf '%6s' $total)  = 代码 $(printf '%6s' $code) + 空行 $(printf '%6s' $blank) + 注释 $(printf '%6s' $comment)"

# 2. Web 前端（Vue/TS/CSS）
files=$(find web/src -type f \( -name "*.ts" -o -name "*.vue" -o -name "*.css" \))
read code blank comment <<< "$(count_style "$files" "$RE_TS")"
total=$((code + blank + comment))
echo "   Web 前端 (Vue/TS/CSS)   总 $(printf '%6s' $total)  = 代码 $(printf '%6s' $code) + 空行 $(printf '%6s' $blank) + 注释 $(printf '%6s' $comment)"

# 3. 移动端（React Native：TSX/TS/JSON）
files=$(find mobile -type f \( -name "*.tsx" -o -name "*.ts" -o -name "*.json" \))
read code blank comment <<< "$(count_style "$files" "$RE_TS")"
total=$((code + blank + comment))
echo "   移动端 (React Native)   总 $(printf '%6s' $total)  = 代码 $(printf '%6s' $code) + 空行 $(printf '%6s' $blank) + 注释 $(printf '%6s' $comment)"

# 4. 浏览器插件（JS/HTML/CSS/JSON）
files=$(find browser-extension -type f \( -name "*.js" -o -name "*.html" -o -name "*.css" -o -name "*.json" \))
read code blank comment <<< "$(count_style "$files" "$RE_TS")"
total=$((code + blank + comment))
echo "   浏览器插件              总 $(printf '%6s' $total)  = 代码 $(printf '%6s' $code) + 空行 $(printf '%6s' $blank) + 注释 $(printf '%6s' $comment)"

# 5. 脚本（根目录 scripts：Python/Shell/Bat）
files=$(find scripts -maxdepth 1 -type f \( -name "*.py" -o -name "*.sh" -o -name "*.bat" \) -not -name "count_lines*")
read code blank comment <<< "$(count_style "$files" "$RE_PY|$RE_BAT")"
total=$((code + blank + comment))
echo "   脚本 (scripts/)         总 $(printf '%6s' $total)  = 代码 $(printf '%6s' $code) + 空行 $(printf '%6s' $blank) + 注释 $(printf '%6s' $comment)"

# 6. 共享类型（shared TS）
files=$(find shared -type f -name "*.ts")
read code blank comment <<< "$(count_style "$files" "$RE_TS")"
total=$((code + blank + comment))
echo "   共享类型 (shared/)      总 $(printf '%6s' $total)  = 代码 $(printf '%6s' $code) + 空行 $(printf '%6s' $blank) + 注释 $(printf '%6s' $comment)"

# 7. 文档/配置（根目录 md/gitignore + backend 配置）
files=$( { find . -maxdepth 1 -type f \( -name "*.md" -o -name ".gitignore" \); find backend -maxdepth 1 -type f \( -name "*.txt" -o -name ".env" -o -name ".gitignore" \); } 2>/dev/null )
read code blank comment <<< "$(count_style "$files" "$RE_HTML")"
total=$((code + blank + comment))
echo "   文档/配置               总 $(printf '%6s' $total)  = 代码 $(printf '%6s' $code) + 空行 $(printf '%6s' $blank) + 注释 $(printf '%6s' $comment)"

# 8. 总计（全仓代码文件，排除依赖与产物目录）
files=$(find . -type f \( -name "*.py" -o -name "*.ts" -o -name "*.tsx" -o -name "*.vue" -o -name "*.css" -o -name "*.js" -o -name "*.html" -o -name "*.json" -o -name "*.md" -o -name "*.sh" -o -name "*.bat" \) \
  -not -path "*/node_modules/*" -not -path "*/__pycache__/*" -not -path "*/dist/*" \
  -not -path "*/.git/*" -not -path "*/.claude/*" -not -path "*/package-lock.json")
read code blank comment <<< "$(count_style "$files" "$RE_PY|$RE_TS|$RE_BAT")"
total=$((code + blank + comment))
echo "   ─────────────────────────────────────────────────────────"
echo "   总计                    总 $(printf '%6s' $total)  = 代码 $(printf '%6s' $code) + 空行 $(printf '%6s' $blank) + 注释 $(printf '%6s' $comment)"
echo ""
echo "   $(printf '%s' "$files" | wc -l) 个文件"
echo ""
