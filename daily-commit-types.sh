#!/bin/bash

# 检查是否在 Git 仓库中
if ! git rev-parse --git-dir >/dev/null 2>&1; then
    echo "错误：当前目录不是 Git 仓库。"
    exit 1
fi

# 检查是否有提交记录
if ! git rev-parse --verify HEAD >/dev/null 2>&1; then
    echo "错误：没有找到任何提交记录。"
    exit 1
fi

# 定义已知提交类型（用空格分隔），已增加 test
types_list="feat fix refactor style docs chore test"

# 列宽定义
date_width=12
col_width=10

# 提取数据并统计，输出带边框的表格
git log --pretty=tformat:"%ad|%s" --date=short | \
awk -F'|' -v types="$types_list" -v date_w="$date_width" -v col_w="$col_width" '
function rep(ch, n,   s) {
    s = sprintf("%*s", n, "")
    gsub(/ /, ch, s)
    return s
}

BEGIN {
    # 将传入的类型字符串拆分为数组
    n = split(types, type_names, " ")
    # 总列数 = 日期 + n 个类型 + other
    total_cols = 1 + n + 1

    # 列标题
    header[1] = "Date"
    for (i = 1; i <= n; i++) {
        header[i+1] = type_names[i]
    }
    header[total_cols] = "other"

    # 各列宽度
    col_widths[1] = date_w
    for (i = 2; i <= total_cols; i++) {
        col_widths[i] = col_w
    }

    # 生成顶部边框
    top = "┌"
    for (i = 1; i <= total_cols; i++) {
        top = top rep("─", col_widths[i] + 2)
        if (i < total_cols) top = top "┬"
    }
    top = top "┐"

    # 生成表头行
    header_line = "│"
    for (i = 1; i <= total_cols; i++) {
        header_line = header_line sprintf(" %-*s │", col_widths[i], header[i])
    }

    # 生成表头与数据的分隔线
    sep = "├"
    for (i = 1; i <= total_cols; i++) {
        sep = sep rep("─", col_widths[i] + 2)
        if (i < total_cols) sep = sep "┼"
    }
    sep = sep "┤"

    # 生成底部边框
    bottom = "└"
    for (i = 1; i <= total_cols; i++) {
        bottom = bottom rep("─", col_widths[i] + 2)
        if (i < total_cols) bottom = bottom "┴"
    }
    bottom = bottom "┘"
}

{
    date = $1
    subj = $2

    # 提取类型：取冒号前的部分，并移除 scope
    if (match(subj, /^[^:]+:/)) {
        type = substr(subj, RSTART, RLENGTH-1)
        # 只保留字母部分（如 feat(ui) -> feat）
        if (match(type, /^[a-zA-Z]+/)) {
            type = substr(type, RSTART, RLENGTH)
        }
        type = tolower(type)
    } else {
        type = "other"
    }

    # 检查类型是否在已知列表中
    known = 0
    for (i = 1; i <= n; i++) {
        if (type == type_names[i]) {
            known = 1
            break
        }
    }
    if (!known) type = "other"

    # 计数
    count[date, type]++
    dates[date] = 1
}

END {
    # 按日期排序
    m = asorti(dates, sorted_dates)

    # 输出表格
    print top
    print header_line
    print sep

    for (i = 1; i <= m; i++) {
        d = sorted_dates[i]
        line = "│"
        line = line sprintf(" %-*s │", date_w, d)
        for (j = 1; j <= n; j++) {
            t = type_names[j]
            line = line sprintf(" %*d │", col_w, count[d, t])
        }
        line = line sprintf(" %*d │", col_w, count[d, "other"])
        print line
    }

    print bottom
}
'