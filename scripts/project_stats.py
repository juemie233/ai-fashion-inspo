"""项目体量统计：代码行数 + 数据/存储/仓库体积 + 测试数 + 最大源文件。

用法（仓库根或任意目录均可）::

    python scripts/project_stats.py                 # 打印完整报告
    python scripts/project_stats.py --json out.json # 另存为 JSON（供趋势对比）
    python scripts/project_stats.py --no-largest    # 跳过「最大源文件」扫描（更快）

为什么单独写一个（而不是继续用 ``count_lines.ps1``）：那个脚本只有行数，而且分节口径
写死在 PowerShell 里、Windows 之外跑不了。排查「项目到底多大」时真正要一起看的是四件事
——**代码量、数据量（DB/存储）、仓库体积（提交数与跟踪文件）、测试与最大文件**，本脚本
一次全给，并且可 ``--json`` 落盘做逐月趋势。行数口径与 ``count_lines.ps1`` 保持一致
（按扩展名分块、跳过 node_modules/缓存/存储目录）。

只读：只统计不修改任何文件、不连数据库以外的服务。
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

"""参与统计的源码扩展名（与 count_lines.ps1 的第 1~6 节一致）。"""
SOURCE_EXTS = (".py", ".ts", ".tsx", ".vue", ".css", ".js", ".sh", ".bat")

"""统计时需要跳过的目录名（依赖/缓存/产物/数据）。"""
SKIP_DIRS = {
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "site-packages",
    "dist",
    "build",
    ".git",
    "storage",
    "backups",
    ".claude",
    ".expert-mode",
}

"""锁文件不参与行数统计（自动生成，行数无信息量）。"""
SKIP_FILES = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock"}

"""代码分块口径：标签 → 相对路径。与 README「项目结构」的分层对应。"""
CODE_AREAS = [
    ("后端业务代码 backend/app", "backend/app"),
    ("后端测试 backend/tests", "backend/tests"),
    ("后端脚本 backend/scripts", "backend/scripts"),
    ("数据库迁移 backend/alembic", "backend/alembic"),
    ("Web 页面 web/src/views", "web/src/views"),
    ("Web 组件 web/src/components", "web/src/components"),
    ("Web composables", "web/src/composables"),
    ("Web utils", "web/src/utils"),
    ("Web api/stores/types", "web/src/api"),
    ("移动端 mobile", "mobile"),
    ("浏览器插件 browser-extension", "browser-extension"),
    ("共享类型 shared", "shared"),
]

"""存储子目录（相对 backend/storage），用于体量报告。"""
STORAGE_PARTS = [
    "images",
    "thumbnails",
    "videos",
    "trash",
    "avatars",
    "lancedb",
    "person_photos",
    "person_thumbnails",
    "_crop_backup",
    "_crop_dups",
    "import_batches",
]

"""数据库里值得报行数的表（表名 → 中文说明）。缺失的表跳过，不报错。"""
DB_TABLES = {
    "inspirations": "素材",
    "tags": "标签",
    "inspiration_tags": "素材-标签关联",
    "bloggers": "穿搭博主",
    "models": "职业模特",
    "inspiration_bloggers": "素材-博主关联",
    "blogger_face_embeddings": "博主人脸特征",
    "inspiration_face_detections": "素材人脸检测",
    "task_queue": "任务队列",
    "audit_logs": "审计日志",
    "scraper_tasks": "采集任务",
    "scraper_seen_urls": "URL 墓碑",
    "ai_analysis_log": "AI 分析日志",
    "collections": "收藏合集",
}


def human_bytes(n: float) -> str:
    """字节 → 人类可读（KB/MB/GB，保留 1 位小数）。"""
    for unit, scale in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= scale:
            return f"{n / scale:.1f} {unit}"
    return f"{int(n)} B"


def iter_files(base: Path, exts: tuple[str, ...]):
    """递归列出 base 下指定扩展名的文件（跳过依赖/缓存/数据目录）。"""
    if not base.exists():
        return
    for path in base.rglob("*"):
        if not path.is_file() or path.suffix not in exts or path.name in SKIP_FILES:
            continue
        if SKIP_DIRS & set(path.parts):
            continue
        yield path


def count_lines(files) -> tuple[int, int]:
    """返回 (文件数, 行数)；读不了的文件按 0 行计（不因个别文件中断统计）。"""
    files = list(files)
    lines = 0
    for path in files:
        try:
            lines += len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
        except OSError:
            continue
    return len(files), lines


def dir_size(base: Path) -> int:
    """目录体积（字节）；不存在返回 0。"""
    if not base.exists():
        return 0
    return sum(f.stat().st_size for f in base.rglob("*") if f.is_file())


def git_info(root: Path) -> dict:
    """仓库体量：提交数 / 跟踪文件数 / 时间跨度 / .git 体积。非仓库返回空。"""

    def run(*args: str) -> str:
        try:
            return subprocess.run(
                ["git", *args],
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    if not run("rev-parse", "--is-inside-work-tree"):
        return {}
    dates = run("log", "--reverse", "--format=%ad", "--date=short").splitlines()
    return {
        "commits": int(run("rev-list", "--count", "HEAD") or 0),
        "tracked_files": len(run("ls-files").splitlines()),
        "first_commit": dates[0] if dates else "",
        "last_commit": dates[-1] if dates else "",
        "git_dir_size": dir_size(root / ".git"),
    }


def db_stats(db_path: Path) -> dict:
    """数据库体量与关键表行数；库不存在时返回空。"""
    if not db_path.exists():
        return {}
    stats: dict = {"db_size": db_path.stat().st_size, "tables": {}, "counts": {}}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        names = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        stats["counts"]["tables"] = len(names)
        stats["counts"]["indexes"] = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
        for table, label in DB_TABLES.items():
            if table not in names:
                continue
            stats["tables"][label] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        # 几个「欠账」口径：未打标 / 未缓存哈希 / 垃圾桶，一眼看出还有多少活没干
        stats["counts"]["alive_materials"] = conn.execute(
            "SELECT COUNT(*) FROM inspirations WHERE deleted_at IS NULL"
        ).fetchone()[0]
        stats["counts"]["trash_materials"] = conn.execute(
            "SELECT COUNT(*) FROM inspirations WHERE deleted_at IS NOT NULL"
        ).fetchone()[0]
        stats["counts"]["untagged"] = conn.execute(
            """SELECT COUNT(*) FROM inspirations i WHERE i.deleted_at IS NULL
               AND NOT EXISTS (SELECT 1 FROM inspiration_tags t
                               WHERE t.inspiration_id = i.id)"""
        ).fetchone()[0]
        stats["counts"]["no_phash"] = conn.execute(
            """SELECT COUNT(*) FROM inspirations
               WHERE deleted_at IS NULL AND media_type = 'image'
                 AND (phash IS NULL OR phash = '')"""
        ).fetchone()[0]
    finally:
        conn.close()
    return stats


def test_stats(root: Path) -> dict:
    """测试规模：后端用例数（pytest --collect-only -q -n0，不执行测试）与前端测试文件数。

    ``-q --collect-only`` 的输出是**逐文件计数**（``tests/xx.py: 10``），本函数把它们
    求和；某些版本末行还有 ``N tests collected``，两种格式都认。pytest 不可用时留空
    （只影响这一项，不影响其余统计）。
    """
    backend = root / "backend"
    cases = None
    if (backend / "tests").exists():
        # 优先用当前解释器（跑本脚本的那个，通常已装 pytest）；再退到 PATH 里的 python
        for exe in (sys.executable, "python"):
            if not exe:
                continue
            try:
                out = subprocess.run(
                    [exe, "-m", "pytest", "--collect-only", "-q", "--no-header", "-n0"],
                    cwd=backend,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=300,
                    check=False,
                ).stdout
            except (OSError, subprocess.SubprocessError):
                continue
            per_file = re.findall(r"^\S+\.py:\s+(\d+)$", out, flags=re.MULTILINE)
            if per_file:
                cases = sum(int(n) for n in per_file)
            else:
                for line in reversed(out.splitlines()):
                    if "tests collected" in line:
                        try:
                            cases = int(line.split()[0])
                        except ValueError:
                            cases = None
                        break
            if cases is not None:
                break
    front = len(list((root / "web/src").rglob("*.test.ts"))) if (root / "web/src").exists() else 0
    return {
        "backend_test_files": len(list((backend / "tests").glob("test_*.py"))),
        "backend_cases": cases,
        "web_test_files": front,
    }


def largest_sources(root: Path, top: int = 12) -> list[dict]:
    """最大的源文件（按行数，已跳过依赖/数据目录）。"""
    rows: list[tuple[int, str]] = []
    for path in iter_files(root, (".py", ".ts", ".tsx", ".vue", ".css", ".js")):
        try:
            n = len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
        except OSError:
            continue
        rows.append((n, str(path.relative_to(root)).replace("\\", "/")))
    rows.sort(reverse=True)
    return [{"lines": n, "path": p} for n, p in rows[:top]]


def collect(root: Path) -> dict:
    """汇总所有统计口径（纯只读）。"""
    code: dict = {}
    total_files = total_lines = 0
    for label, rel in CODE_AREAS:
        files, lines = count_lines(iter_files(root / rel, SOURCE_EXTS))
        if files:
            code[label] = {"files": files, "lines": lines}
            total_files += files
            total_lines += lines
    return {
        "root": str(root),
        "code": code,
        "code_total": {"files": total_files, "lines": total_lines},
        "db": db_stats(root / "backend/fashion_inspo.db"),
        "storage": {
            part: dir_size(root / "backend/storage" / part) for part in STORAGE_PARTS
        },
        "storage_total": dir_size(root / "backend/storage"),
        "git": git_info(root),
        "tests": test_stats(root),
    }


def print_report(data: dict, top: int = 12, show_largest: bool = True) -> None:
    """打印中文报告。"""
    print("\n=== 项目体量 ===")
    print("\n[代码行数]（按扩展名，跳过依赖/缓存/数据目录）")
    for label, stat in data["code"].items():
        print(f"  {label:<34} {stat['files']:>4} 文件 {stat['lines']:>8} 行")
    print(f"  {'合计':<34} {data['code_total']['files']:>4} 文件 "
          f"{data['code_total']['lines']:>8} 行")

    db = data.get("db") or {}
    if db:
        print(f"\n[数据库] {human_bytes(db['db_size'])}"
              f"（表 {db['counts'].get('tables', 0)} 张 / 索引 {db['counts'].get('indexes', 0)} 个）")
        for label, n in db["tables"].items():
            print(f"  {label:<20} {n:>9}")
        c = db["counts"]
        print(f"  在库素材 {c.get('alive_materials', 0)}｜垃圾桶 {c.get('trash_materials', 0)}"
              f"｜未打标 {c.get('untagged', 0)}｜无 phash {c.get('no_phash', 0)}")

    print(f"\n[存储目录] 合计 {human_bytes(data['storage_total'])}")
    for part, size in sorted(data["storage"].items(), key=lambda kv: -kv[1]):
        if size:
            print(f"  {part:<20} {human_bytes(size):>12}")

    git = data.get("git") or {}
    if git:
        print(f"\n[Git 仓库] {git['commits']} 次提交｜跟踪文件 {git['tracked_files']} 个"
              f"｜.git {human_bytes(git['git_dir_size'])}")
        print(f"  时间跨度：{git['first_commit']} → {git['last_commit']}")

    tests = data.get("tests") or {}
    if tests:
        cases = tests.get("backend_cases")
        print(f"\n[测试] 后端 {tests['backend_test_files']} 个文件"
              f"（{cases if cases is not None else '用例数未采集'} 项）"
              f"｜前端 {tests['web_test_files']} 个文件")

    if show_largest:
        print(f"\n[最大源文件 Top {top}]")
        for row in largest_sources(Path(data["root"]), top):
            print(f"  {row['lines']:>5} 行  {row['path']}")
    print()


def main() -> int:
    """入口：解析参数 → 统计 → 打印（可选落盘 JSON）。"""
    parser = argparse.ArgumentParser(description="项目体量统计（只读）")
    parser.add_argument("--root", type=Path, default=None, help="仓库根目录（默认脚本上两级）")
    parser.add_argument("--json", type=Path, default=None, help="另存为 JSON（供趋势对比）")
    parser.add_argument("--top", type=int, default=12, help="「最大源文件」展示条数")
    parser.add_argument("--no-largest", action="store_true", help="跳过最大源文件扫描")
    args = parser.parse_args()

    root = (args.root or Path(__file__).resolve().parents[1]).resolve()
    data = collect(root)
    print_report(data, top=args.top, show_largest=not args.no_largest)
    if args.json:
        args.json.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"已写入 {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
