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


async def _spawn_backup(target: str) -> int:
    """异步执行 backup_data.sh，返回退出码；stdout/stderr 追加到 backup.log。"""
    log_path = settings.storage_root / "logs" / "backup.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    bash_exe = "bash"
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
