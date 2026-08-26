#!/bin/bash
# AI 穿搭素材库 — 数据备份脚本（增强版）
#
# 用法:
#   bash scripts/backup_data.sh <目标目录> [--allow-same-disk] [--verify-hash]
#
# 示例:
#   bash scripts/backup_data.sh E:/fashion-inspo-backups           # 推荐：异盘
#   bash scripts/backup_data.sh D:/backups --verify-hash           # 全量 SHA-256 校验
#   bash scripts/backup_data.sh backups --allow-same-disk          # 临时同盘备份
#
# 能力（对应 docs/wayfinder/backup-disaster-recovery/T3/T4/T6/T7）:
#   - SQLite 一致性快照（Python sqlite3.backup，后端运行中可安全备份）
#   - 额外导出 SQL 明文（iterdump），DB 损坏时的抢救双保险
#   - robocopy 全量 storage/，排除可重建的 _crop_backup / logs / tmp 等
#   - 运行时配置（.env / prompt 配置 / web/.env.local）+ git HEAD
#   - 备份后校验：integrity_check、SQL 内存试导、必备目录、文件数/字节数比对
#   - manifest.json 清单 + SUCCESS/FAILED 标记 + 可靠退出码
#   - 并发锁 backup.lock（防止计划任务与启动补备撞车）
#   - rotation：日备留 7 份 + 周备（周日）留 4 份
#
# 退出码:
#   0 全部成功（含「已有备份在跑，跳过」）
#   1 参数错误 / 同盘拒绝
#   2 备份或校验失败
#
# 注意：本脚本只做备份，不注册计划任务、不停启服务（项目约定）。

set -u

# Windows 下 Python 管道默认 GBK，强制 UTF-8 输出避免中文日志/print 乱码
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd -W 2>/dev/null || pwd)"

# ── 参数解析 ──
TARGET=""
ALLOW_SAME_DISK=0
VERIFY_HASH=0
while [ $# -gt 0 ]; do
  case "$1" in
    --allow-same-disk) ALLOW_SAME_DISK=1; shift ;;
    --verify-hash) VERIFY_HASH=1; shift ;;
    -h|--help)
      sed -n '2,30p' "$0"; exit 0 ;;
    -*)
      echo "未知参数: $1"; exit 1 ;;
    *)
      if [ -z "$TARGET" ]; then TARGET="$1"; else echo "多余的位置参数: $1"; exit 1; fi
      shift ;;
  esac
done

if [ -z "$TARGET" ]; then
  echo "错误：必须指定备份目标目录。"
  echo "示例: bash scripts/backup_data.sh E:/fashion-inspo-backups"
  exit 1
fi

# 先创建目标根目录（存在或新建），再解析为规范的 Windows 绝对路径
mkdir -p "$TARGET" || { echo "无法创建目标目录: $TARGET"; exit 1; }
TARGET_ROOT="$(cd "$TARGET" && pwd -W 2>/dev/null || pwd)"

# ── 同盘检测：目标与项目在同一物理盘符时拒绝（除非 --allow-same-disk）──
_drive_letter() {
  local w
  w="$(cygpath -w "$1" 2>/dev/null || echo "$1")"
  if [[ "$w" =~ ^([A-Za-z])[:\\\\/] ]]; then
    echo "${BASH_REMATCH[1]^^}"
  fi
}
PROJ_DRIVE="$(_drive_letter "$PROJECT_ROOT")"
TGT_DRIVE="$(_drive_letter "$TARGET_ROOT")"
if [ "$ALLOW_SAME_DISK" -ne 1 ] && [ -n "$PROJ_DRIVE" ] && [ -n "$TGT_DRIVE" ] && [ "$PROJ_DRIVE" = "$TGT_DRIVE" ]; then
  echo "错误：备份目标与项目位于同一盘符（$PROJ_DRIVE:），无法防磁盘损坏。"
  echo "请指定其他磁盘（如 E:/fashion-inspo-backups），或加 --allow-same-disk 强制同盘备份。"
  exit 1
fi

# ── 并发锁（锁放目标根，所有触发通道互斥）──
LOCK_DIR="$TARGET_ROOT/.backup.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "已有备份在进行中（锁存在：$LOCK_DIR），本次跳过。"
  exit 0
fi
echo "$$ $(date '+%Y-%m-%d %H:%M:%S')" > "$LOCK_DIR/pid" 2>/dev/null || true
trap 'rm -rf "$LOCK_DIR" 2>/dev/null || true' EXIT

STAMP="$(date +%Y-%m-%d_%H%M%S)"
DEST="$TARGET_ROOT/$STAMP"
mkdir -p "$DEST" || { echo "无法创建本次备份目录: $DEST"; exit 1; }

# 日志：同时写本次备份目录与 backend/storage/logs/backup.log
LOG_DIR="backend/storage/logs"
mkdir -p "$LOG_DIR"
LOGFILE="$LOG_DIR/backup.log"
exec > >(tee -a "$DEST/backup.log" "$LOGFILE") 2>&1

echo "=============================================="
echo "  数据备份  $STAMP"
echo "=============================================="
echo "目标目录: $DEST"
echo "项目根:   $PROJECT_ROOT"
echo "哈希校验: $([ "$VERIFY_HASH" -eq 1 ] && echo 全量SHA-256 || echo 关)"
echo ""

BACKUP_FAILED=0
fail() {
  echo "  !!! $1"
  BACKUP_FAILED=1
}

# ── 1. 数据库一致性快照 + SQL 明文导出 ──
echo ">>> [1/5] 备份数据库（一致性快照 + SQL 导出）..."
DB_OK=0
if [ -f backend/fashion_inspo.db ]; then
  python - backend/fashion_inspo.db "$DEST/fashion_inspo.db" "$DEST/fashion_inspo.sql" <<'PYEOF'
import sqlite3, sys
src_path, db_dest, sql_dest = sys.argv[1], sys.argv[2], sys.argv[3]
src = sqlite3.connect(src_path)
dst = sqlite3.connect(db_dest)
with dst:
    src.backup(dst)  # backup API 自动处理 WAL，在线安全
src.close()
dst.close()
# SQL 明文导出（不依赖 sqlite3 CLI）
con = sqlite3.connect(db_dest)
with open(sql_dest, "w", encoding="utf-8") as f:
    for line in con.iterdump():
        f.write(f"{line}\n")
con.close()
print("  数据库快照 + SQL 导出完成")
PYEOF
  DB_OK=$?
else
  fail "未找到 backend/fashion_inspo.db，跳过数据库备份"
fi
[ "$DB_OK" -ne 0 ] && fail "数据库快照/导出失败（python 退出码 $DB_OK）"
echo ""

# ── 2. 素材存储（robocopy 镜像，排除可重建/临时目录）──
echo ">>> [2/5] 备份素材存储（robocopy）..."
STORAGE_OK=0
if [ -d backend/storage ]; then
  if command -v robocopy >/dev/null 2>&1; then
    # 退出码 0-7 成功；/XD 目录名在源下任意层级匹配，支持通配。
    # _pre_reset_snapshot / _pre_restore_snapshot 为 reset/restore 前的 7 天临时
    # 安全网（内含整库素材副本），不应重复打进长期备份，故一并排除。
    robocopy "backend/storage" "$DEST/storage" //E \
      //XD logs tmp _crop_backup _crop_dups cookies debug faces lancedb_backup_* _pre_reset_snapshot _pre_restore_snapshot \
      //R:1 //W:1 //NFL //NDL //NJH //NJS //NP
    RC=$?
    if [ "$RC" -le 7 ]; then
      echo "  素材存储备份完成（robocopy 退出码 $RC）"
      STORAGE_OK=1
    else
      fail "素材存储备份出错（robocopy 退出码 $RC）"
    fi
  else
    cp -r backend/storage "$DEST/storage"
    echo "  素材存储备份完成（cp）"
    STORAGE_OK=1
  fi
else
  fail "未找到 backend/storage，跳过素材存储备份"
fi
echo ""

# ── 3. 运行时配置（不进 git 的文件，保持原相对路径，便于 restore 直接拷回）──
echo ">>> [3/5] 备份运行时配置..."
for f in backend/.env backend/prompt_configs.json backend/model_configs.json \
         backend/prompt_versions.json backend/prompt.txt web/.env.local; do
  if [ -f "$f" ]; then
    mkdir -p "$DEST/$(dirname "$f")"
    cp "$f" "$DEST/$f"
    echo "  已备份 $f"
  fi
done
echo ""

# ── 4. 记录代码版本 ──
echo ">>> [4/5] 记录代码版本..."
GIT_HEAD="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
echo "$GIT_HEAD" > "$DEST/git_head.txt"
echo "  git HEAD: $GIT_HEAD"
echo ""

# ── 5. 校验 + manifest ──
echo ">>> [5/5] 校验备份并生成 manifest..."
python - "$DEST" "backend/storage" "$VERIFY_HASH" "$GIT_HEAD" <<'PYEOF'
import hashlib
import json
import os
import sqlite3
import sys

dest, storage_src, verify_hash, git_head = sys.argv[1], sys.argv[2], sys.argv[3] == "1", sys.argv[4]
errors, warnings = [], []

# 5.1 数据库完整性
db_path = os.path.join(dest, "fashion_inspo.db")
counts: dict[str, int | None] = {}
if os.path.exists(db_path):
    con = sqlite3.connect(db_path)
    try:
        ic = con.execute("PRAGMA integrity_check").fetchone()[0]
        if ic != "ok":
            errors.append(f"integrity_check 返回 {ic!r}")
        for t in ("inspirations", "tags", "inspiration_tags", "bloggers",
                  "models", "audit_logs", "ai_analysis_log", "task_queue"):
            try:
                counts[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.Error:
                counts[t] = None
    finally:
        con.close()
else:
    errors.append("fashion_inspo.db 缺失")

# 5.2 SQL 明文内存试导，确保可导入
sql_path = os.path.join(dest, "fashion_inspo.sql")
if not os.path.exists(sql_path) or os.path.getsize(sql_path) == 0:
    errors.append("fashion_inspo.sql 缺失或为空")
else:
    with open(sql_path, encoding="utf-8") as f:
        sql_text = f.read()
    if "CREATE TABLE" not in sql_text:
        errors.append("fashion_inspo.sql 缺少 CREATE TABLE")
    else:
        try:
            mem = sqlite3.connect(":memory:")
            mem.executescript(sql_text)
            mem.close()
        except sqlite3.Error as e:
            errors.append(f"SQL 内存试导失败: {e}")

# 5.3 必备目录存在性 + 源/目标文件数/字节数比对
REQUIRED = ("images", "person_photos", "trash", "thumbnails",
            "person_thumbnails", "lancedb")

def stat_dir(path: str) -> dict | None:
    if not os.path.isdir(path):
        return None
    fc = tb = 0
    for root, _, files in os.walk(path):
        for fn in files:
            fp = os.path.join(root, fn)
            try:
                tb += os.path.getsize(fp)
                fc += 1
            except OSError:
                pass
    return {"files": fc, "bytes": tb}

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

storage_dest = os.path.join(dest, "storage")
dir_stats: dict[str, dict] = {}
for d in REQUIRED:
    s = stat_dir(os.path.join(storage_src, d))
    t = stat_dir(os.path.join(storage_dest, d))
    dir_stats[d] = {"source": s, "dest": t}
    if s is None:
        warnings.append(f"源端无 {d}/ 目录（跳过）")
        continue
    if t is None:
        errors.append(f"目标缺少必备目录 {d}/")
    elif s["files"] != t["files"] or s["bytes"] != t["bytes"]:
        errors.append(f"{d}/ 文件数或字节数不一致：源={s} 目标={t}")

# videos 有内容时才要求
vs = stat_dir(os.path.join(storage_src, "videos"))
if vs and vs["files"] > 0:
    t = stat_dir(os.path.join(storage_dest, "videos"))
    dir_stats["videos"] = {"source": vs, "dest": t}
    if not t or t["files"] != vs["files"] or t["bytes"] != vs["bytes"]:
        errors.append(f"videos/ 不一致：源={vs} 目标={t}")

# 5.4 配置文件：backend/.env 必须在（配置按原相对路径保存）
if not os.path.exists(os.path.join(dest, "backend", ".env")):
    errors.append("backend/.env 未备份")
else:
    for cfg in ("backend/prompt_configs.json", "backend/model_configs.json",
                "backend/prompt_versions.json", "backend/prompt.txt",
                "web/.env.local"):
        if not os.path.exists(os.path.join(dest, cfg)):
            warnings.append(f"{cfg} 未备份（可能本就不存在）")

# 5.5 可选：全量 SHA-256
hashes: dict[str, str] = {}
if verify_hash:
    for d in dir_stats:
        ddir = os.path.join(storage_dest, d)
        if not os.path.isdir(ddir):
            continue
        for root, _, files in os.walk(ddir):
            for fn in files:
                fp = os.path.join(root, fn)
                rel = os.path.relpath(fp, storage_dest).replace("\\", "/")
                try:
                    hashes[rel] = sha256_file(fp)
                except OSError as e:
                    warnings.append(f"哈希计算失败 {rel}: {e}")

# 5.6 写 manifest
manifest = {
    "timestamp": os.path.basename(dest),
    "git_head": git_head,
    "db_size": os.path.getsize(db_path) if os.path.exists(db_path) else 0,
    "sql_size": os.path.getsize(sql_path) if os.path.exists(sql_path) else 0,
    "table_counts": counts,
    "dir_stats": dir_stats,
    "verify_hash": verify_hash,
    "hashes": hashes,
    "warnings": warnings,
}
with open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8") as f:
    json.dump(manifest, f, ensure_ascii=False, indent=2)

print(f"  关键表行数: {{{', '.join(f'{k}={v}' for k,v in counts.items() if v is not None)}}}")
for w in warnings:
    print(f"  告警: {w}")
if errors:
    print("  校验失败:")
    for e in errors:
        print(f"    - {e}")
    sys.exit(2)
print("  校验通过，manifest.json 已生成")
PYEOF
[ $? -ne 0 ] && fail "备份校验未通过"
echo ""

# ── 标记 + 汇总 ──
if [ "$BACKUP_FAILED" -ne 0 ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') FAILED" > "$DEST/FAILED"
  echo "=============================================="
  echo "  备份失败（详见 $DEST/backup.log）"
  echo "=============================================="
  exit 2
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') SUCCESS git=$GIT_HEAD" > "$DEST/SUCCESS"
echo "=============================================="
echo "  备份完成"
echo "=============================================="
echo "备份位置: $DEST"
du -sh "$DEST" 2>/dev/null | sed 's/^/总大小: /'
echo "SUCCESS 标记已写入。"
echo ""

# ── rotation（仅成功后执行）：日备 7 份 + 周日周备 4 份 ──
echo ">>> 清理旧备份（日备留 7 份 + 周备留 4 份）..."
python - "$TARGET_ROOT" 7 4 <<'PYEOF'
import datetime
import os
import re
import shutil
import sys

root, daily_keep, weekly_keep = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
failed_keep = 3  # 失败备份独立留最近 3 份作排障证据，更早的优先清理
pat = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}$")

deleted: list[str] = []
success_entries, failed_entries = [], []
for name in os.listdir(root):
    p = os.path.join(root, name)
    if not os.path.isdir(p) or not pat.match(name):
        continue
    try:
        dt = datetime.datetime.strptime(name, "%Y-%m-%d_%H%M%S")
    except ValueError:
        continue
    if os.path.exists(os.path.join(p, "SUCCESS")):
        success_entries.append((dt, name, p))
    elif os.path.exists(os.path.join(p, "FAILED")):
        failed_entries.append((dt, name, p))
    else:
        # 被中断、既无 SUCCESS 也无 FAILED 的半截备份：无留证价值，立即清理
        shutil.rmtree(p, ignore_errors=True)
        deleted.append(name + " (中断)")

success_entries.sort(key=lambda x: x[0], reverse=True)
failed_entries.sort(key=lambda x: x[0], reverse=True)

# 成功备份：最近 daily_keep 份全留（日备）；更早的周日备份留 weekly_keep 份（周备）
daily = success_entries[:daily_keep]
rest = success_entries[daily_keep:]
weekly = [item for item in rest if item[0].weekday() == 6]  # 周日
weekly_keep_ids = {id(x) for x in weekly[:weekly_keep]}
for item in rest:
    if id(item) not in weekly_keep_ids:
        shutil.rmtree(item[2], ignore_errors=True)
        deleted.append(item[1])
kept_names = [x[1] for x in daily] + [x[1] for x in weekly[:weekly_keep]]

# 失败备份：不占用日/周备名额，独立保留最近 failed_keep 份，更早的删除（T7：优先删失败的）
for i, (_, name, p) in enumerate(failed_entries):
    if i < failed_keep:
        kept_names.append(name + " (FAILED)")
    else:
        shutil.rmtree(p, ignore_errors=True)
        deleted.append(name + " (FAILED)")

print(f"  删除 {len(deleted)} 份: {', '.join(deleted) if deleted else '无'}")
print(f"  保留 {len(kept_names)} 份: {', '.join(kept_names) if kept_names else '无'}")
PYEOF

echo ""
echo "提醒：本备份含 .env（密钥），请勿放入未加密的网盘/公共位置。"
exit 0
