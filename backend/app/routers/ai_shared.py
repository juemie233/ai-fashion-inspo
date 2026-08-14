"""AI 路由共享状态与后台任务。

多个 AI 子路由（ai_analysis 等）共享的：
- 并发信号量、分析队列、任务追踪等内存状态
- _run_analysis 后台任务
- .env 更新、时间/大小格式化等辅助函数
"""

import asyncio
import logging
import re
from pathlib import Path

from sqlalchemy import delete, select

from app.config import settings
from app.database import async_session
from app.models.inspiration import AIAnalysisLog, Inspiration

logger = logging.getLogger(__name__)

# 分析任务并发控制：最多同时分析 2 个素材，避免显存溢出
_analysis_semaphore = asyncio.Semaphore(2)
# 正在分析中的 inspiration_id 集合，用于前端轮询
_active_analyses: dict[str, str] = {}  # inspiration_id -> 状态描述
# 保留任务引用，防止 GC 回收
_analysis_tasks: set[asyncio.Task] = set()
# 任务 ID → Task 映射，用于取消单个任务
_task_by_id: dict[str, asyncio.Task] = {}
# 排队中的任务 ID 列表（尚未获取信号量的）
_pending_queue: list[str] = []
# 队列暂停开关
_queue_paused = False


def get_queue_paused() -> bool:
    """返回队列暂停状态（供其它模块读取，避免按值导入导致读到旧值）。"""
    return _queue_paused


def set_queue_paused(paused: bool) -> None:
    """设置队列暂停状态（供其它模块修改，避免按值导入失效）。"""
    global _queue_paused
    _queue_paused = paused


async def _run_analysis(inspiration_id: str, file_path: str):
    """后台任务：对图片执行 AI 分析并保存标签（带并发控制 + 任务追踪）。"""
    if inspiration_id in _active_analyses:
        logger.info(f"素材已在分析队列中，跳过: {inspiration_id}")
        return

    # 注册当前任务
    current_task = asyncio.current_task()
    if current_task:
        _task_by_id[inspiration_id] = current_task

    # 加入排队
    _pending_queue.append(inspiration_id)
    _active_analyses[inspiration_id] = "排队中..."

    try:
        # 暂停检查放在信号量之前，避免消耗信号量槽位
        while _queue_paused:
            await asyncio.sleep(1)

        async with _analysis_semaphore:
            try:
                # 安全地从排队列表移除（可能已被取消端点移除）
                try:
                    _pending_queue.remove(inspiration_id)
                except ValueError:
                    pass
                _active_analyses[inspiration_id] = "正在分析..."
                from app.services.ai_service import analyze_image

                logger.info(f"开始 AI 分析: {inspiration_id}")
                async with async_session() as db:
                    success = await analyze_image(db, inspiration_id, file_path)
                    # 仅分析成功时删除该素材的旧失败日志
                    if success:
                        old_logs = await db.execute(
                            select(AIAnalysisLog.id).where(
                                AIAnalysisLog.inspiration_id == inspiration_id,
                                AIAnalysisLog.error.isnot(None),
                            )
                        )
                        old_ids = [row[0] for row in old_logs]
                        if old_ids:
                            await db.execute(
                                delete(AIAnalysisLog).where(AIAnalysisLog.id.in_(old_ids))
                            )
                            await db.commit()
                            logger.info(f"清理了 {len(old_ids)} 条旧失败日志: {inspiration_id}")
                logger.info(f"AI 分析完成: {inspiration_id}")
            except asyncio.CancelledError:
                logger.info(f"分析任务被取消: {inspiration_id}")
                raise
            except ImportError:
                logger.warning("AI 服务尚未安装")
            except Exception as e:
                logger.error(f"分析失败 {inspiration_id}: {e}")
    finally:
        # 无论何时被取消（含等待信号量/暂停期间），都清理队列与追踪状态
        _active_analyses.pop(inspiration_id, None)
        _task_by_id.pop(inspiration_id, None)
        try:
            _pending_queue.remove(inspiration_id)
        except ValueError:
            pass


async def _update_env_file(updates: dict[str, str]) -> None:
    """将键值对更新写入 .env 文件（保留其他配置不变）。"""
    env_path = Path(__file__).parent.parent.parent / ".env"

    def _write():
        if env_path.exists():
            content = env_path.read_text(encoding="utf-8")
        else:
            content = ""

        for key, value in updates.items():
            if re.search(rf"^{key}=.*$", content, re.MULTILINE):
                content = re.sub(rf"^{key}=.*$", f"{key}={value}", content, flags=re.MULTILINE)
            else:
                if content and not content.endswith("\n"):
                    content += "\n"
                content += f"{key}={value}\n"

        env_path.write_text(content, encoding="utf-8")

    await asyncio.to_thread(_write)
    logger.info(f"已更新 .env: {list(updates.keys())}")


def _fmt_utc(dt) -> str:
    """将 naive UTC datetime 格式化为带 Z 后缀的 ISO 字符串。"""
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _format_size(size_bytes: int) -> str:
    """将字节数转换为可读格式。"""
    if size_bytes == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    size = float(size_bytes)
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.1f} {units[i]}"
