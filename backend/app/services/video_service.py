"""视频关键帧提取服务。

用 ffmpeg 从视频素材中提取关键帧，保存到 ``storage/keyframes/{inspiration_id}/``：
- 不新增数据库表/字段，关键帧按「素材 ID 命名的目录」约定组织，
  前端通过 ``GET /api/files/keyframes/{inspiration_id}`` 按需列目录获取；
- 提取幂等（懒提取）：目录已存在且帧数 > 0 时直接返回，不重复提取；
- 提取失败（文件缺失 / ffmpeg 报错 / 超时）静默降级返回空列表并记日志，
  不抛异常阻断上传、向量、人脸等主流程。

提取策略：
- 默认按固定间隔抽帧（``settings.keyframe_interval_seconds``，默认每 3 秒一帧）；
- 可选场景检测抽帧（``settings.keyframe_scene_threshold`` > 0 时启用
  ``select='gt(scene,t)'``，适合镜头切换稀疏、固定间隔浪费磁盘的视频）；
- 单视频最多提取 ``settings.keyframe_max_frames`` 帧，避免长视频刷爆磁盘。

关键帧的下游消费方：
- 向量链路（vector/similarity.py）：用第一帧生成 CLIP 图像向量；
- 人脸扫描（task_runners/face_scan.py）：对前 N 帧做人脸检测；
- AI 视频分析（主线收口）：将关键帧列表替换图片素材调 analyze 流程；
- 详情页（web DetailView）：横向滚动缩略图展示。
"""

import asyncio
import logging
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# ffmpeg 单次提取超时（秒）：长视频全量解码 + 大量抽帧需放宽，
# 超时强制终止避免挂死调用方（向量回填 / 人脸扫描任务）
_FFMPEG_TIMEOUT = 120

# 防止后台预热任务被垃圾回收的引用集合（任务结束后自动移除）
_prewarm_tasks: set[asyncio.Task] = set()


def keyframes_dir(inspiration_id: str) -> Path:
    """返回指定素材的关键帧目录（``storage/keyframes/{inspiration_id}``）。"""
    return settings.keyframes_dir / inspiration_id


def _list_frames(directory: Path) -> list[Path]:
    """列出现有关键帧（按文件名排序，帧序号即时间顺序），目录不存在返回空。"""
    if not directory.is_dir():
        return []
    return sorted(directory.glob("frame_*.jpg"))


def _build_filter_args() -> list[str]:
    """根据配置构造 ffmpeg 抽帧的 ``-vf`` 过滤参数。

    - 场景检测模式（threshold > 0）：``select='gt(scene,t)'`` 只在画面突变处抽帧；
      表达式内的逗号必须转义（无 shell 环境，逗号会被 ffmpeg 当作过滤器分隔符）；
    - 默认固定间隔模式：``fps=1/interval`` 每隔 interval 秒抽一帧。
    """
    threshold = settings.keyframe_scene_threshold
    if threshold and threshold > 0:
        return ["-vf", f"select=gt(scene\\,{threshold})", "-vsync", "vfr"]
    interval = max(settings.keyframe_interval_seconds, 0.1)
    return ["-vf", f"fps=1/{interval}"]


async def extract_keyframes_for_video(
    inspiration_id: str, video_rel_path: str
) -> list[Path]:
    """提取单个视频的关键帧（幂等），返回帧文件绝对路径列表（时间序）。

    参数:
        inspiration_id: 素材 ID（作为关键帧子目录名）
        video_rel_path: 视频文件相对 storage_root 的路径（如 ``videos/2026-08/x.mp4``）

    说明:
        - 已有帧（目录存在且帧数 > 0）时直接返回，不重复提取；
        - 视频文件缺失、ffmpeg 报错或超时均静默降级返回空列表，仅记日志，
          不抛异常（调用方多为上传/向量/人脸主流程，失败不应阻断）。
    """
    if not video_rel_path:
        return []
    video_path = settings.storage_root / video_rel_path
    if not video_path.exists():
        logger.warning(f"关键帧提取跳过（视频文件缺失）: {video_rel_path}")
        return []

    out_dir = keyframes_dir(inspiration_id)
    existing = _list_frames(out_dir)
    if existing:
        return existing  # 幂等：已有帧直接返回

    out_dir.mkdir(parents=True, exist_ok=True)
    output_pattern = str(out_dir / "frame_%03d.jpg")
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        *_build_filter_args(),
        "-frames:v", str(settings.keyframe_max_frames),
        "-q:v", "2",
        output_pattern,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=_FFMPEG_TIMEOUT)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            # kill 后回收子进程，避免留下僵尸进程
            try:
                await proc.wait()
            except Exception:
                pass
            logger.warning(f"关键帧提取超时（{_FFMPEG_TIMEOUT}s），已终止: {video_rel_path}")
    except Exception as e:
        logger.warning(f"关键帧提取失败（ffmpeg 启动异常，忽略）: {video_rel_path} — {e}")

    frames = _list_frames(out_dir)
    if frames:
        logger.info(f"关键帧提取完成: 素材 {inspiration_id}，共 {len(frames)} 帧")
    else:
        # 空目录移除，保持存储干净；同时让下次调用可重试
        try:
            out_dir.rmdir()
        except OSError:
            pass
        logger.warning(f"关键帧提取失败（无帧产出，忽略）: {video_rel_path}")
    return frames


async def extract_keyframes(inspiration) -> list[Path]:
    """提取素材关键帧（懒提取入口，对 image/非视频素材返回空列表）。

    幂等：已提取过（帧数 > 0）直接返回；失败静默降级返回空列表。
    """
    # 同步读取属性后再进入子流程，避免会话关闭后属性过期（expire_on_commit）问题
    insp_id = inspiration.id
    rel_path = inspiration.file_path
    if getattr(inspiration, "media_type", None) != "video":
        return []
    return await extract_keyframes_for_video(str(insp_id), rel_path)


async def get_keyframes(inspiration_id: str) -> list[Path]:
    """列出现有关键帧（只读，不触发提取），返回按时间序排序的路径列表。"""
    return _list_frames(keyframes_dir(str(inspiration_id)))


async def ensure_first_frame(inspiration) -> Path | None:
    """确保关键帧已提取并返回第一帧路径（向量 / 人脸链路用）。

    无帧可提取（非视频、文件缺失、ffmpeg 失败）返回 None，调用方据此降级。
    """
    frames = await extract_keyframes(inspiration)
    return frames[0] if frames else None


def _cleanup_keyframes_sync(inspiration_id: str) -> None:
    """同步删除素材的关键帧目录（物理删除链路调用，失败仅记日志）。"""
    import shutil

    directory = keyframes_dir(inspiration_id)
    try:
        if directory.is_dir():
            shutil.rmtree(directory)
    except OSError as e:
        logger.warning(f"清理关键帧目录失败（忽略）: {inspiration_id} — {e}")


async def cleanup_keyframes(inspiration_id: str) -> None:
    """删除单个素材的关键帧目录（素材物理删除时调用，幂等）。"""
    await asyncio.to_thread(_cleanup_keyframes_sync, str(inspiration_id))


def sample_frames(frames: list[Path], max_n: int) -> list[Path]:
    """从时间序帧列表中均匀采样最多 max_n 帧（覆盖全片而非只取开头）。

    max_n <= 0 视为不限制（返回全部帧）；帧数不超过上限时原样返回；
    超过时按等距索引采样（首尾帧优先保留：开头交代整体、结尾常有完整
    造型，中间均匀取点）。
    """
    frames = list(frames)
    if max_n <= 0 or len(frames) <= max_n:
        return frames
    if max_n == 1:
        return [frames[0]]
    positions = [round(i * (len(frames) - 1) / (max_n - 1)) for i in range(max_n)]
    # 去重（round 可能产生重复位置）并保持时间序
    return [frames[i] for i in dict.fromkeys(positions)]


async def resolve_analysis_frames(inspiration) -> list[str]:
    """解析素材的「多帧分析源」：返回相对 storage_root 的帧路径列表。

    图片素材 → [原图相对路径]（单帧，与既有 analyze_image 链路一致）；
    视频素材 → 提取关键帧后按 ``settings.video_analysis_max_frames`` 均匀
    采样（懒提取，ffmpeg 失败返回空列表由调用方降级）；
    其余类型 → []。
    """
    media_type = getattr(inspiration, "media_type", None)
    if media_type == "image":
        return [inspiration.file_path]
    if media_type != "video":
        return []

    insp_id = str(inspiration.id)
    frames = await extract_keyframes(inspiration)
    if not frames:
        return []
    max_frames = getattr(settings, "video_analysis_max_frames", 3)
    sampled = sample_frames(frames, max_frames)
    return [f.relative_to(settings.storage_root).as_posix() for f in sampled]


async def cleanup_keyframes_batch(inspiration_ids: list[str]) -> None:
    """批量删除多个素材的关键帧目录（批量物理删除/清空垃圾桶时调用，幂等）。"""
    for insp_id in inspiration_ids:
        await asyncio.to_thread(_cleanup_keyframes_sync, str(insp_id))


def prewarm_keyframes(inspiration) -> None:
    """视频入库后后台预热关键帧（fire-and-forget，不阻塞上传响应）。

    从 ORM 对象同步捕获所需属性后调度后台任务，规避会话提交后属性过期；
    提取失败仅记日志（详情页首次访问 / 向量回填 / 人脸扫描会懒提取兜底）。
    """
    insp_id = str(inspiration.id)
    rel_path = inspiration.file_path
    if getattr(inspiration, "media_type", None) != "video":
        return

    async def _run() -> None:
        try:
            await extract_keyframes_for_video(insp_id, rel_path)
        except Exception as e:  # 兜底：预热失败绝不影响主流程
            logger.warning(f"关键帧后台预热失败（忽略）: {insp_id} — {e}")

    task = asyncio.create_task(_run())
    _prewarm_tasks.add(task)
    task.add_done_callback(_prewarm_tasks.discard)
