#!/usr/bin/env bash
# ============================================================
# OpenViking 索引同步脚本（Git Bash 版）
# 用途：把项目「代码 + 文档 + 数据库结构」增量同步到 OpenViking
#       索引（viking://resources/fashion-inspo/），供语义检索。
# 用法：双击 scripts/sync_openviking.bat（推荐），或在 Git Bash 中执行：
#         bash scripts/sync_openviking.sh            # 增量（默认）
#         bash scripts/sync_openviking.sh --full     # 忽略状态文件，强制全量
#         bash scripts/sync_openviking.sh --dry-run  # 只预览，不上传也不写状态
# 说明：
#   - 增量判定按「文件内容 sha1」比对，状态落 scripts/.sync_openviking_state.json。
#     该文件是机器相关运行时数据，已在 .gitignore 忽略；删掉它即退化为全量同步。
#   - 之所以必须靠本地增量：服务端 batch-write 对 upsert 不做内容比对，内容没变
#     也会重写并标记 modified，照样重跑一遍 L0/L1 摘要。本地模型是单卡串行的，
#     全量重跑一次要几十分钟，所以「跳过未变文件」只能在客户端做。
#   - 测试目录与测试文件一律不入索引（见 TEST_DIR_NAMES / TEST_FILE_RE）。
#   - 幂等（upsert），同步后向量/摘要由 OpenViking 后台异步生成。
# ============================================================
set -euo pipefail

# 脚本所在目录 = scripts/，项目根 = 上一级
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ---------- 解析参数 ----------
FULL_SYNC=0
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --full)    FULL_SYNC=1 ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help)
      echo "用法：bash scripts/sync_openviking.sh [--full] [--dry-run]"
      echo "  （无参数）  增量同步，只上传内容发生变化的文件"
      echo "  --full      忽略状态文件，强制全量上传"
      echo "  --dry-run   只统计并列出待上传项，不联网、不写状态"
      exit 0
      ;;
    *)
      echo "[错误] 未知参数：$arg（可用：--full / --dry-run / --help）"
      exit 2
      ;;
  esac
done

# 选择 Python 解释器（python / python3 / py）
PY=""
for c in python python3 py; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "[错误] 未找到 Python，请先安装 Python 3.12+ 并加入 PATH"
  exit 1
fi

# 转成 Windows 风格正斜杠路径，供 Windows 版 Python 使用
WIN_ROOT="$(cygpath -m "$PROJECT_ROOT" 2>/dev/null || printf '%s' "$PROJECT_ROOT")"

echo "=============================================="
echo " OpenViking 索引同步"
echo " 项目根：$PROJECT_ROOT"
echo " Python：$PY"
echo " 模式：  $( [ "$FULL_SYNC" = "1" ] && echo '全量' || echo '增量' )$( [ "$DRY_RUN" = "1" ] && echo '（dry-run）' || true )"
echo "=============================================="

# 核心逻辑交给 Python（统一 UTF-8 + \uXXXX 转义，规避编码坑）
"$PY" - "$WIN_ROOT" "$FULL_SYNC" "$DRY_RUN" <<'PY'
# -*- coding: utf-8 -*-
"""OpenViking 索引同步核心逻辑：扫描源码/文档、生成数据库结构文档、按内容 sha1 增量分批上传"""
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request

ROOT = sys.argv[1]
FULL_SYNC = sys.argv[2] == "1"
DRY_RUN = sys.argv[3] == "1"

API = "http://localhost:1933"
ROOT_URI = "viking://resources/fashion-inspo"
STATE_REL = "scripts/.sync_openviking_state.json"
STATE_PATH = os.path.join(ROOT, "scripts", ".sync_openviking_state.json")
STATE_VERSION = 1
CHUNK = 60          # 每批操作数
MAX_BYTES = 2 * 1024 * 1024


def log(msg):
    print(msg, flush=True)


# ---------- 0. 健康检查（--dry-run 跳过，便于离线预览） ----------
if DRY_RUN:
    log("[信息] --dry-run：跳过服务健康检查")
else:
    try:
        with urllib.request.urlopen(f"{API}/health", timeout=5) as r:
            if r.status != 200:
                raise RuntimeError(f"HTTP {r.status}")
    except Exception as e:
        log(f"[错误] OpenViking 服务不可达（{API}）：{e}")
        sys.exit(1)
    log(f"[信息] OpenViking 服务正常：{API}")

# ---------- 1. 收集源码 / 文档文件 ----------
EXTS = {
    ".py", ".ts", ".tsx", ".vue", ".js", ".jsx", ".mjs", ".cjs",
    ".json", ".md", ".txt", ".css", ".scss", ".html",
    ".yaml", ".yml", ".toml", ".cfg", ".ini", ".sql", ".sh",
}
# 注意：backend/tests 已从白名单移除，测试一律不入索引
DIRS = [
    "backend/app", "backend/alembic",
    "web/src", "mobile", "browser-extension", "shared",
    "scripts", "docs", ".claude/skills", "face-service", "mcp",
]
EXTRA_ROOT = [
    "CLAUDE.md", "README.md", "README.en.md", "TODO.md",
    "LICENSE", ".gitignore", ".dsh-ignore",
]
SKIP_DIRS = {
    "node_modules", "dist", "build", ".venv", "venv", "__pycache__",
    ".git", ".expo", ".pytest_cache", "coverage", ".idea", ".vscode",
}
# 测试目录名（任意层级命中即整棵跳过）
TEST_DIR_NAMES = {"tests", "test", "__tests__", "testing"}
# 测试文件名模式：test_*.py / *_test.py / conftest.py / *.test.* / *.spec.*
TEST_FILE_RE = re.compile(
    r"^(test_.*|.*_test|conftest|.*\.(test|spec))\.(py|js|jsx|mjs|cjs|ts|tsx|vue)$",
    re.IGNORECASE,
)
# 本脚本自己的增量状态文件（scripts/ 也在白名单里、.json 也在扩展名白名单里，
# 不显式排除会被当成源码索引进去，而且它每次运行都变，会无限自我重传）
STATE_BASENAME = ".sync_openviking_state.json"

files = set()
skipped_tests = 0      # 按文件名命中测试模式而跳过的文件
skipped_test_dirs = 0  # 整棵剪掉的测试目录数
for d in DIRS:
    base = os.path.join(ROOT, *d.split("/"))
    if not os.path.isdir(base):
        continue
    for dirpath, dirnames, filenames in os.walk(base):
        kept = []
        for x in dirnames:
            if x in SKIP_DIRS:
                continue
            if x.lower() in TEST_DIR_NAMES:
                skipped_test_dirs += 1
                continue
            kept.append(x)
        dirnames[:] = kept
        for fn in filenames:
            if fn == STATE_BASENAME or fn == STATE_BASENAME + ".tmp":
                continue
            if os.path.splitext(fn)[1].lower() not in EXTS:
                continue
            if TEST_FILE_RE.match(fn):
                skipped_tests += 1
                continue
            fp = os.path.join(dirpath, fn)
            if os.path.getsize(fp) <= MAX_BYTES:
                files.add(fp)

for f in EXTRA_ROOT:
    p = os.path.join(ROOT, f)
    if os.path.isfile(p):
        files.add(p)

# 根目录下其余文本文件（含中文名文件，如「待改进列表.txt」）自动纳入
for f in os.listdir(ROOT):
    if f.startswith(".") or f in EXTRA_ROOT:
        continue
    p = os.path.join(ROOT, f)
    if not os.path.isfile(p) or os.path.splitext(f)[1].lower() not in EXTS:
        continue
    if TEST_FILE_RE.match(f):
        skipped_tests += 1
        continue
    files.add(p)

# ---------- 2. 生成数据库结构文档 ----------
TABLE_NOTES = {
    "inspirations": "穿搭灵感素材主表（软删除/垃圾桶，deleted_at/trash_reason）",
    "inspiration_tags": "素材-标签多对多关联表（含来源列 source）",
    "ai_analysis_log": "素材 AI 分析日志",
    "ai_extracted_tags": "素材 AI 分析标签",
    "ai_quality_review": "AI 质量审核记录（负样本学习）",
    "tags": "标签表（支持层级 parent_id）",
    "tag_history": "标签操作历史表",
    "bloggers": "博主表",
    "models": "模特表",
    "model_face_embeddings": "模特人脸特征向量表",
    "inspiration_face_detections": "素材人脸检测记录",
    "model_photo_sets": "人物照片组",
    "model_photos": "人物照片表",
    "task_queue": "后台任务表（采集/审核/向量回填等）",
    "pending_vector_backfills": "向量回填攒批待回填表",
    "service_heartbeats": "服务心跳表",
    "audit_logs": "操作审计日志表",
    "scraper_seen_urls": "采集去重 URL 记录表",
    "scraper_schedules": "定时采集计划表",
    "scraper_tasks": "采集任务表",
    "scraper_hashtags": "采集话题标签存档表",
    "blogger_enrichment_skips": "博主主页补全跳过表",
    "blogger_face_embeddings": "博主人脸特征向量表",
    "tag_aliases": "标签别名表",
}


def quote(t):
    return '"' + t.replace('"', '""') + '"'


def dump_schema(db_path, title):
    """从 SQLite 库导出结构化 schema 文档（只读打开）"""
    if not os.path.isfile(db_path):
        return None
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.cursor()
    version = cur.execute("select sqlite_version()").fetchone()[0]
    tables = [
        r[0]
        for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    lines = [f"# {title}", "", f"> 自动生成自 SQLite 数据库 `{db_path}`（SQLite 版本 {version}），共 {len(tables)} 张表。", ""]
    lines.append("## 表总览")
    lines.append("")
    lines.append("| 表名 | 列数 | 行数 | 说明 |")
    lines.append("|------|------|------|------|")
    overview = []
    for t in tables:
        cols = cur.execute(f"PRAGMA table_info({quote(t)})").fetchall()
        try:
            n = cur.execute(f"SELECT COUNT(*) FROM {quote(t)}").fetchone()[0]
        except Exception:
            n = None
        overview.append((t, len(cols), n))
    for t, ccount, n in overview:
        lines.append(f"| {t} | {ccount} | {n if n is not None else 'N/A'} | {TABLE_NOTES.get(t, '')} |")
    lines.append("")
    lines.append("## 各表详细结构")
    lines.append("")
    for t in tables:
        cols = cur.execute(f"PRAGMA table_info({quote(t)})").fetchall()
        lines.append(f"### 表 `{t}`")
        lines.append("")
        if t in TABLE_NOTES:
            lines.append(f"说明：{TABLE_NOTES[t]}")
            lines.append("")
        lines.append("| 列名 | 类型 | 非空 | 默认值 | 主键 |")
        lines.append("|------|------|------|--------|------|")
        for _cid, name, ctype, notnull, dflt, pk in cols:
            d = "" if dflt is None else str(dflt)
            lines.append(f"| {name} | {ctype} | {'是' if notnull else '否'} | {d} | {'是' if pk else ''} |")
        idxs = cur.execute(f"PRAGMA index_list({quote(t)})").fetchall()
        if idxs:
            lines.append("")
            lines.append("**索引**：")
            for _seqno, iname, unique, origin, _partial in idxs:
                try:
                    ic = cur.execute(f"PRAGMA index_info({quote(iname)})").fetchall()
                    colnames = ", ".join(c[2] for c in ic)
                except Exception:
                    colnames = "?"
                lines.append(f"- `{iname}`（{'唯一' if unique else '普通'}，origin={origin}）：{colnames}")
        fks = cur.execute(f"PRAGMA foreign_key_list({quote(t)})").fetchall()
        if fks:
            lines.append("")
            lines.append("**外键**：")
            for fk in fks:
                lines.append(f"- `{fk[3]}` → `{fk[2]}({fk[4]})`（on_delete={fk[6]}，on_update={fk[7]}）")
        lines.append("")
    con.close()
    return "\n".join(lines)


# ---------- 3. 载入增量状态 ----------
prev = {}
if FULL_SYNC:
    log("[信息] --full：忽略状态文件，强制全量上传")
elif os.path.isfile(STATE_PATH):
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as fh:
            state = json.load(fh)
        if isinstance(state, dict) and state.get("version") == STATE_VERSION:
            prev = state.get("files") or {}
            log(f"[信息] 已载入增量状态：{len(prev)} 条（{STATE_REL}）")
        else:
            log("[警告] 状态文件版本不符，本次按全量处理")
    except Exception as e:
        log(f"[警告] 状态文件读取失败（{e}），本次按全量处理")
else:
    log("[信息] 未找到状态文件，首次运行按全量处理")

# ---------- 4. 组装增量操作 ----------
ops = []        # [(uri, content)] 本次需要上传的
new_state = {}  # {相对路径: 内容 sha1}
skipped = 0


def consider(rel, content):
    """内容 sha1 与上次一致则跳过上传，否则纳入本次同步。"""
    global skipped
    digest = hashlib.sha1(content.encode("utf-8")).hexdigest()
    new_state[rel] = digest
    if prev.get(rel) == digest:
        skipped += 1
        return
    ops.append((f"{ROOT_URI}/{rel}", content))


for fp in sorted(files):
    rel = os.path.relpath(fp, ROOT).replace("\\", "/")
    with open(fp, "r", encoding="utf-8", errors="replace") as fh:
        consider(rel, fh.read())

schema_main = dump_schema(os.path.join(ROOT, "backend", "fashion_inspo.db"), "fashion-inspo 主数据库结构")
schema_face = dump_schema(os.path.join(ROOT, "face-service", "face_service.db"), "face-service 数据库结构")
if schema_main:
    consider("database/fashion_inspo_db_schema.md", schema_main)
if schema_face:
    consider("database/face_service_db_schema.md", schema_face)

stale = sorted(set(prev) - set(new_state))
log(f"[信息] 入库文件 {len(files)} 个；排除测试目录 {skipped_test_dirs} 个、测试文件 {skipped_tests} 个")
log(f"[信息] 内容未变跳过 {skipped} 项；本次需上传 {len(ops)} 项（索引口径共 {len(new_state)} 项）")
if stale:
    log(f"[提示] 上次状态里有 {len(stale)} 项已不在本次收集范围内（文件被删/被移出白名单），本次不会从索引删除")

if not ops:
    log("[完成] 索引已是全量最新，无需上传")
    log(f"[提示] 状态文件：{STATE_REL}")
    sys.exit(0)

if DRY_RUN:
    log("")
    log(f"[dry-run] 本次会上传 {len(ops)} 项，前 20 项：")
    for uri, _ in ops[:20]:
        log(f"  - {uri}")
    if len(ops) > 20:
        log(f"  ... 其余 {len(ops) - 20} 项")
    log("[dry-run] 未联网上传，也未写状态文件")
    sys.exit(0)

# ---------- 5. 分批上传（ensure_ascii 生成纯 ASCII JSON，规避编码问题） ----------
created = updated = 0
batches = (len(ops) + CHUNK - 1) // CHUNK
for i in range(0, len(ops), CHUNK):
    chunk = ops[i : i + CHUNK]
    payload = {
        "root_uri": ROOT_URI,
        "operations": [{"uri": u, "content": c, "mode": "upsert"} for u, c in chunk],
        "wait": False,
    }
    body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    req = urllib.request.Request(
        f"{API}/api/v1/content/batch-write",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            res = json.loads(r.read().decode("utf-8"))
        if res.get("status") != "ok":
            raise RuntimeError(json.dumps(res.get("error", res), ensure_ascii=False))
        c = len(res["result"].get("created", []))
        u = len(res["result"].get("updated", []))
        created += c
        updated += u
        log(f"[批次 {i // CHUNK + 1}/{batches}] 新建 {c}，更新 {u}")
    except Exception as e:
        log(f"[错误] 批次 {i // CHUNK + 1} 上传失败：{e}")
        log("[错误] 状态文件未更新，下次运行会重传本次全部内容")
        sys.exit(1)

# ---------- 6. 全部批次成功后才落状态（原子替换） ----------
tmp_path = STATE_PATH + ".tmp"
with open(tmp_path, "w", encoding="utf-8") as fh:
    json.dump(
        {
            "version": STATE_VERSION,
            "synced_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "root": ROOT,
            "files": new_state,
        },
        fh,
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
    )
os.replace(tmp_path, STATE_PATH)

log(f"[完成] 本次上传 {len(ops)} 项：新建 {created}，更新 {updated}")
log(f"[信息] 增量状态已更新：{STATE_REL}")
log("[提示] 向量/语义摘要在后台异步生成，稍后即可用 memfind/memsearch 检索")
PY
