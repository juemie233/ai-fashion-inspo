"""清理运行产物：一次性日志与过期 DB 改前快照（默认只预览，``--apply`` 才删）。

对应文档 [docs/ops/运行产物与实验数据管理.md](../../docs/ops/运行产物与实验数据管理.md)
第 11 节的缺口 1、2。这两个目录长期只增不减：

- ``logs/``：服务日志（``backend/frontend/worker/supervisor/face-service``）由 supervisor
  按 10 MB × 5 轮转，但**一次性排查日志**（``ab-*.log``、``backfill_*.log``、``f2-*.log``）
  没人回收，实测 137 个 / 7.4 MB；
- ``backend/storage/_db_backups/``：手工改库前的快照，一份就是 400~500 MB，实测 2 份 911 MB。

为什么默认 dry-run：这两类删除都**不可恢复**（快照是回滚保险，日志是事后证据）。
所以脚本只负责「按规则列清单 + 报体积」，删不删由人确认后再加 ``--apply``。

判定规则
--------
- 服务日志（**永不删**）：``^(backend|frontend|worker|supervisor|face-service)\\.log(\\.\\d+)?$``
- 一次性日志：``logs/`` 下其余 ``*.log`` / ``*.log.N``；``f2-*.log`` 默认保留
  （体积极小、且是采集排障的第一手材料），要删加 ``--include-f2``；
  **0 字节的 f2 日志始终清**（f2 启动即退出时留下的空壳，无排障价值）
- 按天日志：``analyze-YYYY-MM-DD.log`` 超过 ``--analyze-keep-days``（默认 30）天才删
  （worker 启动时也会自动清一次，这里用于手工补清）
- 历史遗留：``backend/logs/*``、``backend/{api,worker}.{out,err}``（手动启动遗留，
  2026-08 之后不再更新）一并列入，删除前务必确认没有进程还在写
- DB 快照：按 mtime 保留**最新 N 份**且**不超过 keep-days 天**；两份规则都满足才保留
  （即「太旧」或「超份数」任一命中即列入删除）

用法::

    python scripts/ops/cleanup_artifacts.py                      # 只预览
    python scripts/ops/cleanup_artifacts.py --target logs        # 只看日志
    python scripts/ops/cleanup_artifacts.py --apply              # 真删
    python scripts/ops/cleanup_artifacts.py --apply --include-f2 --keep-count 20
    python scripts/ops/cleanup_artifacts.py --target logs --analyze-keep-days 14
    python scripts/ops/cleanup_artifacts.py --target snapshots --max-total-mb 512   # 压到 512 MB

退出码：0=正常（含无可清理项）；1=参数错误。
"""

import argparse
import io
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# UTF-8 输出：Windows 控制台（配合 chcp 65001）中文不乱码
try:
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )
except (AttributeError, ValueError):  # 非交互环境（重定向/CI）没有 buffer，允许失败
    pass

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOGS_DIR = PROJECT_ROOT / "logs"
SNAPSHOT_DIR = PROJECT_ROOT / "backend" / "storage" / "_db_backups"

# supervisor 管理的服务日志：轮转策略在 supervisor 里，脚本绝不碰
SERVICE_LOG_RE = re.compile(r"^(backend|frontend|worker|supervisor|face-service)\.log(\.\d+)?$")
F2_LOG_RE = re.compile(r"^f2-.*\.log(\.\d+)?$")
ANALYZE_LOG_RE = re.compile(r"^analyze-\d{4}-\d{2}-\d{2}\.log$")

# 历史遗留产物（手动启动时代留下的，不再更新）
LEGACY_DIRS = (PROJECT_ROOT / "backend" / "logs",)
LEGACY_FILES = (
    PROJECT_ROOT / "backend" / "api.out",
    PROJECT_ROOT / "backend" / "api.err",
    PROJECT_ROOT / "backend" / "worker.out",
    PROJECT_ROOT / "backend" / "worker.err",
)

DEFAULT_KEEP_DAYS = 30
DEFAULT_KEEP_COUNT = 20
DEFAULT_ANALYZE_KEEP_DAYS = 30


def human_size(num_bytes: float) -> str:
    """把字节数格式化成易读单位（B/KB/MB/GB）。"""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GB"


def _mtime(path: Path) -> datetime:
    """文件修改时间（UTC 感知；展示时再转本地时区）。"""
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


def collect_oneoff_logs(include_f2: bool, analyze_keep_days: int = DEFAULT_ANALYZE_KEEP_DAYS) -> list[Path]:
    """列出「一次性日志」：logs/ 下除服务日志以外的一切 .log/.log.N。

    规则（2026-11-02 日志扫描 P1-4）：

    - ``analyze-*.log``（worker 按天日志）只清超过 ``analyze_keep_days`` 天的；
    - ``f2-*.log`` 默认保留（采集排障第一手材料），但 **0 字节空壳始终清**；
    - 其余一次性排查日志（``ab-*`` / ``backfill_*`` 等）全部列入。
    """
    if not LOGS_DIR.is_dir():
        return []
    analyze_cutoff = datetime.now(UTC) - timedelta(days=max(0, analyze_keep_days))
    victims: list[Path] = []
    for path in sorted(LOGS_DIR.iterdir()):
        if not path.is_file():
            continue
        if not (path.name.endswith(".log") or re.search(r"\.log\.\d+$", path.name)):
            continue
        if SERVICE_LOG_RE.match(path.name):
            continue
        if ANALYZE_LOG_RE.match(path.name):
            if _mtime(path) >= analyze_cutoff:
                continue
            victims.append(path)
            continue
        if F2_LOG_RE.match(path.name):
            # 0 字节空壳（f2 启动即失败/立刻退出）没有排障价值，始终清
            if path.stat().st_size == 0:
                victims.append(path)
                continue
            if not include_f2:
                continue
        victims.append(path)
    return victims


def collect_legacy_artifacts() -> list[Path]:
    """列出历史遗留的手动启动产物（``backend/logs/*``、``*.out`` / ``*.err``）。"""
    victims: list[Path] = []
    for directory in LEGACY_DIRS:
        if directory.is_dir():
            victims.extend(sorted(p for p in directory.iterdir() if p.is_file()))
    victims.extend(p for p in LEGACY_FILES if p.is_file())
    return victims


def collect_old_snapshots(
    keep_days: int, keep_count: int, max_total_mb: int = 0
) -> list[Path]:
    """列出应删除的 DB 快照：超保留份数、超保留天数，或超总体积预算。

    三条规则「或」关系（命中即删，最新一份**永不删**）：
    - 份数：只保留最新 ``keep_count`` 份；
    - 天数：超过 ``keep_days`` 天的删掉（避免「20 份但全是两年前的」假保留）；
    - 体积：``max_total_mb > 0`` 时，从最旧的开始删到总体积 ≤ 预算——
      单份快照 400~500 MB，只靠份数/天数压不住磁盘占用。
    """
    if not SNAPSHOT_DIR.is_dir():
        return []
    snaps = sorted(
        (p for p in SNAPSHOT_DIR.glob("*.db") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    cutoff = datetime.now(UTC) - timedelta(days=keep_days)
    victims: list[Path] = []
    for index, path in enumerate(snaps):
        too_many = index >= keep_count
        too_old = _mtime(path) < cutoff
        if too_many or too_old:
            victims.append(path)

    # 体积预算：从最旧的开始追加删除，直到剩余总量进预算（最新一份不动）
    if max_total_mb > 0 and snaps:
        budget = max_total_mb * 1024 * 1024
        remaining = [p for p in snaps if p not in victims]
        newest = snaps[0]  # snaps 按 mtime 倒序，首个即最新
        total = sum(p.stat().st_size for p in remaining)
        for path in sorted(remaining, key=lambda p: p.stat().st_mtime):
            if total <= budget or path == newest:
                break
            victims.append(path)
            total -= path.stat().st_size
    return victims


def _print_group(title: str, victims: list[Path], *, keep_note: str) -> int:
    """打印一组待删清单并返回总体积（字节）。"""
    total = 0
    print(f"\n[{title}] 待删除 {len(victims)} 个（{keep_note}）")
    if not victims:
        print("  （无）")
        return 0
    for path in victims:
        size = path.stat().st_size
        total += size
        rel = path.relative_to(PROJECT_ROOT) if path.is_relative_to(PROJECT_ROOT) else path
        stamp = _mtime(path).astimezone().strftime("%Y-%m-%d %H:%M")
        print(f"  - {rel}  ({human_size(size)}, {stamp})")
    print(f"  合计: {human_size(total)}")
    return total


def main(argv: list[str] | None = None) -> int:
    """解析参数、列清单，按需删除。"""
    parser = argparse.ArgumentParser(
        description="清理一次性日志与过期 DB 改前快照（默认只预览）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--target",
        choices=("all", "logs", "snapshots"),
        default="all",
        help="清理哪一类（默认 all）",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正删除；不加时只打印清单（dry-run）",
    )
    parser.add_argument(
        "--include-f2",
        action="store_true",
        help="连 f2-*.log 一起清（默认保留：体积小且是采集排障的第一手材料）",
    )
    parser.add_argument(
        "--keep-days",
        type=int,
        default=DEFAULT_KEEP_DAYS,
        help=f"DB 快照保留天数（默认 {DEFAULT_KEEP_DAYS}）",
    )
    parser.add_argument(
        "--keep-count",
        type=int,
        default=DEFAULT_KEEP_COUNT,
        help=f"DB 快照最多保留份数（默认 {DEFAULT_KEEP_COUNT}）",
    )
    parser.add_argument(
        "--max-total-mb",
        type=int,
        default=0,
        help="DB 快照总体积预算（MB，0=不限）；超出时从最旧的开始删（最新一份永不删）",
    )
    parser.add_argument(
        "--analyze-keep-days",
        type=int,
        default=DEFAULT_ANALYZE_KEEP_DAYS,
        help=f"按天日志 analyze-*.log 保留天数（默认 {DEFAULT_ANALYZE_KEEP_DAYS}）",
    )
    args = parser.parse_args(argv)

    if (
        args.keep_days < 0
        or args.keep_count < 0
        or args.max_total_mb < 0
        or args.analyze_keep_days < 0
    ):
        parser.error(
            "--keep-days / --keep-count / --max-total-mb / --analyze-keep-days 不能为负数"
        )

    logs: list[Path] = []
    legacy: list[Path] = []
    snaps: list[Path] = []
    if args.target in ("all", "logs"):
        logs = collect_oneoff_logs(
            include_f2=args.include_f2, analyze_keep_days=args.analyze_keep_days
        )
        legacy = collect_legacy_artifacts()
    if args.target in ("all", "snapshots"):
        snaps = collect_old_snapshots(args.keep_days, args.keep_count, args.max_total_mb)

    print(f"仓库根: {PROJECT_ROOT}")
    print(f"模式:   {'删除（--apply）' if args.apply else '预览（dry-run，加 --apply 才删）'}")
    total = 0
    if args.target in ("all", "logs"):
        keep_note = (
            f"保留服务轮转日志；analyze 保留 {args.analyze_keep_days} 天"
            + ("；含 f2 日志" if args.include_f2 else "；保留 f2 日志（0 字节除外）")
        )
        total += _print_group("一次性日志 logs/", logs, keep_note=keep_note)
        total += _print_group(
            "历史遗留 backend/（手动启动产物）", legacy, keep_note="8 月手动启动遗留，确认无进程占用"
        )
    if args.target in ("all", "snapshots"):
        keep_note = (
            f"保留最新 {args.keep_count} 份且 {args.keep_days} 天内"
            + (f"、总量 ≤ {args.max_total_mb} MB" if args.max_total_mb else "")
        )
        total += _print_group("DB 改前快照 _db_backups/", snaps, keep_note=keep_note)

    if not (logs or legacy or snaps):
        print("\n没有需要清理的产物。")
        return 0
    if not args.apply:
        print(f"\n共可释放约 {human_size(total)}。确认后重跑并加 --apply。")
        return 0

    failed: list[tuple[Path, str]] = []
    removed = 0
    for path in [*logs, *legacy, *snaps]:
        try:
            path.unlink()
            removed += 1
        except OSError as exc:  # 文件被占用/权限不足：列出而不是静默跳过
            failed.append((path, str(exc)))
    print(f"\n已删除 {removed} 个文件，释放约 {human_size(total)}。")
    if failed:
        print("以下文件删除失败（可能被进程占用）：")
        for path, reason in failed:
            print(f"  - {path.name}: {reason}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
