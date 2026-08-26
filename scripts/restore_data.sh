#!/bin/bash
# AI 穿搭素材库 — 数据恢复脚本
#
# 用法:
#   bash scripts/restore_data.sh <备份目录> [--force] [--allow-overwrite] [--from-sql]
#
# 示例:
#   bash scripts/restore_data.sh E:/fashion-inspo-backups/2026-08-26_102630
#   bash scripts/restore_data.sh /path/to/backup --from-sql          # .db 损坏时用 SQL 重建
#   bash scripts/restore_data.sh /path/to/backup --force             # 备份无 SUCCESS 标记也恢复
#   bash scripts/restore_data.sh /path/to/backup --allow-overwrite   # 目标已有数据时不拒绝
#
# 执行步骤（对应 T8）:
#   1. 前置检查（SUCCESS 标记/磁盘空间/服务是否在跑/是否覆盖现有数据）
#   2. 恢复前快照当前 DB + storage（移到 backend/storage/_pre_restore_snapshot/，保留7天）
#   3. 还原数据库（默认 .db 一致性快照；--from-sql 用 fashion_inspo.sql 重建）
#   4. robocopy 还原 storage 必备目录（_crop_backup/logs 等排除项缺失不告警）
#   5. 还原运行时配置（.env / prompt 配置 / web/.env.local）
#   6. 恢复后校验（integrity_check + 关键表 count 对 manifest + 抽查素材文件）
#
# 安全约定:
#   - 不自动停/启后端与 worker（项目约定）：检测到服务在运行会打印停止命令并退出。
#   - 不自动回滚：恢复后校验不一致只报错，交由人工用步骤2的快照决定。
#
# 退出码: 0 成功；1 参数/前置检查失败；2 恢复或校验失败

set -u

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd -W 2>/dev/null || pwd)"
BACKEND_DIR="$PROJECT_ROOT/backend"
STORAGE_DIR="$BACKEND_DIR/storage"

# ── 参数解析 ──
BACKUP=""
FORCE=0
ALLOW_OVERWRITE=0
FROM_SQL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    --allow-overwrite) ALLOW_OVERWRITE=1; shift ;;
    --from-sql) FROM_SQL=1; shift ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    -*) echo "未知参数: $1"; exit 1 ;;
    *)
      if [ -z "$BACKUP" ]; then BACKUP="$1"; else echo "多余的位置参数: $1"; exit 1; fi
      shift ;;
  esac
done

if [ -z "$BACKUP" ]; then
  echo "错误：必须指定要恢复的备份目录。"
  echo "示例: bash scripts/restore_data.sh E:/fashion-inspo-backups/2026-08-26_102630"
  exit 1
fi
# 规范化为绝对路径
if [ -d "$BACKUP" ]; then
  BACKUP="$(cd "$BACKUP" && pwd -W 2>/dev/null || pwd)"
else
  echo "错误：备份目录不存在: $BACKUP"
  exit 1
fi

echo "=============================================="
echo "  数据恢复"
echo "=============================================="
echo "备份目录: $BACKUP"
echo "项目根:   $PROJECT_ROOT"
echo ""

RESTORE_FAILED=0
fail() { echo "  !!! $1"; RESTORE_FAILED=1; }

# ── 步骤 1：前置检查（不改动任何数据）──
echo ">>> [1/6] 前置检查..."

# 1.1 SUCCESS 标记
if [ ! -f "$BACKUP/SUCCESS" ]; then
  if [ "$FORCE" -ne 1 ]; then
    echo "  错误：该备份缺少 SUCCESS 标记，可能是不完整/失败的备份。"
    echo "  如确认要恢复，加 --force 强制执行。"
    exit 1
  fi
  echo "  告警：缺少 SUCCESS 标记，已按 --force 继续。"
else
  echo "  SUCCESS 标记: $(cat "$BACKUP/SUCCESS")"
fi

# 1.2 必要产物存在
if [ "$FROM_SQL" -eq 1 ]; then
  [ -f "$BACKUP/fashion_inspo.sql" ] || fail "指定了 --from-sql 但缺少 fashion_inspo.sql"
else
  [ -f "$BACKUP/fashion_inspo.db" ] || fail "缺少 fashion_inspo.db（可用 --from-sql 从 SQL 恢复）"
fi
[ -d "$BACKUP/storage" ] || fail "备份中缺少 storage/ 目录"
[ -f "$BACKUP/backend/.env" ] || echo "  告警：备份中无 backend/.env（恢复后需自行配置）"

# 1.3 服务是否在运行（检查 18888 端口）
if command -v netstat >/dev/null 2>&1; then
  if netstat -ano 2>/dev/null | grep -q "[:.]18888.*LISTENING"; then
    echo "  错误：检测到后端仍在运行（18888 端口 LISTENING）。"
    echo "  请先手动停止后端/worker 后再恢复（项目约定脚本不自行停服务）。"
    echo "  可在项目根执行你的停止脚本，或任务管理器结束 python 进程。"
    exit 1
  fi
fi
echo "  未检测到后端运行（18888 空闲）"

# 1.4 目标已有数据？
HAS_EXISTING=0
if [ -f "$BACKEND_DIR/fashion_inspo.db" ] || [ -d "$STORAGE_DIR/images" ] || [ -d "$STORAGE_DIR/person_photos" ]; then
  HAS_EXISTING=1
fi
if [ "$HAS_EXISTING" -eq 1 ] && [ "$ALLOW_OVERWRITE" -ne 1 ]; then
  echo "  错误：目标位置已有数据（DB 或素材目录存在）。"
  echo "  恢复会覆盖现有数据（脚本会先做快照，但仍需你显式确认）。"
  echo "  加 --allow-overwrite 继续。"
  exit 1
fi
echo "  覆盖确认: $([ "$HAS_EXISTING" -eq 1 ] && echo '已有数据，将先快照后覆盖' || echo '目标为空，直接恢复')"

# 1.5 磁盘空间（粗略：备份占用 ×1.1）
if command -v du >/dev/null 2>&1; then
  BK_SIZE_KB=$(du -sk "$BACKUP" 2>/dev/null | cut -f1)
  FREE_KB=$(df -Pk "$PROJECT_ROOT" 2>/dev/null | awk 'NR==2{print $4}')
  if [ -n "${BK_SIZE_KB:-}" ] && [ -n "${FREE_KB:-}" ]; then
    NEED_KB=$(( BK_SIZE_KB * 11 / 10 ))
    if [ "$NEED_KB" -gt "$FREE_KB" ]; then
      echo "  错误：磁盘空间不足（备份 ${BK_SIZE_KB}KB，需要约 ${NEED_KB}KB，可用 ${FREE_KB}KB）。"
      exit 1
    fi
    echo "  磁盘空间充足（备份 $((BK_SIZE_KB/1024))MB，可用 $((FREE_KB/1024))MB）"
  fi
fi

[ "$RESTORE_FAILED" -ne 0 ] && { echo "前置检查未通过，终止。"; exit 1; }
echo ""

# ── 步骤 2：恢复前快照当前状态 ──
SNAPSHOT_DIR=""
if [ "$HAS_EXISTING" -eq 1 ]; then
  echo ">>> [2/6] 快照当前数据（恢复前安全网）..."
  STAMP="$(date +%Y-%m-%d_%H%M%S)"
  SNAPSHOT_DIR="$STORAGE_DIR/_pre_restore_snapshot/$STAMP"
  mkdir -p "$SNAPSHOT_DIR"
  # DB（含 WAL/SHM）移动到快照
  for f in fashion_inspo.db fashion_inspo.db-wal fashion_inspo.db-shm; do
    [ -f "$BACKEND_DIR/$f" ] && mv "$BACKEND_DIR/$f" "$SNAPSHOT_DIR/" && echo "  快照 $f"
  done
  # 当前 storage 必备目录移动到快照（被排除的 _crop_backup/logs 等保留原位，不参与恢复）
  if [ -d "$STORAGE_DIR" ]; then
    mkdir -p "$SNAPSHOT_DIR/storage"
    for d in images person_photos trash videos thumbnails person_thumbnails lancedb; do
      if [ -d "$STORAGE_DIR/$d" ]; then
        mv "$STORAGE_DIR/$d" "$SNAPSHOT_DIR/storage/" && echo "  快照 storage/$d"
      fi
    done
  fi
  echo "  快照位置: $SNAPSHOT_DIR"
  echo "  （快照保留 7 天，由后端启动清理任务回收；如需回滚可从此处手动取回）"
else
  echo ">>> [2/6] 目标为空，跳过恢复前快照"
  mkdir -p "$STORAGE_DIR"
fi
echo ""

# ── 步骤 3：还原数据库 ──
echo ">>> [3/6] 还原数据库..."
if [ "$FROM_SQL" -eq 1 ]; then
  echo "  使用 SQL 明文重建（--from-sql）..."
  python - "$BACKEND_DIR/fashion_inspo.db" "$BACKUP/fashion_inspo.sql" <<'PYEOF'
import sqlite3, sys
dst_path, sql_path = sys.argv[1], sys.argv[2]
con = sqlite3.connect(dst_path)
with open(sql_path, encoding="utf-8") as f:
    con.executescript(f.read())
con.commit()
con.close()
print("  SQL 重建完成")
PYEOF
  [ $? -ne 0 ] && fail "SQL 重建失败"
else
  cp "$BACKUP/fashion_inspo.db" "$BACKEND_DIR/fashion_inspo.db" && echo "  .db 快照已复制"
  # 清掉可能残留的 WAL/SHM，避免连接时用旧 WAL 覆盖恢复的数据
  rm -f "$BACKEND_DIR/fashion_inspo.db-wal" "$BACKEND_DIR/fashion_inspo.db-shm"
fi
echo ""

# ── 步骤 4：还原 storage 文件 ──
echo ">>> [4/6] 还原素材存储（robocopy）..."
if command -v robocopy >/dev/null 2>&1; then
  # 逐必备目录复制（/E 含子目录；不清空目标上的排除目录，避免误删 _crop_backup 等）
  # 被备份排除的目录（_crop_backup/logs/tmp 等）在备份中本就不存在，不告警
  for d in images person_photos trash videos thumbnails person_thumbnails lancedb; do
    if [ -d "$BACKUP/storage/$d" ]; then
      robocopy "$BACKUP/storage/$d" "$STORAGE_DIR/$d" //E //R:1 //W:1 //NFL //NDL //NJH //NJS //NP >/dev/null 2>&1
      RC=$?
      if [ "$RC" -le 7 ]; then
        echo "  还原 storage/$d（robocopy $RC）"
      else
        fail "还原 storage/$d 失败（robocopy $RC）"
      fi
    fi
  done
else
  for d in images person_photos trash videos thumbnails person_thumbnails lancedb; do
    if [ -d "$BACKUP/storage/$d" ]; then
      mkdir -p "$STORAGE_DIR/$d"
      cp -r "$BACKUP/storage/$d/." "$STORAGE_DIR/$d/" && echo "  还原 storage/$d（cp）"
    fi
  done
fi
echo ""

# ── 步骤 5：还原运行时配置 ──
echo ">>> [5/6] 还原运行时配置..."
for f in backend/.env backend/prompt_configs.json backend/model_configs.json \
         backend/prompt_versions.json backend/prompt.txt web/.env.local; do
  if [ -f "$BACKUP/$f" ]; then
    mkdir -p "$PROJECT_ROOT/$(dirname "$f")"
    cp "$BACKUP/$f" "$PROJECT_ROOT/$f"
    echo "  还原 $f"
  fi
done
echo "  提醒：在新机器上请核对 .env 中的路径、API Key、Ollama 地址是否需要调整。"
echo ""

# ── 步骤 6：恢复后校验 ──
echo ">>> [6/6] 恢复后校验..."
python - "$BACKEND_DIR/fashion_inspo.db" "$STORAGE_DIR" "$BACKUP/manifest.json" <<'PYEOF'
import json, os, sqlite3, sys

db_path, storage_dir, manifest_path = sys.argv[1], sys.argv[2], sys.argv[3]
errors, warnings = [], []

# 6.1 integrity_check
con = sqlite3.connect(db_path)
try:
    ic = con.execute("PRAGMA integrity_check").fetchone()[0]
    if ic != "ok":
        errors.append(f"integrity_check 返回 {ic!r}")

    # 6.2 关键表 count 与 manifest 比对
    counts = {}
    for t in ("inspirations", "tags", "inspiration_tags", "bloggers",
              "models", "audit_logs", "ai_analysis_log", "task_queue"):
        try:
            counts[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.Error:
            counts[t] = None
finally:
    con.close()

manifest = {}
if os.path.exists(manifest_path):
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    m_counts = manifest.get("table_counts", {})
    for t, v in m_counts.items():
        if v is not None and counts.get(t) is not None and counts[t] != v:
            errors.append(f"表 {t} 行数不一致：备份={v} 恢复={counts[t]}")
else:
    warnings.append("无 manifest.json，跳过 count 比对")

print("  关键表行数:", {k: v for k, v in counts.items() if v is not None})

# 6.3 抽查素材文件：随机取若干 file_path 验证磁盘存在
con = sqlite3.connect(db_path)
try:
    rows = con.execute(
        "SELECT file_path FROM inspirations WHERE file_path IS NOT NULL "
        "ORDER BY RANDOM() LIMIT 20"
    ).fetchall()
finally:
    con.close()
missing = []
for (fp,) in rows:
    full = os.path.join(storage_dir, fp.replace("/", os.sep))
    if not os.path.exists(full):
        missing.append(fp)
if rows:
    print(f"  抽查素材文件：{len(rows) - len(missing)}/{len(rows)} 存在")
if missing:
    errors.append(f"{len(missing)} 个抽查文件缺失，例如: {missing[:3]}")
crop_ok = not os.path.isdir(os.path.join(storage_dir, "_crop_backup"))
if crop_ok:
    print("  _crop_backup 不存在（符合预期：该目录不纳入备份）")

for w in warnings:
    print("  告警:", w)
if errors:
    print("  校验失败:")
    for e in errors:
        print("    -", e)
    sys.exit(2)
print("  恢复校验通过")
PYEOF
[ $? -ne 0 ] && fail "恢复后校验未通过"
echo ""

# ── 汇总 ──
if [ "$RESTORE_FAILED" -ne 0 ]; then
  echo "=============================================="
  echo "  恢复过程存在失败项（见上方 !!! 提示）"
  echo "=============================================="
  if [ -n "$SNAPSHOT_DIR" ]; then
    echo "恢复前快照保留在: $SNAPSHOT_DIR"
    echo "如需回滚，可手动将其中的 DB / storage 移回原位。"
  fi
  exit 2
fi

echo "=============================================="
echo "  恢复完成"
echo "=============================================="
if [ -n "$SNAPSHOT_DIR" ]; then
  echo "恢复前快照: $SNAPSHOT_DIR"
fi
echo ""
echo "后续步骤（请手动执行，脚本不自动启停服务）："
echo "  1. 如有需要，运行数据库迁移：cd backend && alembic upgrade head"
echo "  2. 确认 Ollama 已启动、所需模型已 pull（qwen3-vl:8b-instruct、all-minilm）"
echo "  3. 启动后端/前端/worker"
echo "  4. 验证：打开后台 / GET /api/health，核对素材数、抽查图片、搜索标签"
echo ""
echo "定期备份的计划任务（schtasks）不会随恢复迁移，新机器需重新注册，见"
echo "docs/backup-restore.md。"
exit 0
