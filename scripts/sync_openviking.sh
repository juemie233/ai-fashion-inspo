#!/usr/bin/env bash
# ============================================================
# OpenViking 索引同步脚本（Git Bash 版）
# 用途：把项目「代码 + 文档 + 数据库结构」同步到 OpenViking
#       索引（viking://resources/fashion-inspo/），供语义检索。
# 用法：双击 scripts/sync_openviking.bat（推荐），
#       或在 Git Bash 中直接执行本脚本。
# 说明：幂等（upsert），可重复执行；同步后向量/摘要由
#       OpenViking 后台异步生成。
# ============================================================
set -euo pipefail

# 脚本所在目录 = scripts/，项目根 = 上一级
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

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
echo "=============================================="

# 核心逻辑交给 Python（统一 UTF-8 + \uXXXX 转义，规避编码坑）
"$PY" - "$WIN_ROOT" <<'PY'
# -*- coding: utf-8 -*-
"""OpenViking 索引同步核心逻辑：扫描源码/文档、生成数据库结构文档、分批上传"""
import json
import os
import re
import sqlite3
import sys
import urllib.request

ROOT = sys.argv[1]
API = "http://localhost:1933"
ROOT_URI = "viking://resources/fashion-inspo"
CHUNK = 60          # 每批操作数
MAX_BYTES = 2 * 1024 * 1024


def log(msg):
    print(msg, flush=True)


# ---------- 0. 健康检查 ----------
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
DIRS = [
    "backend/app", "backend/alembic", "backend/tests",
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

files = set()
for d in DIRS:
    base = os.path.join(ROOT, *d.split("/"))
    if not os.path.isdir(base):
        continue
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [x for x in dirnames if x not in SKIP_DIRS]
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            if os.path.splitext(fn)[1].lower() in EXTS and os.path.getsize(fp) <= MAX_BYTES:
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
    if os.path.isfile(p) and os.path.splitext(f)[1].lower() in EXTS:
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


ops = []  # [(uri, content)]
for fp in sorted(files):
    rel = os.path.relpath(fp, ROOT).replace("\\", "/")
    uri = f"{ROOT_URI}/{rel}"
    with open(fp, "r", encoding="utf-8", errors="replace") as fh:
        ops.append((uri, fh.read()))

schema_main = dump_schema(os.path.join(ROOT, "backend", "fashion_inspo.db"), "fashion-inspo 主数据库结构")
schema_face = dump_schema(os.path.join(ROOT, "face-service", "face_service.db"), "face-service 数据库结构")
if schema_main:
    ops.append((f"{ROOT_URI}/database/fashion_inspo_db_schema.md", schema_main))
if schema_face:
    ops.append((f"{ROOT_URI}/database/face_service_db_schema.md", schema_face))

log(f"[信息] 待同步：{len(ops)} 个文件/文档")
if not ops:
    log("[错误] 未收集到任何文件")
    sys.exit(1)

# ---------- 3. 分批上传（ensure_ascii 生成纯 ASCII JSON，规避编码问题） ----------
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
        sys.exit(1)

log(f"[完成] 共 {len(ops)} 项：新建 {created}，更新 {updated}")
log("[提示] 向量/语义摘要在后台异步生成，稍后即可用 memfind/memsearch 检索")
PY
