#!/bin/bash

# 获取第一个提交的日期（ISO 8601 格式 YYYY-MM-DD）
first_date=$(git log --reverse --pretty=format:"%cd" --date=short | head -1)
if [ -z "$first_date" ]; then
    echo "错误：没有找到任何提交记录。"
    exit 1
fi

# 输出统计表头
echo "日期            提交数    新增行    删除行"

# 获取所有提交的日期和变更统计，按天汇总
git log --pretty=format:"%cd" --shortstat --no-renames --date=short | \
awk '
    BEGIN { current_date = "" }
    # 匹配日期行（如 2026-08-18）
    /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/ {
        current_date = $0
        next
    }
    # 匹配变更统计行（如 " 1 file changed, 2 insertions(+), 1 deletion(-)"）
    /file changed/ {
        if (current_date == "") next
        add = 0; del = 0
        # 提取新增行数（支持 "insertion" 或 "insertions"）
        if (match($0, /[0-9]+ insertions?/)) {
            s = substr($0, RSTART, RLENGTH)
            gsub(/[^0-9]/, "", s)
            add = s + 0
        }
        # 提取删除行数（支持 "deletion" 或 "deletions"）
        if (match($0, /[0-9]+ deletions?/)) {
            s = substr($0, RSTART, RLENGTH)
            gsub(/[^0-9]/, "", s)
            del = s + 0
        }
        commits[current_date] += 1
        adds[current_date] += add
        dels[current_date] += del
    }
    END {
        for (d in commits) {
            printf "%-12s %8d %8d %8d\n", d, commits[d], adds[d], dels[d]
        }
    }
' | sort -k1   # 按日期排序输出