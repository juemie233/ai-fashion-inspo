#!/usr/bin/env python3
"""从后端源码生成两份「结构清单」文档。

生成物：
    docs/overview/API参考.md    —— 路由清单（方法 / 路径 / 函数 / 首行说明）
    docs/overview/数据模型.md   —— 表与列清单（类型 / 主键 / 可空 / 索引 / 外键 / 备注）

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


def main() -> int:
    parser = argparse.ArgumentParser(description="从后端源码生成 API 与数据模型清单文档")
    parser.add_argument("--check", action="store_true", help="只校验文档是否与代码一致，不写入")
    args = parser.parse_args()

    api_md = render_api(collect_api())
    model_md = render_models(collect_models())

    stale = []
    for path, content in ((OUT_API, api_md), (OUT_MODEL, model_md)):
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        # 忽略日期行，避免「只因为换了天」就判过期
        strip = lambda s: "\n".join(l for l in s.splitlines() if not l.startswith("> **状态**：自动生成"))
        if strip(current) != strip(content):
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

    for p in (OUT_API, OUT_MODEL):
        print(f"已写入 {p.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
