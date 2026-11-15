"""备份目录保留策略：成功备份只保留最新 N 份，失败备份独立保留少量排障证据。

由 ``scripts/ops/backup_data.sh`` 在写入 SUCCESS 后调用；单独抽出来是为了能对
「超过数量删最旧」做纯函数单测，不必真的跑一次几十 GB 的备份。

规则：
- 只处理匹配 ``YYYY-MM-DD_HHMMSS`` 的目录，手动放进目标根的文件/目录不碰；
- 成功备份（含 SUCCESS）按时间倒序保留前 ``keep_count`` 份，更早删除；
- 失败备份（含 FAILED）独立保留前 ``failed_keep`` 份（默认 3），不占成功备份名额；
- 既无 SUCCESS 也无 FAILED 的中断目录无留证价值，立即删除。
"""

from __future__ import annotations

import argparse
import datetime
import re
import shutil
from pathlib import Path

DEFAULT_KEEP_COUNT = 3
DEFAULT_FAILED_KEEP = 3

_STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}$")


def _parse_stamp(name: str) -> datetime.datetime | None:
    try:
        return datetime.datetime.strptime(name, "%Y-%m-%d_%H%M%S")
    except ValueError:
        return None


def prune_backups(
    root: Path,
    keep_count: int = DEFAULT_KEEP_COUNT,
    *,
    failed_keep: int = DEFAULT_FAILED_KEEP,
    dry_run: bool = False,
) -> dict:
    """清理过期备份目录。

    Args:
        root: 备份目标根目录。
        keep_count: 成功备份保留份数（>=1）。
        failed_keep: 失败备份保留份数（>=0，默认 3 份排障证据）。
        dry_run: 只计算删除清单，不真正删除（测试/排查用）。

    Returns:
        ``{"kept": [...], "deleted": [...], "success_kept": [...],
        "failed_kept": [...]}``；目录不存在时全为空。
    """
    if keep_count < 1:
        raise ValueError("keep_count 必须 >= 1")
    if failed_keep < 0:
        raise ValueError("failed_keep 必须 >= 0")

    if not root.is_dir():
        return {"kept": [], "deleted": [], "success_kept": [], "failed_kept": []}

    success_entries: list[tuple[datetime.datetime, str, Path]] = []
    failed_entries: list[tuple[datetime.datetime, str, Path]] = []
    incomplete: list[Path] = []

    for child in root.iterdir():
        if not child.is_dir() or not _STAMP_RE.match(child.name):
            continue
        stamp = _parse_stamp(child.name)
        if stamp is None:
            continue
        if (child / "SUCCESS").exists():
            success_entries.append((stamp, child.name, child))
        elif (child / "FAILED").exists():
            failed_entries.append((stamp, child.name, child))
        else:
            incomplete.append(child)

    success_entries.sort(key=lambda item: item[0], reverse=True)
    failed_entries.sort(key=lambda item: item[0], reverse=True)

    deleted: list[str] = []
    for path in incomplete:
        if not dry_run:
            shutil.rmtree(path, ignore_errors=True)
        deleted.append(f"{path.name} (中断)")

    success_kept = success_entries[:keep_count]
    for _, name, path in success_entries[keep_count:]:
        if not dry_run:
            shutil.rmtree(path, ignore_errors=True)
        deleted.append(name)

    failed_kept = failed_entries[:failed_keep]
    for _, name, path in failed_entries[failed_keep:]:
        if not dry_run:
            shutil.rmtree(path, ignore_errors=True)
        deleted.append(f"{name} (FAILED)")

    kept = [name for _, name, _ in success_kept] + [
        f"{name} (FAILED)" for _, name, _ in failed_kept
    ]
    return {
        "kept": kept,
        "deleted": deleted,
        "success_kept": [name for _, name, _ in success_kept],
        "failed_kept": [name for _, name, _ in failed_kept],
    }


def main(argv: list[str] | None = None) -> int:
    """命令行入口：``backup_retention.py <备份根目录> [保留份数]``。"""
    parser = argparse.ArgumentParser(description="备份目录保留策略：成功备份保留最近 N 份")
    parser.add_argument("root", help="备份目标根目录")
    parser.add_argument(
        "keep_count",
        nargs="?",
        type=int,
        default=DEFAULT_KEEP_COUNT,
        help=f"成功备份保留份数（默认 {DEFAULT_KEEP_COUNT}）",
    )
    parser.add_argument(
        "--failed-keep",
        type=int,
        default=DEFAULT_FAILED_KEEP,
        help=f"失败备份保留份数（默认 {DEFAULT_FAILED_KEEP}）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印将删除的目录")
    args = parser.parse_args(argv)

    try:
        result = prune_backups(
            Path(args.root),
            keep_count=args.keep_count,
            failed_keep=args.failed_keep,
            dry_run=args.dry_run,
        )
    except ValueError as exc:
        print(f"错误：{exc}")
        return 2

    deleted = result["deleted"]
    kept = result["kept"]
    print(f"  删除 {len(deleted)} 份: {', '.join(deleted) if deleted else '无'}")
    print(f"  保留 {len(kept)} 份: {', '.join(kept) if kept else '无'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
