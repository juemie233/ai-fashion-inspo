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
#   - 热写入收敛：后台任务持续写入（标签分析/lancedb）时自动增量修复，
#     并以「冻结源端清单」为校验基准（时点快照语义，不与实时源端比对）
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

# ── 2.1 热写入收敛：备份期间后台任务（标签分析/向量回填等）可能持续写入
#    storage/lancedb 等目录，单次复制后立即比对必然不一致（源端在变）。
#    此处做「比对 → 增量修复」循环：只重同步出现不一致的目录（robocopy
#    //MIR 增量很快），最多 REPAIR_MAX 次；全部一致则零开销直接通过。
#
#    每轮统计都会把「此时点」的源端清单冻结到 $DEST/.source_stats.json，
#    最终校验（步骤 5）以最后一份冻结清单为基准，而不是校验时刻的实时
#    源端——备份本就是时点快照语义，收敛通过后源端继续写入不应再判失败。
MISMATCH_LIST="$DEST/.mismatch_dirs"
FROZEN_STATS="$DEST/.source_stats.json"
REPAIR_MAX=5
repair_n=0
while :; do
  python - "$DEST" "backend/storage" "$MISMATCH_LIST" "$FROZEN_STATS" <<'PYEOF'
import json
import os
import sys

dest, storage_src, out_file, stats_file = sys.argv[1:5]
# lancedb 不在此列：改由「写入锁内一致性快照」单独处理（见步骤 2.2），
# 其文件数在任务写入期间持续增长，纳入热写入收敛循环永远追不上
REQUIRED = ("images", "person_photos", "trash", "thumbnails",
            "person_thumbnails")

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

dirs = list(REQUIRED)
vs = stat_dir(os.path.join(storage_src, "videos"))
if vs and vs["files"] > 0:
    dirs.append("videos")

storage_dest = os.path.join(dest, "storage")
mismatch: list[str] = []
stats: dict[str, dict | None] = {}
for d in dirs:
    s = stat_dir(os.path.join(storage_src, d))
    stats[d] = s
    if s is None:
        continue  # 源端本就无此目录：交由最终校验做告警，不参与修复
    t = stat_dir(os.path.join(storage_dest, d))
    if t is None or s["files"] != t["files"] or s["bytes"] != t["bytes"]:
        mismatch.append(d)

# 冻结此时点的源端统计（无论是否一致都写，供步骤 5 作比对基准）
with open(stats_file, "w", encoding="utf-8") as f:
    json.dump(stats, f, ensure_ascii=False)

if mismatch:
    with open(out_file, "w", encoding="utf-8") as f:
        f.write("\n".join(mismatch))
    print("  源/目标不一致目录: " + ", ".join(mismatch))
    sys.exit(3)
sys.exit(0)
PYEOF
  if [ $? -eq 0 ]; then
    [ "$repair_n" -gt 0 ] && echo "  热写入收敛完成（增量修复 $repair_n 次后一致）"
    break
  fi
  repair_n=$((repair_n + 1))
  if [ "$repair_n" -gt "$REPAIR_MAX" ]; then
    echo "  !!! 连续 $REPAIR_MAX 次增量修复后仍不一致：源端疑似持续写入"
    echo "      （如标签分析/AI 任务进行中）。本次备份将按校验失败处理，"
    echo "      可暂停相关任务或等其结束后再触发补备。"
    break
  fi
  echo "  检测到后台写入导致的不一致，增量修复（第 $repair_n/$REPAIR_MAX 次）..."
  while IFS= read -r d; do
    [ -z "$d" ] && continue
    if command -v robocopy >/dev/null 2>&1; then
      # /MIR 修复：同时处理「源端新增」与「源端删除」两类漂移；
      # 正在写入中的文件复制失败由下一轮重试兜底
      robocopy "backend/storage/$d" "$DEST/storage/$d" //MIR \
        //XD logs tmp _crop_backup _crop_dups cookies debug faces lancedb_backup_* _pre_reset_snapshot _pre_restore_snapshot \
        //R:1 //W:1 //NFL //NDL //NJH //NJS //NP
      RC=$?
      if [ "$RC" -le 7 ]; then
        echo "    已重同步 $d/（robocopy 退出码 $RC）"
      else
        fail "增量修复 $d/ 失败（robocopy 退出码 $RC）"
      fi
    else
      rm -rf "$DEST/storage/$d"
      cp -r "backend/storage/$d" "$DEST/storage/$d"
      echo "    已重同步 $d/（cp）"
    fi
  done < "$MISMATCH_LIST"
  sleep 3
done
rm -f "$MISMATCH_LIST"

# ── 2.2 lancedb 一致性快照：在「向量写入锁」（backend/app/services/vector/store.py
#    的 _vector_write_lock，所有向量写入方共享的跨进程文件锁）内复制并冻结统计，
#    保证快照不含半提交写入；锁内复制+比对必然一轮收敛（源端被锁暂停变更）。
#    其源端统计冻结到 .lancedb_stats.json，步骤 5 以它为校验基准（时点快照语义）。
echo ">>> [2.2/5] lancedb 一致性快照（向量写入锁内）..."
LANCE_STATS="$DEST/.lancedb_stats.json"
python - "$DEST" "$LANCE_STATS" <<'INNER_EOF'
import json
import os
import shutil
import sys

sys.path.insert(0, "backend")

dest, stats_file = sys.argv[1:3]
src = os.path.join("backend", "storage", "lancedb")
dst = os.path.join(dest, "storage", "lancedb")

if not os.path.isdir(src):
    print("  源端无 lancedb/ 目录（向量功能未启用），跳过")
    with open(stats_file, "w", encoding="utf-8") as f:
        json.dump(None, f)
    sys.exit(0)

from app.services.vector.store import _vector_write_lock


def stat_dir(path: str) -> dict:
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


with _vector_write_lock():
    # 锁内：所有向量写入方（API/worker）都会被挡在锁外，复制期间源端不变
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    s = stat_dir(src)
    t = stat_dir(dst)
    with open(stats_file, "w", encoding="utf-8") as f:
        json.dump(s, f)

if s != t:
    print(f"  !!! 锁内复制后仍不一致：源={s} 目标={t}（异常，请检查磁盘）")
    sys.exit(3)
print(f"  lancedb 快照完成（{s['files']} 个文件，锁内一致）")
INNER_EOF
RC=$?
if [ "$RC" -eq 0 ]; then
  :
elif [ "$RC" -eq 3 ]; then
  fail "lancedb 锁内快照不一致（异常场景）"
else
  fail "lancedb 快照失败（python 退出码 $RC；注意：锁获取需 backend 依赖可导入）"
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

# 5.3 必备目录：与「冻结源端清单」比对（而非校验时刻的实时源端）。
# 备份是时点快照：以复制收敛完成时刻的源端统计为基准，之后后台任务
# 继续写入源端属正常现象，不应判备份失败（热写入收敛见步骤 2.1）。
# lancedb 不在此列：改由「写入锁内一致性快照」单独处理（见步骤 2.2），
# 其文件数在任务写入期间持续增长，纳入热写入收敛循环永远追不上
REQUIRED = ("images", "person_photos", "trash", "thumbnails",
            "person_thumbnails")

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
frozen_path = os.path.join(dest, ".source_stats.json")
frozen: dict | None = None
if os.path.exists(frozen_path):
    try:
        with open(frozen_path, encoding="utf-8") as f:
            frozen = json.load(f)
    except (OSError, ValueError):
        frozen = None

dir_stats: dict[str, dict] = {}
if not os.path.isdir(storage_dest):
    # 未复制 storage：步骤 2 已 fail（未找到 backend/storage），此处仅跳过目录比对
    pass
elif frozen is None:
    errors.append("缺少源端统计快照 .source_stats.json（复制收敛步骤未运行）")
else:
    for d, fs in frozen.items():
        if fs is None:
            warnings.append(f"源端无 {d}/ 目录（跳过）")
            continue
        t = stat_dir(os.path.join(storage_dest, d))
        dir_stats[d] = {"source": fs, "dest": t}
        if t is None:
            errors.append(f"目标缺少必备目录 {d}/")
        elif fs["files"] != t["files"] or fs["bytes"] != t["bytes"]:
            errors.append(f"{d}/ 文件数或字节数不一致：源={fs} 目标={t}")
    # 冻结清单已并入 manifest，清理临时文件
    try:
        os.remove(frozen_path)
    except OSError:
        pass

# 5.4 lancedb：以「写入锁内快照时刻」冻结的源端统计为基准校验目标副本
lance_stats_path = os.path.join(dest, ".lancedb_stats.json")
if os.path.exists(lance_stats_path):
    try:
        with open(lance_stats_path, encoding="utf-8") as f:
            lance_frozen = json.load(f)
    except (OSError, ValueError):
        lance_frozen = None
    if lance_frozen is not None:
        t = stat_dir(os.path.join(storage_dest, "lancedb"))
        dir_stats["lancedb"] = {"source": lance_frozen, "dest": t}
        if t is None:
            errors.append("目标缺少 lancedb/")
        elif lance_frozen["files"] != t["files"] or lance_frozen["bytes"] != t["bytes"]:
            errors.append(
                f"lancedb/ 文件数或字节数不一致：源={lance_frozen} 目标={t}"
            )
    try:
        os.remove(lance_stats_path)
    except OSError:
        pass

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
