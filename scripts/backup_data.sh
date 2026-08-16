#!/bin/bash
# AI 穿搭素材库 — 手动数据备份脚本
# 用法: bash scripts/backup_data.sh [目标目录]
#
# 说明:
#   - 纯手动执行，不注册任何定时任务；需要备份时运行一次即可
#   - 默认备份到项目根目录 backups/YYYY-MM-DD_HHMMSS/；
#     建议传入其他磁盘/网盘路径，例如:
#       bash scripts/backup_data.sh D:/fashion-inspo-backups
#     （路径请用正斜杠）
#   - 不自动清理旧备份，磁盘空间请自行管理
#   - 建议在低峰期执行（备份期间新上传的文件可能不在本次备份内）
#
# 备份内容:
#   - SQLite 数据库：用 Python sqlite3 backup API 生成一致性快照，
#     后端运行中也能安全备份（WAL 模式下不会拿到半截数据）
#   - storage/ 素材文件：图片、缩略图、视频、垃圾桶、向量库（LanceDB）、
#     裁剪备份等全部数据（排除 logs 与 tmp 临时目录）
#   - 运行时配置：.env、prompt/model 配置等（这些文件不进 git）
#   - git HEAD 提交号（记录备份对应的代码版本）

cd "$(dirname "$0")/.."

TARGET="${1:-backups}"
STAMP=$(date +%Y-%m-%d_%H%M%S)
DEST="$TARGET/$STAMP"

if ! mkdir -p "$DEST"; then
  echo "无法创建备份目录: $DEST"
  exit 1
fi

echo "=============================================="
echo "  数据备份"
echo "=============================================="
echo "目标目录: $DEST"
echo ""

# ── 1. 数据库一致性快照 ──
if [ -f backend/fashion_inspo.db ]; then
  echo ">>> 备份数据库（一致性快照）..."
  python - "$DEST/fashion_inspo.db" <<'PYEOF'
import sqlite3
import sys

src = sqlite3.connect("backend/fashion_inspo.db")
dst = sqlite3.connect(sys.argv[1])
with dst:
    src.backup(dst)
src.close()
dst.close()
print("  数据库快照完成")
PYEOF
else
  echo "  !!! 未找到 backend/fashion_inspo.db，跳过数据库备份"
fi

# ── 2. 素材存储 ──
if [ -d backend/storage ]; then
  echo ">>> 备份素材存储（约数 GB，可能需要几分钟）..."
  if command -v robocopy >/dev/null 2>&1; then
    # Windows 原生 robocopy，速度快且可靠；退出码 0-7 均表示成功
    # 双斜杠 // 避免 Git Bash 把 /E 等参数误转成路径
    robocopy "backend/storage" "$DEST/storage" //E //XD logs tmp //NFL //NDL //NJH //NJS //NP >/dev/null 2>&1
    RC=$?
    if [ "$RC" -le 7 ]; then
      echo "  素材存储备份完成"
    else
      echo "  !!! 素材存储备份出错（robocopy 退出码 $RC）"
    fi
  else
    cp -r backend/storage "$DEST/storage"
    echo "  素材存储备份完成"
  fi
else
  echo "  !!! 未找到 backend/storage，跳过素材存储备份"
fi

# ── 3. 运行时配置（不进 git 的文件）──
echo ">>> 备份运行时配置 ..."
for f in \
  backend/.env \
  backend/prompt_configs.json \
  backend/model_configs.json \
  backend/prompt_versions.json \
  backend/prompt.txt \
  web/.env.local; do
  if [ -f "$f" ]; then
    cp "$f" "$DEST/"
    echo "  已备份 $f"
  fi
done

# ── 4. 记录代码版本 ──
git rev-parse HEAD > "$DEST/git_head.txt" 2>/dev/null
if [ -f "$DEST/git_head.txt" ]; then
  echo "  已记录 git HEAD: $(cat "$DEST/git_head.txt")"
fi

# ── 汇总 ──
echo ""
echo "=============================================="
echo "  备份完成"
echo "=============================================="
echo "备份位置: $DEST"
du -sh "$DEST" 2>/dev/null | sed 's/^/总大小: /'
echo ""
echo "提醒：当前备份位于本项目目录内（同一块硬盘）。"
echo "建议定期执行:  bash scripts/backup_data.sh D:/fashion-inspo-backups"
echo "把备份放到其他磁盘或网盘，才能防硬盘损坏。"
