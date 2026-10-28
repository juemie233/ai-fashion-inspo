"""项目代码量体检报告：一行命令产出「总览 / 分布 / 结构 / 质量 / 数据量」五节。

为什么单独写一个（而不是继续用 ``count_lines.ps1`` / ``project_stats.py``）：
- ``count_lines.ps1`` 只有按扩展名的行数，口径写死在 PowerShell 里，Windows 之外跑不了；
- ``project_stats.py`` 是「代码/数据/仓库/测试」四件事的体检，分块按目录，不回答
  「哪一类占多少、结构指标多少、质量指标多少」；
- 本脚本按**职能分类**（后端源码/测试、前端源码/测试、脚本、文档、迁移、移动端…）
  给出文件数、行数与占比，并补结构指标（路由/端点/服务/组件…）、质量指标
  （测试数、覆盖率、测试/源码比、门禁项数）与真实库的数据量。

统计口径（写死在 ``BUCKET_RULES`` 里，便于对照）：
- **计入**：真实源码、测试、脚本与文档，以及项目配置（json/yaml/toml/ini/txt 等）；
- **排除**：``.git`` / ``node_modules`` / 虚拟环境 / 缓存 / 构建产物（dist/build/coverage/
  test-results）/ 存储与备份目录（storage/backups/logs）/ 模型权重与媒体文件
  （safetensors/gguf/onnx/pt/pth/ckpt/bin、图片音视频字体）/ 数据库文件 / 锁文件；
- 二进制判定：前 4KB 含 ``\\0`` 即跳过（防止把图片/权重当文本数行）；
- 行数 = 文件的物理行数（含空行与注释，与 ``cloc`` 的 code-only 口径不同）。

两种统计范围：
- 默认 **工作区**：直接扫当前文件，未提交的改动也计入（看「现在到底多大」用这个）；
- ``--ref <commit>``：用 ``git archive`` 在内存里读该提交（不写盘、不动工作区），
  同一提交任何时候跑都得到同样的数字（写报告、做趋势对比用这个）。

只读：不修改任何文件，不连数据库以外的服务（DB 用 ``mode=ro`` 只读打开）。

用法::

    python scripts/code_report.py                 # 工作区 + 静态近似测试数
    python scripts/code_report.py --deep          # 再跑 pytest 收集 / vitest list 取精确测试数
    python scripts/code_report.py --ref 1844455   # 固定到某次提交（可复现）
    python scripts/code_report.py --json out.json # 另存 JSON，便于逐月对比
    python scripts/code_report.py --no-db         # 跳过数据库那节
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sqlite3
import subprocess
import sys
import tarfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

"""仓库根：本脚本在 scripts/ 下，取父目录（与 cwd 无关，双击 BAT 也能跑对）。"""
ROOT = Path(__file__).resolve().parent.parent

"""参与统计的文本扩展名（二进制/媒体不在内，见模块 docstring 的排除说明）。"""
TEXT_EXTS = {
    ".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".vue", ".md", ".mdx",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".txt",
    ".sh", ".bat", ".ps1", ".sql", ".html", ".css", ".scss", ".less",
}

"""即使扩展名命中也要跳过的文件（锁文件：自动生成、行数无信息量）。"""
SKIP_NAMES = {
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lockb",
    "poetry.lock",
    "Pipfile.lock",
    ".coverage",
    # 脚本产出的数据清单（不是源码：批量裁剪的候选清单，几千行 JSON）
    "crop_candidates.json",
}

"""整棵子树跳过的目录：依赖 / 缓存 / 虚拟环境 / 构建产物 / 数据与备份 / 原生构建。"""
EXCLUDE_DIRS = {
    ".git", ".hg", ".svn",
    "node_modules", "bower_components",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".pytype",
    ".venv", "venv", ".tox", ".nox", "site-packages", ".egg-info",
    "dist", "build", "out", "coverage", "htmlcov", ".nyc_output",
    "test-results", "playwright-report", ".next", ".turbo", ".nuxt",
    "storage", "backups", "logs", "uploads",
    ".expo", "android", "ios", ".gradle", ".cxx",
    ".idea", ".vscode", ".DS_Store",
}

"""模型权重 / 媒体 / 数据库等非源码产物（显式列出，便于说明「已排除模型权重」）。"""
EXCLUDE_EXTS = {
    ".safetensors", ".gguf", ".onnx", ".pt", ".pth", ".ckpt", ".bin",
    ".pkl", ".joblib", ".npy", ".npz", ".h5", ".pb", ".tflite",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".ico", ".svg", ".heic",
    ".mp4", ".mov", ".avi", ".webm", ".mkv", ".flv",
    ".mp3", ".wav", ".m4a", ".flac",
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    ".db", ".sqlite", ".sqlite3", ".zip", ".7z", ".rar", ".gz", ".tar",
    ".log", ".pdf", ".docx", ".xlsx", ".pptx", ".csv",
}

BUCKET_ORDER = [
    "backend_src",
    "frontend_src",
    "backend_tests",
    "frontend_tests",
    "scripts",
    "docs",
    "root_other",
    "backend_migrations",
    "mobile",
    "browser_extension",
    "shared_types",
    "frontend_e2e",
]

BUCKET_LABELS = {
    "backend_src": "后端 源码",
    "frontend_src": "前端 源码",
    "backend_tests": "后端 测试",
    "frontend_tests": "前端 测试",
    "scripts": "脚本",
    "docs": "文档",
    "root_other": "根/其他（配置等）",
    "backend_migrations": "后端 迁移",
    "mobile": "移动端（源码+测试）",
    "browser_extension": "浏览器插件",
    "shared_types": "共享类型",
    "frontend_e2e": "前端 e2e",
}

"""测试/源码比的分母：只算「业务源码」，不含测试、迁移、文档、配置。"""
SOURCE_BUCKETS_FOR_RATIO = ("backend_src", "frontend_src")

"""后端根目录下属于「后端源码/配置」的文件（requirements、pyproject、alembic.ini 等）。"""
BACKEND_ROOT_FILES = {
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-test.txt",
    "pyproject.toml",
    "alembic.ini",
    "pytest.ini",
    "setup.cfg",
    "tox.ini",
    ".env.example",
    "run_tests.sh",
    "run_tests.bat",
    "README.md",
}

"""数据库里值得报行数的表（表名 → 中文说明）。缺失的表自动跳过。"""
DB_TABLES = [
    ("inspirations", "素材"),
    ("tags", "标签"),
    ("inspiration_tags", "素材-标签关联"),
    ("bloggers", "博主"),
    ("models", "模特"),
    ("inspiration_bloggers", "素材-博主绑定"),
    ("inspiration_face_detections", "人脸检测"),
    ("blogger_face_embeddings", "博主已注册人脸特征"),
    ("model_face_embeddings", "模特已注册人脸特征"),
    ("ai_analysis_log", "AI 分析日志"),
    ("ai_quality_review", "AI 质量审核"),
    ("generation_runs", "出图批次"),
    ("task_queue", "任务队列"),
    ("collections", "收藏合集"),
    ("audit_logs", "操作审计"),
]

"""环境：Windows 控制台默认 cp936，脚本中文输出要显式切 UTF-8。"""
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (ValueError, OSError):
        pass


# ─────────────────────────── 文件读取：工作区 / git ref ───────────────────────────


@dataclass
class SourceFile:
    """参与统计的一个文件：相对路径（POSIX 风格）+ 行数 + 文本（结构指标要用）。"""

    rel: str
    lines: int
    text: str


class WorktreeStore:
    """扫描工作区文件（默认口径，含未提交改动）。

    两种清单策略：
    - ``tracked_only=True``：走 ``git ls-files --cached --others --exclude-standard``
      （已跟踪 + 未忽略的新文件），结果干净、与 ``--ref`` 口径一致；
    - ``tracked_only=False``（默认）：直接遍历磁盘，**.gitignore 的文件也算**。
      为什么要它：项目里 ``docs/decisions/``（每周决策日志）、``.claude/skills/``
      这类"真实文档但被 gitignore"的内容，用 git 清单会漏掉，报告会显得比实际小。
    """

    def __init__(self, root: Path, *, tracked_only: bool = False) -> None:
        self.root = root
        self.tracked_only = tracked_only

    def _rel_paths(self) -> list[str]:
        if not self.tracked_only:
            out: list[str] = []
            for dirpath, _dirnames, filenames in _walk_pruned(self.root):
                for name in filenames:
                    out.append(
                        Path(dirpath).joinpath(name).relative_to(self.root).as_posix()
                    )
            return out
        result = subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                # -z：路径按 NUL 分隔、原样输出。默认的 quotePath 会把中文路径转成
                # "docs/ä¸­æ.md" 这种转义串，直接读会全部失败
                # （踩过：文档/迁移这类中文命名文件被静默漏掉，总行数少了 1 万多）
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            # 不是 git 仓库：直接遍历（仍会跳过依赖/产物目录）
            out: list[str] = []
            for dirpath, _dirnames, filenames in _walk_pruned(self.root):
                for name in filenames:
                    out.append(Path(dirpath).joinpath(name).relative_to(self.root).as_posix())
            return out
        raw = result.stdout.decode("utf-8", "replace")
        return sorted({item for item in raw.split(chr(0)) if item})

    def files(self) -> list[SourceFile]:
        out: list[SourceFile] = []
        for rel in self._rel_paths():
            path = self.root / rel
            item = _read_text_file(rel, path.read_bytes)
            if item is not None:
                out.append(item)
        return out


class GitRefStore:
    """读取指定提交的文件（git archive → tarfile 内存解析，不写盘、不动工作区）。"""

    def __init__(self, ref: str) -> None:
        self.ref = ref
        self._raw = self._load()

    def _load(self) -> bytes:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "archive", "--format=tar", self.ref],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise SystemExit(
                f"读取提交 {self.ref} 失败："
                f"{result.stderr.decode('utf-8', 'replace').strip()}\n"
                f"提示：确认提交存在（git log --oneline -5），或改用工作区（去掉 --ref）。"
            )
        return result.stdout

    def files(self) -> list[SourceFile]:
        out: list[SourceFile] = []
        with tarfile.open(fileobj=io.BytesIO(self._raw)) as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                rel = member.name
                if _skip_path(rel):
                    continue
                handle = tar.extractfile(member)
                if handle is None:
                    continue
                data = handle.read()
                item = _read_text_file(rel, lambda data=data: data)
                if item is not None:
                    out.append(item)
        return out


def _walk_pruned(root: Path):
    """跳过 EXCLUDE_DIRS 的遍历器（不进入依赖/产物目录，避免白扫几万文件）。"""
    import os

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
        yield dirpath, dirnames, filenames


def _skip_path(rel: str) -> bool:
    """git archive 里按路径判断是否跳过（对应工作区的 EXCLUDE_DIRS / SKIP_NAMES）。"""
    parts = PurePosixPath(rel).parts
    if any(part in EXCLUDE_DIRS for part in parts[:-1]):
        return True
    name = parts[-1]
    if name in SKIP_NAMES:
        return True
    suffix = PurePosixPath(rel).suffix.lower()
    return suffix in EXCLUDE_EXTS or suffix not in TEXT_EXTS


def _read_text_file(rel: str, read_bytes) -> SourceFile | None:
    """读一个文件并算行数；非源码/二进制/空文件返回 None。

    过滤规则与 ``_skip_path`` 一致（扩展名白名单 + 排除目录/模型权重/锁文件），
    两种统计范围共用，保证「工作区」与「--ref」口径完全相同。

    Args:
        rel: 相对仓库根的 POSIX 路径（用于分类）。
        read_bytes: 无参可调用，返回文件字节（工作区是 ``Path.read_bytes``，
            归档里是已取出的字节）。
    """
    if _skip_path(rel):
        return None
    try:
        data = read_bytes()
    except (OSError, KeyError):
        return None
    if not data:
        # 空文件（如空的 __init__.py）也要计入文件数，只是 0 行：报告里的「文件数」
        # 是目录里真实的模块文件数，跳过空文件会让路由/服务数量比实际少 1
        return SourceFile(rel=rel, lines=0, text="")
    if b"\x00" in data[:4096]:
        return None
    text = data.decode("utf-8", "replace")
    lines = text.count("\n") + (0 if text.endswith("\n") else 1)
    if lines <= 0:
        return None
    return SourceFile(rel=rel, lines=lines, text=text)


# ─────────────────────────── 分类 ───────────────────────────


def classify(rel: str) -> str:
    """把相对路径归到 12 个职能桶之一（口径见模块 docstring 与 BUCKET_LABELS）。"""
    parts = PurePosixPath(rel).parts
    name = parts[-1]
    top = parts[0] if parts else ""

    if top == "backend":
        if len(parts) > 1 and parts[1] == "alembic":
            return "backend_migrations"
        if len(parts) > 1 and parts[1] == "tests":
            return "backend_tests"
        if len(parts) > 1 and parts[1] in ("app", "scripts"):
            # app/ 里偶有 test_ 前缀的辅助文件，仍按测试归类（与 tests/ 同口径）
            if name.startswith("test_") or name.endswith("_test.py"):
                return "backend_tests"
            return "backend_src"
        if len(parts) == 2 and name in BACKEND_ROOT_FILES:
            return "backend_src"
        return "root_other"

    if top == "web":
        if len(parts) > 1 and parts[1] == "e2e":
            return "frontend_e2e"
        if len(parts) > 1 and parts[1] == "src":
            if "__tests__" in parts[:-1] or name.endswith(
                (".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")
            ):
                return "frontend_tests"
            return "frontend_src"
        # web 根的构建配置（vite.config.ts / index.html / tsconfig 等）算前端源码
        return "frontend_src"

    if top == "mobile":
        if "__tests__" in parts[:-1] or name.endswith((".test.ts", ".test.tsx", ".spec.ts")):
            return "mobile"
        return "mobile"

    if top == "browser-extension":
        return "browser_extension"
    if top == "shared":
        return "shared_types"
    if top == "scripts":
        return "scripts"
    if top == "docs":
        return "docs"
    # 仓库根的一次性脚本（daily-commit-stats.sh 之类）与文档归到各自桶
    if len(parts) == 1 and name.endswith((".sh", ".bat", ".ps1")):
        return "scripts"
    if len(parts) == 1 and name.endswith((".md", ".mdx")):
        return "docs"
    return "root_other"


@dataclass
class Stats:
    """一次统计的全部结果（既用于打印，也用于 --json）。"""

    ref: str
    scope: str
    buckets: dict[str, dict[str, int]] = field(default_factory=dict)
    languages: dict[str, dict[str, int]] = field(default_factory=dict)
    structure: dict[str, object] = field(default_factory=dict)
    quality: dict[str, object] = field(default_factory=dict)
    database: list[dict[str, object]] = field(default_factory=list)
    git: dict[str, object] = field(default_factory=dict)
    total_files: int = 0
    total_lines: int = 0


LANGUAGE_GROUPS = [
    ("Python", {".py"}),
    ("Vue", {".vue"}),
    ("TypeScript", {".ts", ".tsx"}),
    ("Markdown", {".md", ".mdx"}),
    ("JavaScript", {".js", ".mjs", ".cjs"}),
    ("Shell/BAT", {".sh", ".bat", ".ps1"}),
    ("配置（JSON/YAML/TOML/INI）", {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf"}),
    ("样式/HTML/SQL", {".css", ".scss", ".less", ".html", ".sql"}),
]


def build_stats(store, scope: str, ref: str) -> Stats:
    """扫描文件 → 分类聚合 → 结构/质量/数据量（后两项在工作区模式下才细分）。"""
    stats = Stats(ref=ref, scope=scope)
    files = store.files()

    buckets: dict[str, dict[str, int]] = defaultdict(lambda: {"files": 0, "lines": 0})
    languages: dict[str, dict[str, int]] = defaultdict(lambda: {"files": 0, "lines": 0})
    by_path: dict[str, SourceFile] = {}
    for item in files:
        by_path[item.rel] = item
        bucket = classify(item.rel)
        buckets[bucket]["files"] += 1
        buckets[bucket]["lines"] += item.lines
        suffix = PurePosixPath(item.rel).suffix.lower()
        for label, exts in LANGUAGE_GROUPS:
            if suffix in exts:
                languages[label]["files"] += 1
                languages[label]["lines"] += item.lines
                break
        else:
            languages["其他"]["files"] += 1
            languages["其他"]["lines"] += item.lines

    for key in BUCKET_ORDER:
        stats.buckets[key] = buckets.get(key, {"files": 0, "lines": 0})
    # 未定义桶（理论上不会有）也带上，免得数字对不上
    for key, value in buckets.items():
        if key not in stats.buckets:
            stats.buckets[key] = value
    stats.languages = dict(languages)
    stats.total_files = len(files)
    stats.total_lines = sum(item.lines for item in files)
    stats.structure = _structure(by_path)
    stats.quality = _quality(by_path, stats)
    stats.git = _git_info(ref if scope == "ref" else "HEAD")
    return stats


# ─────────────────────────── 结构指标 ───────────────────────────


def _structure(by_path: dict[str, SourceFile]) -> dict[str, object]:
    """结构指标：后端路由/端点/服务/模型/迁移；前端视图/组件/…/最大视图；文档行数。"""

    def paths_under(prefix: str, *, exclude_tests: bool = True) -> list[SourceFile]:
        out = []
        for rel, item in by_path.items():
            if not rel.startswith(prefix):
                continue
            if exclude_tests and (
                "__tests__" in rel or "/tests/" in rel or Path(rel).name.startswith("test_")
            ):
                continue
            out.append(item)
        return out

    # 文件数含 __init__.py：报告口径是「这个目录有多少个 Python 模块文件」，
    # 与历史发布的统计一致（26 路由 / 134 服务 / 13 模型），便于前后对比
    routers = [i for i in paths_under("backend/app/routers/") if i.rel.endswith(".py")]
    endpoints = 0
    for item in routers:
        endpoints += len(
            re.findall(r"^@router\.(?:get|post|put|patch|delete|websocket)\b", item.text, re.M)
        )
    services = [i for i in paths_under("backend/app/services/") if i.rel.endswith(".py")]
    task_runners = [i for i in services if "/task_runners/" in i.rel]
    models = [i for i in paths_under("backend/app/models/") if i.rel.endswith(".py")]
    migrations = [i for i in paths_under("backend/alembic/versions/") if i.rel.endswith(".py")]
    backend_src = [
        i
        for rel, i in by_path.items()
        if classify(rel) == "backend_src" and rel.endswith(".py")
    ]

    views = [i for i in paths_under("web/src/views/") if i.rel.endswith(".vue")]
    components = [i for i in paths_under("web/src/components/") if i.rel.endswith(".vue")]
    composables = [i for i in paths_under("web/src/composables/") if i.rel.endswith(".ts")]
    utils = [i for i in paths_under("web/src/utils/") if i.rel.endswith(".ts")]
    api_modules = [i for i in paths_under("web/src/api/") if i.rel.endswith(".ts")]
    type_modules = [i for i in paths_under("web/src/types/") if i.rel.endswith(".ts")]
    stores = [i for i in paths_under("web/src/stores/") if i.rel.endswith(".ts")]
    largest_view = max(views, key=lambda i: i.lines, default=None)

    docs_root = {name: by_path.get(name) for name in ("README.md", "CLAUDE.md", "TODO.md")}
    docs_dir = [i for i in paths_under("docs/") if i.rel.endswith((".md", ".mdx"))]

    return {
        "routers": len(routers),
        "endpoints": endpoints,
        "services": len(services),
        "task_runners": len(task_runners),
        "models": len(models),
        "migrations": len(migrations),
        "backend_source_lines": sum(i.lines for i in backend_src),
        "backend_source_files": len(backend_src),
        "views": len(views),
        "components": len(components),
        "composables": len(composables),
        "utils": len(utils),
        "api_modules": len(api_modules),
        "type_modules": len(type_modules),
        "stores": len(stores),
        "largest_view": (
            {"path": largest_view.rel, "lines": largest_view.lines} if largest_view else None
        ),
        "docs_root": {
            name: (item.lines if item else 0) for name, item in docs_root.items()
        },
        "docs_dir": {"files": len(docs_dir), "lines": sum(i.lines for i in docs_dir)},
    }


# ─────────────────────────── 质量指标 ───────────────────────────


def _quality(by_path: dict[str, SourceFile], stats: Stats) -> dict[str, object]:
    """质量指标：测试文件/用例数（静态近似；--deep 时由调用方覆盖）、覆盖率、门禁项数。"""
    backend_test_files = [
        i
        for rel, i in by_path.items()
        if classify(rel) == "backend_tests" and rel.endswith(".py")
    ]
    frontend_test_files = [
        i
        for rel, i in by_path.items()
        if classify(rel) == "frontend_tests"
    ]
    backend_static = sum(
        len(re.findall(r"^\s*def (test_\w+)\s*\(", item.text, re.M))
        for item in backend_test_files
    )
    frontend_static = sum(
        len(re.findall(r"^\s*(?:it|test)(?:\.\w+)?\s*\(", item.text, re.M))
        for item in frontend_test_files
    )
    mobile_test_files = [
        i
        for rel, i in by_path.items()
        if classify(rel) == "mobile" and ("__tests__" in rel or ".test." in rel)
    ]
    mobile_static = sum(
        len(re.findall(r"^\s*(?:it|test)(?:\.\w+)?\s*\(", item.text, re.M))
        for item in mobile_test_files
    )

    source_lines = sum(
        stats.buckets.get(key, {}).get("lines", 0) for key in SOURCE_BUCKETS_FOR_RATIO
    )
    test_lines = sum(
        stats.buckets.get(key, {}).get("lines", 0)
        for key in ("backend_tests", "frontend_tests")
    )
    test_lines += sum(i.lines for i in mobile_test_files)
    coverage, coverage_note = _read_coverage()
    return {
        "backend_test_files": len(backend_test_files),
        "backend_tests_static": backend_static,
        "backend_tests": None,
        "frontend_test_files": len(frontend_test_files),
        "frontend_tests_static": frontend_static,
        "frontend_tests": None,
        "mobile_test_files": len(mobile_test_files),
        "mobile_tests_static": mobile_static,
        "mobile_test_lines": sum(i.lines for i in mobile_test_files),
        "coverage": coverage,
        "coverage_note": coverage_note,
        "source_lines": source_lines,
        "test_lines": test_lines,
        "test_ratio": (test_lines / source_lines) if source_lines else 0.0,
        "gate_items": _gate_items(),
    }


def _read_coverage() -> tuple[float | None, str]:
    """从 backend/.coverage 读最近一次全量覆盖率（不重跑测试，秒回）。

    返回 (百分比, 说明)；没有数据文件或没装 coverage 时返回 (None, 原因)。
    """
    data_file = ROOT / "backend" / ".coverage"
    if not data_file.exists():
        return None, "未找到 backend/.coverage（跑一次全量 pytest 后会生成）"
    try:
        result = subprocess.run(
            [sys.executable, "-m", "coverage", "report", f"--data-file={data_file}"],
            capture_output=True,
            cwd=str(ROOT),
            check=False,
        )
    except OSError as exc:  # coverage 未安装 / python 不可用
        return None, f"读取失败：{exc}"
    text = (result.stdout or b"").decode("utf-8", "replace")
    match = re.search(r"^TOTAL\s+(\d+)\s+(\d+)\s+(\d+)%", text, re.M)
    if not match:
        return None, "coverage 数据文件不可读（可能不完整，重新跑一次 pytest --cov）"
    statements, missed, percent = int(match.group(1)), int(match.group(2)), int(match.group(3))
    return (
        float(percent),
        f"{statements:,} 条语句 / 漏 {missed:,}；来自 backend/.coverage（最近一次全量运行）",
    )


def _gate_items() -> int:
    """门禁项数：数 scripts/test.sh 里的 ``===== N/M ...`` 标记（自动跟随脚本）."""
    script = ROOT / "scripts" / "test.sh"
    try:
        text = script.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return len(re.findall(r"^echo \"===== \d+/\d+", text, re.M))


def deep_test_counts(quality: dict[str, object]) -> None:
    """精确测试数：pytest 收集 + vitest list（失败则保留静态近似并注明）。"""
    backend_dir = ROOT / "backend"
    quality["backend_tests"] = _pytest_collect_count(backend_dir)
    quality["frontend_tests"] = _vitest_list_count(ROOT / "web")


def _pytest_collect_count(cwd: Path) -> int | None:
    """跑 pytest --collect-only，按「文件: N」求和（本仓库 pytest.ini 不打印汇总行）。"""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q"],
            capture_output=True,
            cwd=str(cwd),
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    # Windows 子进程输出是 CRLF：不先归一化的话 `^tests/xxx.py: N$` 的 $ 会撞上 \r，
    # 全部匹配失败（踩过：--deep 后端测试数静默退化成静态近似）
    text = (result.stdout or b"").decode("utf-8", "replace").replace("\r\n", "\n")
    per_file = re.findall(r"^tests/\S+\.py: (\d+)$", text, re.M)
    if per_file:
        return sum(int(n) for n in per_file)
    summary = re.search(r"(\d+) tests? collected", text)
    return int(summary.group(1)) if summary else None


def _vitest_list_count(cwd: Path) -> int | None:
    """跑 vitest list 数用例（只收集不执行；需要 node_modules 已安装）。"""
    if not (cwd / "node_modules" / "vitest").exists():
        return None
    npx = "npx.cmd" if sys.platform == "win32" else "npx"
    try:
        result = subprocess.run(
            [npx, "vitest", "list"],
            capture_output=True,
            cwd=str(cwd),
            timeout=300,
            check=False,
            shell=(sys.platform == "win32"),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (result.stdout or b"").decode("utf-8", "replace")
    return len([line for line in text.splitlines() if line.strip()])


# ─────────────────────────── 真实库数据量 ───────────────────────────


def read_database(db_path: Path) -> list[dict[str, object]]:
    """只读打开 SQLite，统计各表行数（缺失表/文件自动跳过，不报错）。

    约定：本仓库的脚本规范要求只读连接必须带 ``mode=ro``（见 scripts/check_scripts.py），
    这里显式用 URI 打开，绝不写库。
    """
    if not db_path.exists():
        return [{"name": "(未找到数据库)", "label": str(db_path), "rows": None}]
    uri = f"file:{db_path.as_posix()}?mode=ro"
    rows: list[dict[str, object]] = []
    try:
        # 只读连接：URI 已带 mode=ro（只读打开，绝不写库）
        conn = sqlite3.connect(uri, uri=True)  # mode=ro 已在 uri 里
    except sqlite3.Error as exc:
        return [{"name": "(数据库打不开)", "label": str(exc), "rows": None}]
    try:
        for table, label in DB_TABLES:
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.Error:
                continue
            extra = ""
            if table == "inspirations":
                alive = conn.execute(
                    "SELECT COUNT(*) FROM inspirations WHERE deleted_at IS NULL"
                ).fetchone()[0]
                extra = f"（未删 {alive:,}）"
            rows.append({"name": table, "label": label, "rows": count, "extra": extra})
    finally:
        conn.close()
    return rows


# ─────────────────────────── git 信息 ───────────────────────────


def _git(args: list[str]) -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), *args], capture_output=True, check=False
    )
    if result.returncode != 0:
        return ""
    return result.stdout.decode("utf-8", "replace").strip()


def _git_info(ref: str) -> dict[str, object]:
    """提交数、时间跨度、近 7 天提交数（用 --ref 时给该提交的短 hash）。"""
    short = _git(["rev-parse", "--short", ref])
    total = _git(["rev-list", "--count", ref])
    root_commit = _git(["rev-list", "--max-parents=0", ref]).splitlines()
    first = _git(["log", "-1", "--format=%cs", root_commit[0]]) if root_commit else ""
    last = _git(["log", "-1", "--format=%cs", ref])
    last7 = _git(["rev-list", "--count", "--since=7.days", ref])
    days = 0
    if first and last:
        from datetime import date

        try:
            days = (date.fromisoformat(last) - date.fromisoformat(first)).days + 1
        except ValueError:
            days = 0
    commits = int(total) if total.isdigit() else 0
    return {
        "short": short or "?",
        "commits": commits,
        "first_date": first,
        "last_date": last,
        "days": days,
        "per_day": (commits / days) if days else 0.0,
        "last_7_days": int(last7) if last7.isdigit() else 0,
    }


# ─────────────────────────── 输出 ───────────────────────────


def print_report(stats: Stats, *, deep: bool) -> None:
    """把报告打印成人读的格式：五节 + 对齐表格（中文按显示宽度对齐）。"""
    git = stats.git
    buckets = stats.buckets
    structure = stats.structure
    quality = stats.quality

    print()
    print("=" * 78)
    print(" 项目代码量体检报告")
    print("=" * 78)
    if stats.scope == "ref":
        scope = f"提交 {stats.ref}"
    elif stats.scope == "worktree-tracked":
        scope = "工作区（仅 git 可见文件）"
    else:
        scope = "工作区（全量扫描，含 .gitignore 的文档）"
    print(
        f" 统计范围：{scope}     HEAD：{git.get('short')}"
        f"    生成时间：{_now_text()}"
    )
    print(
        " 已排除：模型权重、日志、node_modules、构建产物、存储/备份、媒体与数据库文件、锁文件"
    )

    print("\n一、总览")
    backend = buckets.get("backend_src", {})
    frontend = buckets.get("frontend_src", {})
    backend_tests = buckets.get("backend_tests", {})
    frontend_tests = buckets.get("frontend_tests", {})
    mobile_test_lines = int(quality.get("mobile_test_lines") or 0)
    total_tests_lines = (
        backend_tests.get("lines", 0) + frontend_tests.get("lines", 0) + mobile_test_lines
    )
    print(f" 代码与文档总行数      {stats.total_lines:,} 行、{stats.total_files:,} 个文件")
    print(
        f" 后端源码              {backend.get('lines', 0):,} 行（{backend.get('files', 0)} 文件）"
    )
    print(
        f" 前端源码              {frontend.get('lines', 0):,} 行（{frontend.get('files', 0)} 文件）"
    )
    print(
        f" 测试代码              {total_tests_lines:,} 行"
        f"（后端 {backend_tests.get('lines', 0):,} + 前端 {frontend_tests.get('lines', 0):,}"
        f" + 移动端 {mobile_test_lines:,}）"
    )
    print(
        f" 提交                  {git.get('commits', 0):,} 次"
        f"（{git.get('first_date')} → {git.get('last_date')}，"
        f"约 {git.get('days')} 天 ≈ {git.get('per_day'):.0f} 次/天；"
        f"近 7 天 {git.get('last_7_days')} 次）"
    )

    print("\n二、代码量分布")
    total = stats.total_lines or 1
    print(f" {'归类':<20}{'文件数':>7}{'行数':>12}{'占比':>8}")
    for key in BUCKET_ORDER:
        item = buckets.get(key, {"files": 0, "lines": 0})
        label = BUCKET_LABELS.get(key, key)
        print(
            f" {_pad(label, 20)}{item['files']:>7}{item['lines']:>12,}"
            f"{item['lines'] / total * 100:>7.1f}%"
        )

    print("\n 按语言：")
    langs = sorted(
        stats.languages.items(), key=lambda kv: kv[1]["lines"], reverse=True
    )
    lang_text = "、".join(
        f"{label} {value['lines']:,} 行（{value['lines'] / total * 100:.0f}%）"
        for label, value in langs[:6]
    )
    print(f" {lang_text}")

    print("\n三、结构指标")
    largest = structure.get("largest_view") or {}
    print(" 后端")
    print(
        f"  {structure.get('routers')} 个路由文件、{structure.get('endpoints')} 个 API 端点、"
        f"{structure.get('services')} 个服务文件（其中任务执行器 {structure.get('task_runners')} 个）、"
        f"{structure.get('models')} 个模型模块、{structure.get('migrations')} 个迁移"
    )
    backend_files = structure.get("backend_source_files") or 1
    backend_lines = structure.get("backend_source_lines") or 0
    print(f"  平均 {backend_lines / backend_files:.0f} 行/文件（源码，按 .py 计）")
    print(" 前端")
    print(
        f"  {structure.get('views')} 个视图、{structure.get('components')} 个组件、"
        f"{structure.get('composables')} 个 composables、{structure.get('utils')} 个 utils、"
        f"{structure.get('api_modules')} 个 api 模块、{structure.get('type_modules')} 个类型文件、"
        f"{structure.get('stores')} 个 store"
    )
    if largest:
        print(
            f"  最大视图 {largest.get('lines')} 行（{Path(str(largest.get('path'))).name}），"
            f"拆分阈值 800 行"
        )
    docs_root = structure.get("docs_root") or {}
    docs_dir = structure.get("docs_dir") or {}
    print(
        f" 文档：README.md {docs_root.get('README.md', 0):,} 行、"
        f"CLAUDE.md {docs_root.get('CLAUDE.md', 0):,} 行、"
        f"TODO.md {docs_root.get('TODO.md', 0):,} 行 + docs/ "
        f"{docs_dir.get('files', 0)} 篇（{docs_dir.get('lines', 0):,} 行）"
    )

    print("\n四、质量指标")
    backend_tests = int(
        quality.get("backend_tests") or quality.get("backend_tests_static") or 0
    )
    frontend_tests = int(
        quality.get("frontend_tests") or quality.get("frontend_tests_static") or 0
    )
    count_note = "" if deep else "，静态近似"
    print(
        f" 后端测试            {backend_tests:,} 条{count_note}"
        f"（{quality.get('backend_test_files')} 个测试文件）"
    )
    print(
        f" 前端测试            {frontend_tests:,} 条{count_note}"
        f"（{quality.get('frontend_test_files')} 个测试文件）"
    )
    coverage = quality.get("coverage")
    if coverage is not None:
        print(f" 后端覆盖率          {coverage:.2f}% —— {quality.get('coverage_note')}")
    else:
        print(f" 后端覆盖率          未知（{quality.get('coverage_note')}）")
    print(
        f" 测试/源码 比        {quality.get('test_ratio', 0) * 100:.0f}%"
        f"（{quality.get('test_lines', 0):,} / {quality.get('source_lines', 0):,}）"
    )
    print(f" 门禁                {quality.get('gate_items')} 项（scripts/test.sh）")

    if stats.database:
        print("\n五、代码承载的数据量（真实库，只读）")
        print(f" {'表':<22}{'行数':>10}")
        for row in stats.database:
            rows = row.get("rows")
            text = f"{rows:,}" if isinstance(rows, int) else "—"
            print(f" {_pad(str(row.get('label')), 22)}{text:>10}{row.get('extra', '')}")

    print()
    print("=" * 78)
    if not deep:
        print(" 提示：加 --deep 可跑 pytest 收集 / vitest list 得到精确测试数（约 40 秒）")
        print("=" * 78)
    print()

def _pad(text: str, width: int) -> str:
    """按显示宽度左对齐（中文/全角字符占 2 列，直接 str.ljust 会错位）。"""
    display = 0
    for char in text:
        display += 2 if _is_wide(char) else 1
    return text + " " * max(0, width - display)


def _is_wide(char: str) -> bool:
    """判断字符是否全角/宽字符（CJK、全角标点等）。"""
    code = ord(char)
    return (
        0x1100 <= code <= 0x115F
        or 0x2E80 <= code <= 0xA4CF
        or 0xAC00 <= code <= 0xD7A3
        or 0xF900 <= code <= 0xFAFF
        or 0xFE30 <= code <= 0xFE6F
        or 0xFF00 <= code <= 0xFF60
        or 0xFFE0 <= code <= 0xFFE6
    )


def _now_text() -> str:
    """当前本地时间（报告头用，不参与统计）。"""
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M")


def to_json(stats: Stats, *, deep: bool) -> dict[str, object]:
    """把结果整理成 JSON（字段名与报告一致，便于写进趋势对比脚本）。"""
    return {
        "scope": stats.scope,
        "ref": stats.ref,
        "git": stats.git,
        "total": {"files": stats.total_files, "lines": stats.total_lines},
        "buckets": stats.buckets,
        "languages": stats.languages,
        "structure": stats.structure,
        "quality": stats.quality,
        "database": stats.database,
        "deep": deep,
    }


# ─────────────────────────── 入口 ───────────────────────────


def main(argv: list[str] | None = None) -> int:
    """命令行入口：解析参数 → 统计 → 打印（可选写 JSON）。"""
    parser = argparse.ArgumentParser(
        description="统计项目代码量：总览 / 分布 / 结构 / 质量 / 数据量（只读）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--ref",
        default=None,
        metavar="COMMIT",
        help="统计指定提交（git archive 内存读取，可复现）；默认统计工作区",
    )
    parser.add_argument(
        "--deep",
        action="store_true",
        help="跑 pytest 收集与 vitest list 取精确测试数（约 40 秒；失败自动回退静态计数）",
    )
    parser.add_argument(
        "--json",
        dest="json_path",
        default=None,
        metavar="PATH",
        help="同时把结果写成 JSON（便于逐月对比）",
    )
    parser.add_argument(
        "--no-db", action="store_true", help="跳过「代码承载的数据量」一节（不连数据库）"
    )
    parser.add_argument(
        "--tracked-only",
        action="store_true",
        help="只统计 git 可见文件（跟踪 + 未忽略的新文件）；默认连 .gitignore 的文档一起算",
    )
    args = parser.parse_args(argv)

    if args.ref:
        store = GitRefStore(args.ref)
        stats = build_stats(store, "ref", args.ref)
    else:
        store = WorktreeStore(ROOT, tracked_only=args.tracked_only)
        scope = "worktree-tracked" if args.tracked_only else "worktree"
        stats = build_stats(store, scope, "HEAD")

    if args.deep:
        if args.ref:
            # --ref 是「读某次提交的文件」；测试计数只能跑当前工作区的用例，
            # 两者口径不同，明确提示以免把工作区数字误当成那次提交的数字
            print(
                " 注意：--deep 的测试数来自**当前工作区**（--ref 只影响代码行数口径）",
                file=sys.stderr,
            )
        deep_test_counts(stats.quality)

    if not args.no_db:
        stats.database = read_database(ROOT / "backend" / "fashion_inspo.db")

    print_report(stats, deep=args.deep)

    if args.json_path:
        out = Path(args.json_path)
        out.write_text(
            json.dumps(to_json(stats, deep=args.deep), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f" 已写入 JSON：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
