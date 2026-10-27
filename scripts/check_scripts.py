"""脚本目录的静态守卫：只读连接 + 统一 CLI 入口。

为什么需要它（对应 2026-10-28 的 scripts 审查结论）：

1. **外部库必须只读**：f2 的 `douyin_users.db` / `douyin_videos.db` 是外部工具在写，
   脚本若以读写方式打开，两边会抢锁；一旦逻辑写错还会改坏对方的数据。这类错误
   **不会在测试里暴露**（脚本不计覆盖率），只能靠静态规则兜住。
2. **入口统一 argparse**：手写 `sys.argv[1]` 没有 `--help`、没有类型校验，参数漏传
   时的报错也各写各的。统一后每一处入口都能 `--help` 自查。

用法：
    python scripts/check_scripts.py          # 检查 scripts/ 与 backend/scripts/
    python scripts/check_scripts.py --fix-hint

退出码：发现问题返回 1（便于接进 CI），全部通过返回 0。

豁免：确实需要读写外部库或在入口里手工取参数的场景，在那一行加注释
``# noqa: scripts-check``，本工具会跳过。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = (ROOT / "scripts", ROOT / "backend" / "scripts")

CONNECT_RE = re.compile(r"(?:_?sqlite3)\.connect\(\s*([^)]*)\)")

# `if __name__ == "__main__":` 之后到文件结尾视为入口块
MAIN_BLOCK_RE = re.compile(r"^if __name__ == [\"']__main__[\"']:\s*$", re.M)
ARGV_INDEX_RE = re.compile(r"sys\.argv\s*\[")
ARGV_SLICE_RE = re.compile(r"sys\.argv\s*\[\s*1\s*:\s*\]")

NOQA = "noqa: scripts-check"


def _iter_scripts() -> list[Path]:
    """列出所有待检脚本（跳过 __init__ 与自身）。"""
    files: list[Path] = []
    for d in SCAN_DIRS:
        files += [p for p in sorted(d.glob("*.py")) if p.name not in ("__init__.py", Path(__file__).name)]
    return files


def _line_of(text: str, index: int) -> int:
    return text.count(chr(10), 0, index) + 1


def _rel(path: Path) -> str:
    """仓库内文件显示相对路径；仓库外（测试用临时文件）退回绝对路径。"""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


# 函数体内出现这些关键字即认为是「会写库」的连接，不要求只读
WRITE_KEYWORDS = (
    "INSERT ",
    "UPDATE ",
    "DELETE ",
    "CREATE ",
    "ATTACH ",
    "executemany",
    "commit(",
    "isolation_level",
)
DEF_RE = re.compile(r"^(?:async )?def [A-Za-z_]", re.M)


def _enclosing_chunk(text: str, index: int) -> str:
    """取 index 所在的「函数体」近似片段（上一个 def 行 → 下一个 def 行）。"""
    defs = [m.start() for m in DEF_RE.finditer(text)]
    start = max([d for d in defs if d <= index], default=0)
    end = min([d for d in defs if d > index], default=len(text))
    return text[start:end]


def check_readonly_connections(path: Path) -> list[str]:
    """只读用途的 sqlite 连接必须带 mode=ro。

    判定（启发式，宁可漏报也不误报）：
    - 连接所在函数体里**没有任何写操作关键字** → 视为只读用途，必须 ``mode=ro``；
    - 带写操作的连接（素材入库、任务状态回写）不动，那是正常写库。

    这一条挡的是「分析/报表/校验脚本顺手以读写方式打开库」——它们本不需要写权限，
    但一旦哪天逻辑写错就会改坏数据；对 f2 的外部库还会与外部工具抢锁。
    """
    problems: list[str] = []
    text = path.read_text(encoding="utf-8", errors="ignore")
    for match in CONNECT_RE.finditer(text):
        # 用「调用附近 240 字符」判断 mode=ro：不能用正则抓括号内参数
        # （`f"file:{db.as_posix()}?mode=ro"` 里的 as_posix() 会在第一个 ) 处截断）
        window = text[match.start() : match.start() + 240]
        if "mode=ro" in window:
            continue
        line = _line_of(text, match.start())
        line_text = text.splitlines()[line - 1]
        if NOQA in line_text:
            continue
        chunk = _enclosing_chunk(text, match.start())
        if any(keyword in chunk for keyword in WRITE_KEYWORDS):
            continue
        problems.append(
            f"{_rel(path)}:{line} 只读用途却以读写方式打开数据库；"
            '改成 sqlite3.connect(f"file:{db}?mode=ro", uri=True)（确有写需求时加 '
            f"`# {NOQA}`）"
        )
    return problems


def check_cli_uses_argparse(path: Path) -> list[str]:
    """入口块里不应直接索引 sys.argv（必须 argparse 或传 argv 给可测函数）。"""
    problems: list[str] = []
    text = path.read_text(encoding="utf-8", errors="ignore")
    match = MAIN_BLOCK_RE.search(text)
    if not match:
        return problems
    tail = text[match.end() :]
    for hit in ARGV_INDEX_RE.finditer(tail):
        line = _line_of(text, match.end() + hit.start())
        line_text = text.splitlines()[line - 1]
        if NOQA in line_text:
            continue
        # `sys.argv[1:]` 作为「取全部参数转发给可测函数」的默认值是允许的
        if ARGV_SLICE_RE.search(line_text):
            continue
        problems.append(
            f"{_rel(path)}:{line} 入口块里直接索引 sys.argv；"
            "改用 argparse（白得 --help 与参数校验）"
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查脚本目录的只读连接与 CLI 入口规范")
    parser.add_argument(
        "--fix-hint",
        action="store_true",
        help="仅打印修复建议（当前与默认行为相同，保留给未来扩展）",
    )
    parser.parse_args(argv)  # 目前无行为差异，只是让 --help 可用

    problems: list[str] = []
    scripts = _iter_scripts()
    for path in scripts:
        problems += check_readonly_connections(path)
        problems += check_cli_uses_argparse(path)

    if not problems:
        print(f"[ok] 脚本规范检查通过（{len(scripts)} 个脚本）")
        return 0

    print(f"[fail] 发现 {len(problems)} 处问题：\n")
    for item in problems:
        print("  - " + item)
    print("\n说明：确实需要例外时，在对应行加注释 `# noqa: scripts-check`。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
