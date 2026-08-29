"""启动后自动补备：后端稳定运行一段时间后，若近期无成功备份则补跑一次。

与每日 03:00 的 Windows 计划任务（schtasks + backup_task.bat）构成双通道；
两个通道通过备份脚本的 backup.lock 目录锁互斥，不会并发。

设计要点（T6）：
- 启动延迟 N 分钟（避开迁移/初始化竞争），之后每隔几小时复查一次；
- 仅认备份目录下含 SUCCESS 标记的备份为成功（半截/失败备份不算）；
- 距上次成功备份 < min_interval_hours 则跳过（覆盖「当天已备份」语义）；
- 用 asyncio subprocess 异步执行 backup_data.sh，不阻塞 HTTP；
  stdout/stderr 追加到 storage/logs/backup.log（与备份脚本自身日志同文件）；
- 失败只记日志、不重试（次日定时任务兜底）。
"""

import asyncio
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# 备份目录名时间戳格式：YYYY-MM-DD_HHMMSS（与 backup_data.sh 一致）
_STAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{6})$")

# 项目根目录（backend/app/services/backup_service.py → 上三级到项目根）
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKUP_SCRIPT = PROJECT_ROOT / "scripts" / "backup_data.sh"

# Git Bash 常见安装路径（与 scripts/backup_task.bat 保持一致）。
# 后端若以服务方式运行，PATH 中往往没有 bash，需按绝对路径探测。
_BASH_CANDIDATES = (
    r"D:\Program Files (x86)\Git\bin\bash.exe",
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files (x86)\Git\bin\bash.exe",
)


def _resolve_bash() -> str | None:
    """解析 Git Bash 可执行文件路径。

    优先取 PATH 中的 bash（开发环境通常可解析）；找不到时回退到常见
    安装路径（服务运行场景）。仍找不到返回 None，由调用方记录错误并跳过。
    """
    import shutil

    found = shutil.which("bash")
    if found:
        return found
    for cand in _BASH_CANDIDATES:
        if Path(cand).exists():
            return cand
    return None


def latest_success_backup_time(target_root: Path) -> datetime | None:
    """返回目标根目录下最近一份含 SUCCESS 标记的备份时间。

    以备份目录名的时间戳为准（SUCCESS 标记只用于判定成功）；无成功备份返回 None。
    """
    if not target_root.is_dir():
        return None
    latest: datetime | None = None
    for child in target_root.iterdir():
        if not child.is_dir():
            continue
        m = _STAMP_RE.match(child.name)
        if not m:
            continue
        if not (child / "SUCCESS").exists():
            continue
        try:
            dt = datetime.strptime(m.group(1), "%Y-%m-%d_%H%M%S")
        except ValueError:
            continue
        if latest is None or dt > latest:
            latest = dt
    return latest


def should_run_startup_backup(
    target_root: Path,
    *,
    now: datetime,
    min_interval_hours: int,
    backup_on_startup: bool,
) -> bool:
    """判断此刻是否需要补备：开关开 且 近期无成功备份。

    抽成纯函数便于单测（注入 now 与目标路径，不依赖真实文件系统时间）。
    """
    if not backup_on_startup:
        return False
    last = latest_success_backup_time(target_root)
    if last is None:
        return True  # 从未成功备份过，补一次
    return (now - last) >= timedelta(hours=min_interval_hours)


def _read_log_tail(path: Path, max_lines: int = 40) -> list[str]:
    """读取日志文件末尾若干行（文件不存在/不可读时返回空列表）。

    供任务管理页展示备份日志尾部；只读，不影响备份链路。
    """
    if not path.is_file():
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return []
    return [line.rstrip("\r\n") for line in lines[-max_lines:]]


def build_backup_status(
    target_root: Path | None = None,
    *,
    log_path: Path | None = None,
    limit: int = 5,
) -> dict:
    """汇总备份状态供任务管理页展示（只读，不触发备份）。

    target_root / log_path 可注入（测试用）；默认取 settings 配置：
    - enabled / configured / target_path：自动补备开关、目标是否已配置、目标路径
    - running：是否正在备份（目标根下 .backup.lock 并发锁存在，与
      backup_data.sh 的锁机制一致，双通道（计划任务/启动补备）都可见）
    - latest_success_at / latest_success_dir：最近一次成功备份的时间与目录名
    - history：目标根下时间戳备份目录列表（最新在前，至多 limit 条），
      含 success 标记（目录内是否有 SUCCESS 文件）
    - log_tail：storage/logs/backup.log 末尾若干行
    """
    root = target_root
    if root is None:
        target = settings.backup_target_path
        root = Path(target) if target and target.strip() else None

    log = log_path
    if log is None:
        log = settings.storage_root / "logs" / "backup.log"

    latest = latest_success_backup_time(root) if root else None

    history: list[dict] = []
    if root and root.is_dir():
        for child in root.iterdir():
            if not child.is_dir() or not _STAMP_RE.match(child.name):
                continue
            try:
                time_iso = datetime.strptime(child.name, "%Y-%m-%d_%H%M%S").isoformat()
            except ValueError:
                time_iso = None
            history.append(
                {
                    "name": child.name,
                    "success": (child / "SUCCESS").exists(),
                    "time": time_iso,
                }
            )
        # 目录名即时间戳，字符串倒序 = 时间倒序
        history.sort(key=lambda x: x["name"], reverse=True)
        history = history[:limit]

    return {
        "enabled": settings.backup_on_startup,
        "configured": root is not None,
        "target_path": str(root) if root else "",
        "running": bool(root and (root / ".backup.lock").is_dir()),
        "latest_success_at": latest.isoformat() if latest else None,
        "latest_success_dir": latest.strftime("%Y-%m-%d_%H%M%S") if latest else None,
        "history": history,
        "log_tail": _read_log_tail(log),
    }


async def _spawn_backup(target: str) -> int:
    """异步执行 backup_data.sh，返回退出码；stdout/stderr 追加到 backup.log。

    找不到 Git Bash（服务环境 PATH 无 bash 且常见路径不存在）时返回 -1，
    由调用方记录错误，不阻塞启动。
    """
    bash_exe = _resolve_bash()
    if not bash_exe:
        logger.error("[启动补备] 未找到 Git Bash（bash.exe），无法执行 backup_data.sh，本次补备跳过")
        return -1

    log_path = settings.storage_root / "logs" / "backup.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [bash_exe, str(BACKUP_SCRIPT), target]
    logger.info(f"[启动补备] 执行: {' '.join(cmd)}")

    with open(log_path, "a", encoding="utf-8") as log_f:
        log_f.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] 启动补备触发\n")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=log_f,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(PROJECT_ROOT),
        )
        return await proc.wait()


async def _startup_backup_loop() -> None:
    """常驻任务：启动延迟后检查是否需要补备，之后周期性复查。

    仿照 _scraper_schedule_loop 的 while True + try/except 模式；
    异常不中断循环，只记日志。
    """
    target = settings.backup_target_path
    if not target or not target.strip():
        logger.error("[启动补备] 未配置 backup_target_path，启动补备已禁用（请在 .env 设置 BACKUP_TARGET_PATH）")
        return
    target_root = Path(target)
    delay = settings.backup_startup_delay_minutes * 60
    tick = settings.backup_tick_hours * 3600

    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        raise

    while True:
        try:
            if should_run_startup_backup(
                target_root,
                now=datetime.now(),
                min_interval_hours=settings.backup_min_interval_hours,
                backup_on_startup=settings.backup_on_startup,
            ):
                rc = await _spawn_backup(target)
                if rc == 0:
                    logger.info("[启动补备] 备份成功")
                elif rc == -1:
                    # 找不到 bash 已在 _spawn_backup 内记 error，此处不再重复告警
                    pass
                else:
                    logger.warning(f"[启动补备] 备份失败，退出码 {rc}（详见 backup.log）")
            else:
                logger.debug("[启动补备] 近期已有成功备份，跳过")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[启动补备] 检查/执行失败: {e}")

        try:
            await asyncio.sleep(tick)
        except asyncio.CancelledError:
            raise
