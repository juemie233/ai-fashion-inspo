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

# 定义列宽
date_w=12
commits_w=10
adds_w=10
dels_w=10
avg_add_w=12
avg_del_w=12

# 输出表头（英文，避免多字节对齐问题）
printf "┌"
for w in $date_w $commits_w $adds_w $dels_w $avg_add_w $avg_del_w; do
    printf "%s" "$(printf '─%.0s' $(seq 1 $((w+2))))"
    printf "┬"
done
# 去掉最后一个多余的 ┬
printf "\b┐\n"

printf "│ %-${date_w}s │ %${commits_w}s │ %${adds_w}s │ %${dels_w}s │ %${avg_add_w}s │ %${avg_del_w}s │\n" \
    "Date" "Commits" "Additions" "Deletions" "Avg Add" "Avg Del"

printf "├"
for w in $date_w $commits_w $adds_w $dels_w $avg_add_w $avg_del_w; do
    printf "%s" "$(printf '─%.0s' $(seq 1 $((w+2))))"
    printf "┼"
done
printf "\b┤\n"

# 提取数据并排序
git log --pretty=tformat:"%cd" --shortstat --no-renames --date=short | \
awk '
    /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/ {
        current_date = $0
        commits[current_date] += 1
        next
    }
    /files? changed/ {
        if (current_date == "") next
        add = 0; del = 0
        if (match($0, /[0-9]+ insertions?/)) {
            s = substr($0, RSTART, RLENGTH)
            gsub(/[^0-9]/, "", s)
            add = s + 0
        }
        if (match($0, /[0-9]+ deletions?/)) {
            s = substr($0, RSTART, RLENGTH)
            gsub(/[^0-9]/, "", s)
            del = s + 0
        }
        adds[current_date] += add
        dels[current_date] += del
    }
    END {
        for (d in commits) {
            avg_add = adds[d] / commits[d]
            avg_del = dels[d] / commits[d]
            printf "%-12s %10d %10d %10d %12.1f %12.1f\n", d, commits[d], adds[d], dels[d], avg_add, avg_del
        }
    }
' | sort -k1 | \
awk -v date_w=$date_w -v commits_w=$commits_w -v adds_w=$adds_w -v dels_w=$dels_w -v avg_add_w=$avg_add_w -v avg_del_w=$avg_del_w '
{
    printf "│ %-12s │ %10d │ %10d │ %10d │ %12.1f │ %12.1f │\n", $1, $2, $3, $4, $5, $6
}
END {
    printf "└"
    for (i=1; i<=6; i++) {
        w = (i==1 ? date_w : (i==2 ? commits_w : (i==3 ? adds_w : (i==4 ? dels_w : (i==5 ? avg_add_w : avg_del_w)))))
        printf "%s", substr("────────────────────────────────", 1, w+2)
        if (i<6) printf "┴"
    }
    printf "┘\n"
}'