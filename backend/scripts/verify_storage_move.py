"""校验「素材存储根目录」迁移（只读：不写、不删、不移动任何文件）。

用途：把 `backend/storage` 搬到别的盘（`.env` 的 ``STORAGE_ROOT``）前后各跑一次，
回答两个问题：
  1. **切换后素材会不会 404**：库里每条记录的 ``file_path`` / ``thumbnail_path``
     在新根下是否都存在（含垃圾桶素材——它们的文件在 ``trash/`` 下）
  2. **复制是否完整**：与源目录逐子目录对比文件数/字节（``--source``）

用法：
    cd backend
    python -m scripts.verify_storage_move --root G:/fashion-inspo-storage
    python -m scripts.verify_storage_move --root G:/fashion-inspo-storage --source C:/.../backend/storage

退出码：0 = 全部通过；1 = 有缺失/不一致（控制台会列出前若干例）。
"""

import argparse
import sqlite3
import sys
from pathlib import Path

# 与 backend/scripts 下其它脚本一致：把 backend 加入 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

"""默认数据库位置（与 app.config._DB_PATH 同一处：backend/fashion_inspo.db）。"""
DEFAULT_DB = Path(__file__).resolve().parent.parent / "fashion_inspo.db"

"""最多打印多少条缺失样例。"""
SAMPLE_LIMIT = 10


def human(size: float) -> str:
    """字节数转可读字符串。"""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.2f}{unit}"
        value /= 1024.0
    return f"{value:.2f}TB"


def dir_stats(root: Path) -> tuple[int, int]:
    """递归统计目录下的文件数与总字节（读不了的项目跳过，不报错）。"""
    files = 0
    total = 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir():
                    stack.append(entry)
                elif entry.is_file():
                    files += 1
                    total += entry.stat().st_size
            except OSError:
                continue
    return files, total


def path_columns(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """找出库里所有以 ``_path`` 结尾的列（约定：这类列存的是存储根下的相对路径）。"""
    found: list[tuple[str, str]] = []
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    ]
    for table in tables:
        try:
            columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
        except sqlite3.Error:
            continue
        found.extend((table, column) for column in columns if column.endswith("_path"))
    return found


def check_db_references(
    db_path: Path, root: Path, limit: int | None = None
) -> tuple[int, int, list[str]]:
    """核对库里每条路径记录在 root 下是否存在。

    Args:
        db_path: 素材库 sqlite（**只读**打开，不会改动它）。
        root: 新的存储根目录。
        limit: 每列最多检查多少条（None = 全部；排查时可先用小值看样例）。

    Returns:
        ``(检查条数, 缺失条数, 缺失样例)``。
    """
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    checked = 0
    missing = 0
    samples: list[str] = []
    try:
        for table, column in path_columns(conn):
            try:
                rows = conn.execute(
                    f"SELECT {column} FROM {table} "
                    f"WHERE {column} IS NOT NULL AND {column} <> ''"
                ).fetchall()
            except sqlite3.Error:
                continue
            if limit:
                rows = rows[:limit]
            for (rel,) in rows:
                rel = str(rel or "")
                if not rel:
                    continue
                checked += 1
                if not (root / rel).exists():
                    missing += 1
                    if len(samples) < SAMPLE_LIMIT:
                        samples.append(f"{table}.{column}: {rel}")
    finally:
        conn.close()
    return checked, missing, samples


def compare_dirs(source: Path, target: Path) -> list[str]:
    """逐子目录对比源与目标的文件数/字节，返回不一致说明（空列表＝完全一致）。"""
    problems: list[str] = []
    try:
        names = sorted(p.name for p in source.iterdir() if p.is_dir())
    except OSError as exc:
        return [f"源目录读不了：{exc}"]

    src_files, src_bytes = dir_stats(source)
    dst_files, dst_bytes = dir_stats(target)
    print(f"  源   {str(source)[-44:]:>44} {src_files:>7} 文件 {human(src_bytes):>10}")
    print(f"  目标 {str(target)[-44:]:>44} {dst_files:>7} 文件 {human(dst_bytes):>10}")
    if (src_files, src_bytes) != (dst_files, dst_bytes):
        problems.append(
            f"总量不一致：文件 {src_files}→{dst_files}、字节 {src_bytes}→{dst_bytes}"
        )

    for name in names:
        fs, bs = dir_stats(source / name)
        fd, bd = dir_stats(target / name)
        if (fs, bs) != (fd, bd):
            problems.append(
                f"  {name:<24} 源 {fs:>7}/{human(bs):>10}  目标 {fd:>7}/{human(bd):>10}"
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    """命令行入口：见模块 docstring。"""
    parser = argparse.ArgumentParser(description="校验存储根目录迁移（只读）")
    parser.add_argument("--root", required=True, help="新的存储根目录（如 G:/fashion-inspo-storage）")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="素材库 sqlite 路径")
    parser.add_argument("--source", default="", help="旧存储根目录：给了就逐子目录对比完整性")
    parser.add_argument("--limit", type=int, default=0, help="每列最多检查多少条（0=全部）")
    args = parser.parse_args(argv)

    root = Path(args.root)
    db_path = Path(args.db)
    print(f"存储根目录：{root}（存在：{root.exists()}）")
    print(f"素材库    ：{db_path}（存在：{db_path.exists()}）")
    if not root.exists():
        print("❌ 新根目录不存在，先确认复制是否完成")
        return 1
    if not db_path.exists():
        print("❌ 素材库不存在，用 --db 指定")
        return 1

    ok = True
    print()
    print("── 1) 库引用核对（缺失＝切换后这些素材会 404）──")
    try:
        checked, missing, samples = check_db_references(db_path, root, args.limit or None)
    except sqlite3.Error as exc:
        print(f"❌ 读取素材库失败：{exc}")
        return 1
    print(f"  检查路径记录 {checked} 条，缺失 {missing} 条")
    for line in samples:
        print(f"    · {line}")
    if missing:
        ok = False

    print()
    print("── 2) 目录概览 ──")
    files, total = dir_stats(root)
    print(f"  {root}：{files} 个文件，{human(total)}")
    for sub in sorted(p for p in root.iterdir() if p.is_dir()):
        fs, bs = dir_stats(sub)
        print(f"    {sub.name:<22} {fs:>6} 文件 {human(bs):>10}")

    if args.source:
        print()
        print("── 3) 与源目录对比（复制完整性）──")
        problems = compare_dirs(Path(args.source), root)
        if problems:
            ok = False
            print("  ⚠ 发现不一致：")
            for line in problems:
                print(f"    {line}")
        else:
            print("  ✅ 逐子目录文件数与字节完全一致")

    print()
    print("✅ 校验通过" if ok else "❌ 校验未通过（见上面的缺失/不一致项）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
