#!/usr/bin/env python3
"""从后端源码生成两份「结构清单」文档。

生成物：
    docs/overview/API参考.md      —— 路由清单（方法 / 路径 / 函数 / 首行说明）
    docs/overview/数据模型.md     —— 表与列清单（类型 / 主键 / 可空 / 索引 / 外键 / 备注）
    docs/overview/配置项参考.md   —— Settings 字段清单（环境变量名 / 类型 / 默认值 / 说明）

为什么生成而不是手写：这两份内容 100% 由代码决定，手写必然一个月后过期，
过期文档比没有文档更糟。改完 router/model 后重跑本脚本即可。

用法：
    python scripts/data/gen_docs_from_code.py            # 写入两份文档
    python scripts/data/gen_docs_from_code.py --check    # 只校验是否过期（CI/自查用）
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_APP = ROOT / "backend" / "app"
ROUTERS = BACKEND_APP / "routers"
MODELS = BACKEND_APP / "models"
OUT_API = ROOT / "docs" / "overview" / "API参考.md"
OUT_MODEL = ROOT / "docs" / "overview" / "数据模型.md"
CONFIG_PY = BACKEND_APP / "config.py"
OUT_CONFIG = ROOT / "docs" / "overview" / "配置项参考.md"

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
# ai_* 子路由无前缀，统一挂在 /api/ai 下（见 routers/ai.py 的 include_router）
AI_PREFIX = "/api/ai"


def _docline(node: ast.AST) -> str:
    """函数/类说明的首行（去掉多余空白）。"""
    doc = ast.get_docstring(node) or ""
    for line in doc.splitlines():
        text = line.strip()
        if text:
            return text
    return ""


def _router_prefixes(tree: ast.Module) -> dict[str, str]:
    """找出模块内 `xxx = APIRouter(prefix="...")` 的前缀。"""
    out: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        if not (isinstance(func, ast.Name) and func.id == "APIRouter"):
            continue
        prefix = ""
        for kw in node.value.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                prefix = str(kw.value.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                out[target.id] = prefix
    return out


def collect_api() -> list[tuple[str, str, list[tuple[str, str, str, str]]]]:
    """返回 [(模块名, 完整前缀, [(方法, 完整路径, 函数名, 说明)])]。"""
    modules = []
    for path in sorted(ROUTERS.glob("*.py")):
        name = path.stem
        if name.startswith("__") or name == "_person_bio":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        prefixes = _router_prefixes(tree)

        # 子路由（ai_* / quality_learner）挂在 /api/ai 之下；父级前缀由各自的 APIRouter 声明
        extra = AI_PREFIX if (name.startswith("ai_") or name == "quality_learner") else ""

        endpoints: list[tuple[str, str, str, str]] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                    continue
                attr = dec.func.attr
                if attr not in HTTP_METHODS and attr != "websocket":
                    continue
                router_obj = getattr(dec.func.value, "id", "")
                if router_obj not in prefixes:
                    continue
                route = ""
                if dec.args and isinstance(dec.args[0], ast.Constant):
                    route = str(dec.args[0].value)
                # 每个 router 变量用自己的前缀（face_scan 文件里 router 与 match_router 前缀不同）
                full = extra + prefixes[router_obj] + route
                method = "WS" if attr == "websocket" else attr.upper()
                endpoints.append((method, full or "（根）", node.name, _docline(node)))
        if endpoints:
            shown = "、".join(
                sorted({extra + p for p in prefixes.values() if p or extra})
            ) or "（无前缀）"
            modules.append((name, shown, endpoints))
    return modules


def collect_models() -> list[tuple[str, str, list[dict[str, str]]]]:
    """返回 [(表名, 类说明, [{col, type, attr, note}])]。"""
    tables = []
    for path in sorted(MODELS.glob("*.py")):
        if path.stem.startswith("__"):
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        lines = source.splitlines()
        for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
            table = ""
            for stmt in cls.body:
                if isinstance(stmt, ast.Assign) and any(
                    getattr(t, "id", "") == "__tablename__" for t in stmt.targets
                ):
                    if isinstance(stmt.value, ast.Constant):
                        table = str(stmt.value.value)
            if not table:
                continue
            cols: list[dict[str, str]] = []
            for stmt in cls.body:
                if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
                    continue
                raw = "\n".join(lines[stmt.lineno - 1: stmt.end_lineno or stmt.lineno])
                note = ""
                m = re.search(r"\)\s*#\s*(.+)$", raw)
                if m:
                    note = m.group(1).strip()
                elif "comment=" in raw:
                    cm = re.search(r'comment="([^"]*)"', raw)
                    note = cm.group(1) if cm else ""
                ann = ast.unparse(stmt.annotation) if stmt.annotation else ""
                attrs = []
                if re.search(r"primary_key=True", raw):
                    attrs.append("PK")
                if re.search(r"nullable=False", raw) or (
                    re.search(r"ForeignKey\(", raw) and "nullable=True" not in raw
                ):
                    attrs.append("非空")
                if re.search(r"index=True", raw):
                    attrs.append("索引")
                if "ForeignKey(" in raw:
                    fk = re.search(r'ForeignKey\("([^"]+)"', raw)
                    if fk:
                        attrs.append(f"FK→{fk.group(1)}")
                if re.search(r"\bunique=True", raw):
                    attrs.append("唯一")
                cols.append(
                    {
                        "col": stmt.target.id,
                        "type": ann.replace("Mapped[", "").rstrip("]"),
                        "attr": " / ".join(attrs) or "—",
                        "note": note,
                    }
                )
            tables.append((table, _docline(cls), cols))
    return tables


def render_api(modules) -> str:
    today = date.today().isoformat()
    total = sum(len(e) for _, _, e in modules)
    out = [
        "# API 参考（路由清单）",
        "",
        f"> **状态**：自动生成（{today}，共 {len(modules)} 个模块 / {total} 个端点）。",
        "> 由 `scripts/data/gen_docs_from_code.py` 从 `backend/app/routers/*.py` 抽取，**不要手改**——",
        "> 改了 router 就重跑脚本（`--check` 可校验是否过期）。",
        "> 参数与响应结构以代码和 `/docs`（FastAPI 自动 OpenAPI，后端在跑时 `http://127.0.0.1:18888/docs`）为准。",
        "",
        "## 模块总览",
        "",
        "| 模块 | 前缀 | 端点数 |",
        "| --- | --- | ---: |",
    ]
    for name, base, eps in modules:
        out.append(f"| [{name}](#{name.lower()}) | `{base}` | {len(eps)} |")
    out.append("")
    for name, base, eps in modules:
        out += [f"## {name}", "", f"前缀：`{base}`", "", "| 方法 | 路径 | 函数 | 说明 |", "| --- | --- | --- | --- |"]
        for method, full, func, doc in eps:
            out.append(f"| `{method}` | `{full}` | `{func}` | {doc.replace('|', '\\|') or '—'} |")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_models(tables) -> str:
    today = date.today().isoformat()
    total_cols = sum(len(c) for _, _, c in tables)
    out = [
        "# 数据模型（表与列清单）",
        "",
        f"> **状态**：自动生成（{today}，共 {len(tables)} 张表 / {total_cols} 列）。",
        "> 由 `scripts/data/gen_docs_from_code.py` 从 `backend/app/models/*.py` 抽取，**不要手改**——",
        "> 改了模型就重跑脚本；改用 Alembic 迁移见 [数据库迁移与回滚](../ops/数据库迁移与回滚.md)。",
        "> 表关系与业务语义见各功能文档（如 [素材生命周期与垃圾桶](../features/素材生命周期与垃圾桶.md)）。",
        "",
        "## 表总览",
        "",
        "| 表 | 列数 | 说明 |",
        "| --- | ---: | --- |",
    ]
    for table, doc, cols in tables:
        out.append(f"| [`{table}`](#{table}) | {len(cols)} | {doc.replace('|', '\\|') or '—'} |")
    out.append("")
    for table, doc, cols in tables:
        out += [f"## {table}", "", f"{doc or '（无说明）'}", "", "| 列 | 类型 | 属性 | 备注 |", "| --- | --- | --- | --- |"]
        for c in cols:
            out.append(
                f"| `{c['col']}` | `{c['type']}` | {c['attr']} | {c['note'].replace('|', '\\|') or '—'} |"
            )
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _field_default(node: ast.AnnAssign) -> tuple[str, str]:
    """返回 (默认值展示, 约束补充)。Field(default=..., ge=..., le=...) 单独拆解。"""
    value = node.value
    if isinstance(value, ast.Call) and getattr(value.func, "id", "") == "Field":
        default = "（必填）"
        limits = []
        for kw in value.keywords:
            if kw.arg == "default":
                default = ast.unparse(kw.value)
            elif kw.arg in {"ge", "gt", "le", "lt"}:
                limits.append(f"{kw.arg}={ast.unparse(kw.value)}")
            elif kw.arg == "alias" and isinstance(kw.value, ast.Constant):
                default = f"{default}｜alias={kw.value.value}"
        return default, "、".join(limits)
    return (ast.unparse(value) if value is not None else "（必填）"), ""


# 配置项分组：按字段名匹配，**顺序敏感**（先匹配先归组）。
# 为什么不用注释里的 `# xxx` 当分组：config.py 的注释既有小节标题也有说明文字，
# 形状上无法区分（如「慢请求阈值（毫秒）：…」是说明、「服务器（默认仅本机…）」是标题）。
# 用字段名规则可判定、可复查；新增字段若匹配不到会在运行时告警到 stderr。
CONFIG_GROUPS: list[tuple[str, str]] = [
    ("应用", r"^(app_name|app_version|debug)$"),
    ("日志", r"^(log_|slow_request_ms)"),
    ("服务器", r"^(host|port)$"),
    ("数据库", r"^(database_url|sqlite_)"),
    (
        "存储与媒体",
        r"^(storage_root|images_dir|thumbnails_dir|videos_dir|trash_dir|keyframes_dir|"
        r"person_photos_dir|person_thumbnails_dir|max_.*_upload_mb|thumbnail_|keyframe_|"
        r"video_analysis_max_frames)",
    ),
    ("向量检索", r"^(lancedb_|clip_model_name|vector_)"),
    (
        "AI 推理（Ollama）",
        r"^(ollama_|ai_(analysis|low_confidence|temperature|top_p|top_k|num_)|"
        r"auto_delete_no_content_images)",
    ),
    ("本地出图（ComfyUI）", r"^(comfyui_|image_gen_)"),
    ("豆包云端出图", r"^(ark_|seedream_)"),
    ("姿势图库", r"^pose_gallery_"),
    ("质量审核", r"^(quality_|manual_upload_auto_approve|ai_generated_confidence_threshold)"),
    ("标签", r"^tag_"),
    ("人脸识别", r"^(face_|face_scan_video_max_frames)"),
    ("任务与并发", r"^(worker_|task_|analyze_concurrency)"),
    ("采集与 Chrome", r"^(scraper_|chrome_)"),
    ("抖音 f2", r"^f2_"),
    ("备份", r"^backup_"),
    ("垃圾桶", r"^trash_retention_days$"),
    ("安全", r"^(api_key|cors_origins)$"),
]


def _config_group(field: str) -> str:
    for group, pattern in CONFIG_GROUPS:
        if re.match(pattern, field):
            return group
    return "未分组"


def collect_settings() -> list[tuple[str, list[dict[str, str]]]]:
    """解析 Settings，返回 [(分组, [{env, name, type, default, note}])]。"""
    source = CONFIG_PY.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source)
    cls = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Settings"
    )

    groups: dict[str, list[dict[str, str]]] = {}
    unmatched: list[str] = []
    for node in cls.body:
        if not (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)):
            continue
        field = node.target.id
        # 收集紧邻注释块作为说明（去掉纯 ─ 分隔线与小节标题行）
        block: list[str] = []
        i = node.lineno - 2
        while i >= 0:
            text = lines[i].strip()
            if text.startswith("#"):
                block.insert(0, text.lstrip("#").strip())
                i -= 1
                continue
            if not text and block:
                i -= 1
                continue
            break
        notes = []
        for text in block:
            stripped = text.strip("─ -").strip()
            if stripped:  # 跳过纯 ─ 分隔线
                notes.append(stripped)
        default, limits = _field_default(node)
        if limits:
            notes.append(f"约束：{limits}")
        group = _config_group(field)
        # 注释块首行常常就是小节标题（如「安全」「垃圾桶（软删除）」），从说明里剥掉
        if notes and notes[0].startswith(group):
            head = notes[0][len(group):].strip().strip("（）() ")
            notes = ([head] if head else []) + notes[1:]
        if group == "未分组":
            unmatched.append(field)
        row = {
            "env": field.upper(),
            "name": field,
            "type": ast.unparse(node.annotation).replace("|", "\\|"),
            "default": default.replace("|", "\\|"),
            "note": " ".join(notes).replace("|", "\\|"),
        }
        groups.setdefault(group, []).append(row)

    if unmatched:
        print(
            "警告：以下配置项未匹配任何分组，已归入「未分组」，请补 CONFIG_GROUPS："
            + "、".join(unmatched),
            file=sys.stderr,
        )

    order = [g for g, _ in CONFIG_GROUPS if g in groups] + (["未分组"] if "未分组" in groups else [])
    return [(g, groups[g]) for g in order]


def render_config(groups) -> str:
    today = date.today().isoformat()
    total = sum(len(rows) for _, rows in groups)
    out = [
        "# 配置项参考",
        "",
        f"> **状态**：自动生成（{today}，共 {total} 项 / {len(groups)} 组）。",
        "> 由 `scripts/data/gen_docs_from_code.py` 从 `backend/app/config.py` 的 `Settings` 抽取，**不要手改**。",
        "> 加载顺序：**代码默认值 → `backend/.env` → 进程环境变量**（后者覆盖前者，`extra=\"ignore\"` 不认识的键会被忽略）。",
        "> 环境变量名 = 字段名大写（pydantic-settings 大小写不敏感）；改部署值写 `backend/.env`，密钥绝不入库。",
        "",
        "## 分组",
        "",
        "| 分组 | 配置项数 |",
        "| --- | ---: |",
    ]
    for group, rows in groups:
        out.append(f"| {group} | {len(rows)} |")
    out.append("")
    for group, rows in groups:
        out += [
            f"## {group}",
            "",
            "| 配置项 | 环境变量 | 类型 | 默认值 | 说明 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for row in rows:
            out.append(
                f"| `{row['name']}` | `{row['env']}` | `{row['type']}` | `{row['default']}` | {row['note'] or '—'} |"
            )
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="从后端源码生成 API 与数据模型清单文档")
    parser.add_argument("--check", action="store_true", help="只校验文档是否与代码一致，不写入")
    args = parser.parse_args()

    api_md = render_api(collect_api())
    model_md = render_models(collect_models())
    config_md = render_config(collect_settings())

    docs = ((OUT_API, api_md), (OUT_MODEL, model_md), (OUT_CONFIG, config_md))
    stale = []

    def _strip_status_line(text: str) -> str:
        """忽略生成的日期注释行，避免「只因为换了天」就判文档过期。"""
        return chr(10).join(
            line for line in text.splitlines() if not line.startswith("> **状态**：自动生成")
        )

    for path, content in docs:
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if _strip_status_line(current) != _strip_status_line(content):
            stale.append(path)
        if not args.check:
            path.write_text(content, encoding="utf-8")

    if args.check:
        if stale:
            for p in stale:
                print(f"过期：{p.relative_to(ROOT)}", file=sys.stderr)
            print("请重跑：python scripts/data/gen_docs_from_code.py", file=sys.stderr)
            return 1
        print("文档与代码一致")
        return 0

    for p in (OUT_API, OUT_MODEL, OUT_CONFIG):
        print(f"已写入 {p.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
