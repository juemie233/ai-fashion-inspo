"""f2 导入路径与采集路径共用的视频首帧缩略图工具。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from app.config import settings


def extract_video_thumbnail_sync(
    video_path: Path, today: str, thumbs_dir: Path | None = None
) -> str | None:
    """用 ffmpeg 提取视频首帧缩略图（同步 subprocess），失败返回 None。

    Args:
        video_path: 视频文件路径。
        today: 日期字符串（用于构建缩略图子目录）。
        thumbs_dir: 缩略图根目录（缺省 settings.thumbnails_dir）。

    Returns:
        缩略图相对路径（如 ``thumbnails/2025-01/thumb_xxx.jpg``），失败返回 None。
    """
    thumb_dir = (thumbs_dir or settings.thumbnails_dir) / today
    thumb_dir.mkdir(parents=True, exist_ok=True)
    thumb_name = f"thumb_{video_path.stem}.jpg"
    thumb_path = thumb_dir / thumb_name
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                "1",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-vf",
                "scale=400:-2",
                str(thumb_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except Exception:
        return None
    if thumb_path.exists() and thumb_path.stat().st_size > 0:
        return f"thumbnails/{today}/{thumb_name}"
    return None
