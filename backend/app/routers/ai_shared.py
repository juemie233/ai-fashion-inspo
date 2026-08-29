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


async def _resolve_analysis_frames(inspiration) -> list[str]:
    """解析素材的多帧分析源（相对 storage_root 的帧路径列表）。

    图片素材 → [原图相对路径]；视频素材 → 按配置采样关键帧
    （video_analysis_max_frames，懒提取，ffmpeg 耗时因此在后台任务中执行）；
    其余类型或提取失败返回空列表。
    """
    from app.services import video_service

    return await video_service.resolve_analysis_frames(inspiration)


async def _write_unresolvable_log(inspiration_id: str, error: str) -> None:
    """写入「分析源无法解析」的失败日志（与 analyze_image 失败日志同口径，
    保证任务执行器能读到失败原因、历史页能看到失败记录）。"""
    from app.config import settings

    try:
        async with async_session() as db:
            db.add(
                AIAnalysisLog(
                    inspiration_id=inspiration_id,
                    model_name=settings.ollama_vision_model,
                    model_version=settings.ollama_vision_model,
                    prompt_version="",
                    processing_time_ms=0,
                    error=error,
                )
            )
            await db.commit()
    except Exception as log_err:
        logger.error(f"写入分析源解析失败日志出错 {inspiration_id}: {log_err}")


async def _run_analysis(inspiration_id: str, file_path: str | None = None) -> None:
    """后台任务：对素材执行 AI 分析并保存标签（带并发控制 + 任务追踪）。

    file_path 为 None 时（视频素材 / 重试路径）从数据库懒解析分析源：
    图片 → 原图，视频 → 按配置采样关键帧（必要时现场提取 ffmpeg），
    多帧走 analyze_video 融合分析；解析失败写入失败日志。
    """
    if inspiration_id in _active_analyses:
        logger.info(f"素材已在分析队列中，跳过: {inspiration_id}")
        return

    success = False

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
                from app.services.ai_service import analyze_image, analyze_video

                # 分析源懒解析：视频素材在此现场提取并采样关键帧（避免阻塞 HTTP 请求）
                frames: list[str] = [file_path] if file_path else []
                if not frames:
                    async with async_session() as db:
                        result = await db.execute(
                            select(Inspiration).where(Inspiration.id == inspiration_id)
                        )
                        inspiration = result.scalar_one_or_none()
                    if inspiration is None:
                        logger.warning(f"分析源解析失败（素材不存在）: {inspiration_id}")
                        await _write_unresolvable_log(
                            inspiration_id, "分析失败：素材不存在"
                        )
                        return
                    frames = await _resolve_analysis_frames(inspiration)
                    if not frames:
                        logger.warning(
                            f"分析源解析失败（视频关键帧提取失败）: {inspiration_id}"
                        )
                        await _write_unresolvable_log(
                            inspiration_id,
                            "分析失败：视频关键帧提取失败（视频文件缺失或 ffmpeg 异常）",
                        )
                        return

                logger.info(f"开始 AI 分析: {inspiration_id}")
                async with async_session() as db:
                    if len(frames) > 1:
                        # 视频：多帧融合分析（标签按帧融合后一次落库，单条日志）
                        success = await analyze_video(db, inspiration_id, frames)
                    else:
                        success = await analyze_image(db, inspiration_id, frames[0])
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

        # 实时推送分析结果（前端 useWebSocket 监听 ai_analysis_done）
        try:
            from app.routers.ws import manager

            await manager.broadcast(
                {
                    "type": "ai_analysis_done",
                    "inspiration_id": inspiration_id,
                    "success": success,
                }
            )
        except Exception:
            logger.debug("WebSocket 广播失败（忽略，不影响分析主流程）", exc_info=True)


async def _update_env_file(updates: dict[str, str]) -> None:
    """将键值对更新写入 .env 文件（保留其他配置不变）。"""
    env_path = Path(__file__).parent.parent.parent / ".env"

    def _write() -> None:
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
