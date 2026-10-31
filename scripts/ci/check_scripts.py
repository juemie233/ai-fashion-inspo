"""脚本目录的静态守卫：只读连接 + 统一 CLI 入口。

为什么需要它（对应 2026-10-28 的 scripts 审查结论）：

1. **外部库必须只读**：f2 的 `douyin_users.db` / `douyin_videos.db` 是外部工具在写，
   脚本若以读写方式打开，两边会抢锁；一旦逻辑写错还会改坏对方的数据。这类错误
   **不会在测试里暴露**（脚本不计覆盖率），只能靠静态规则兜住。
2. **入口统一 argparse**：手写 `sys.argv[1]` 没有 `--help`、没有类型校验，参数漏传
   时的报错也各写各的。统一后每一处入口都能 `--help` 自查。
3. **注释里不许出现 `def`**：`# ====def foo():` 这种「压行」会让函数被注释吞掉，
   函数体还会被吸进上一个函数——模块照样 import、测试照样过，只有 ruff 的 F821 能发现
   （2026-10-28 在 `fetch_xhs_following.py` 真撞到两处）。

用法：
    python scripts/ci/check_scripts.py          # 检查 scripts/ 与 backend/scripts/
    python scripts/ci/check_scripts.py --fix-hint

退出码：发现问题返回 1（便于接进 CI），全部通过返回 0。

豁免：确实需要读写外部库或在入口里手工取参数的场景，在那一行加注释
``# noqa: scripts-check``，本工具会跳过。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# 只检查「仍在维护」的目录；scripts/archive 是已结项脚本，不参与门禁
SCAN_DIRS = (
    ROOT / "scripts" / "ops",
    ROOT / "scripts" / "ci",
    ROOT / "scripts" / "data",
    ROOT / "scripts" / "sync",
    ROOT / "scripts" / "oneoff",
    ROOT / "backend" / "scripts",
)
BAT_DIRS = (
    ROOT / "scripts" / "ops",
    ROOT / "scripts" / "ci",
    ROOT / "scripts" / "sync",
    ROOT / "scripts" / "oneoff",
)
PS1_DIRS = (ROOT / "scripts" / "ci",)

CONNECT_RE = re.compile(r"(?:_?sqlite3)\.connect\(\s*([^)]*)\)")

# `if __name__ == "__main__":` 之后到文件结尾视为入口块
MAIN_BLOCK_RE = re.compile(r"^if __name__ == [\"']__main__[\"']:\s*$", re.M)
ARGV_INDEX_RE = re.compile(r"sys\.argv\s*\[")
ARGV_SLICE_RE = re.compile(r"sys\.argv\s*\[\s*1\s*:\s*\]")

NOQA = "noqa: scripts-check"


def _iter_scripts() -> list[Path]:
    """列出所有待检脚本（递归；跳过 __init__、__pycache__ 与自身）。"""
    files: list[Path] = []
    for d in SCAN_DIRS:
        if not d.is_dir():
            continue
        files += [
            p
            for p in sorted(d.rglob("*.py"))
            if p.name not in ("__init__.py", Path(__file__).name)
            and "__pycache__" not in p.parts
        ]
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


COMMENTED_DEF_RE = re.compile(r"^\s*#.*\bdef\s+[A-Za-z_][A-Za-z0-9_]*\s*\(")


def check_defs_not_swallowed_by_comment(path: Path) -> list[str]:
    """注释行里出现 ``def ...(`` → 可能是「压行把定义吃掉了」的静默损坏。

    真实事故（2026-10-28）：`fetch_xhs_following.py` 里两次把

        # ============
        def _probe_cdp(...):

    压成 ``# ============def _probe_cdp(...):``。注释不改变缩进状态，于是函数体被吸进
    上一个函数：函数不存在、上一个函数末尾多出一句 `return _check_cdp(port)`，
    一调用就 NameError；而模块能 import、单测能过，只有 ruff 的 F821 能发现。
    """
    problems: list[str] = []
    text = path.read_text(encoding="utf-8", errors="ignore")
    for lineno, line in enumerate(text.splitlines(), start=1):
        if COMMENTED_DEF_RE.match(line) and NOQA not in line:
            problems.append(
                f"{_rel(path)}:{lineno} 注释行里出现了 def 定义：多半是「注释 + 空行 + def」"
                "被压成一行，函数会被当注释吞掉；请把 def 拆回独立行"
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


def check_bat_encoding(path: Path) -> list[str]:
    """批处理含中文时必须是 GBK 且显式 ``chcp 936``。

    2026-10-31 实测：GBK 字节在非 936 代码页下会被误解析，甚至把尾字节当成
    ``&`` 拆命令。纯 ASCII 的 .bat 不受影响。
    """
    data = path.read_bytes()
    if all(byte < 128 for byte in data):
        return []
    try:
        text = data.decode("gbk")
    except UnicodeDecodeError:
        return [f"{_rel(path)} 含非 ASCII 但不是合法 GBK；请存为 GBK 或改成纯 ASCII"]
    head = "\n".join(text.splitlines()[:12]).lower()
    if "chcp 936" not in head:
        return [
            f"{_rel(path)} 含 GBK 中文但前 12 行没有 `chcp 936`；"
            "在非 936 控制台可能被错误拆命令"
        ]
    return []


def check_ps1_bom(path: Path) -> list[str]:
    """含中文的 .ps1 必须带 UTF-8 BOM（Windows PowerShell 5.1 按 ANSI 读无 BOM 文件）。"""
    data = path.read_bytes()
    if all(byte < 128 for byte in data):
        return []
    if not data.startswith(b"\xef\xbb\xbf"):
        return [f"{_rel(path)} 含中文但缺少 UTF-8 BOM；PowerShell 5.1 会按 ANSI 解析"]
    return []


def _iter_files(dirs: tuple[Path, ...], pattern: str) -> list[Path]:
    files: list[Path] = []
    for d in dirs:
        if d.is_dir():
            files += sorted(d.rglob(pattern))
    return files


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
        problems += check_defs_not_swallowed_by_comment(path)
    for path in _iter_files(BAT_DIRS, "*.bat"):
        problems += check_bat_encoding(path)
    for path in _iter_files(PS1_DIRS, "*.ps1"):
        problems += check_ps1_bom(path)

    if not problems:
        print(f"[ok] 脚本规范检查通过（{len(scripts)} 个 Python 脚本 + bat/ps1 编码）")
        return 0

    print(f"[fail] 发现 {len(problems)} 处问题：\n")
    for item in problems:
        print("  - " + item)
    print("\n说明：确实需要例外时，在对应行加注释 `# noqa: scripts-check`。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
