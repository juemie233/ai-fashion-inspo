"""人物头像（手动设置）：把选定照片里的**人像**落盘为 ``storage/avatars/avatar_{id}.jpg``。

**头像是手动设置的唯一产物**：用户在详情页/编辑弹窗里从 TA 的素材里挑一张
（或直接上传一张照片），系统送人脸识别子服务取照片里最大的一张人脸，按检测框外扩
裁成正方形人像（见 :mod:`app.services.face_crop`）；照片里没检出人脸（或子服务不可用）
时回退整张照片。系统不再按博主从素材人脸检测里自动生成头像。
``avatar_path`` 为空时前端显示名字首字占位。

三条约定：
  1. 头像文件按人物 id 命名、固定覆盖（``avatar_{id}.jpg``）：换头像不产生孤儿文件
  2. 图片统一重编码为 JPEG 且长边压到 :data:`AVATAR_MAX_SIDE`：头像最大显示 72px，
     原图几 MB 没必要存；视频素材用首帧缩略图（复用 :mod:`app.services.face_image`）
  3. 解不了图直接抛 400（人话提示），不写库——避免把 avatar_path 指向坏文件
"""

from __future__ import annotations

import asyncio
import io
import logging
from pathlib import Path

from fastapi import HTTPException
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.person import Blogger
from app.services.face_image import normalize_image

logger = logging.getLogger(__name__)

"""头像子目录（相对 storage_root）。"""
AVATAR_DIR = "avatars"

"""头像最长边（像素）：详情页最大显示 72px，512 足够清晰且体积小。"""
AVATAR_MAX_SIDE = 512

"""头像 JPEG 质量。"""
AVATAR_QUALITY = 92


def avatar_rel_path(person_id: int) -> str:
    """人物头像的相对路径（相对 storage_root）。"""
    return f"{AVATAR_DIR}/avatar_{person_id}.jpg"


def prepare_avatar_image(data: bytes) -> bytes | None:
    """把图片转成整张照片形态的头像 JPEG（长边 ≤ AVATAR_MAX_SIDE）；解不了返回 None。

    只在「照片里检不出人脸」时兜底使用——正常路径是 :func:`app.services.face_crop.face_avatar`
    从照片里裁出人脸特写。
    """
    normalized = normalize_image(data)
    if normalized is None:
        return None
    try:
        with Image.open(io.BytesIO(normalized)) as image:
            image.load()
            if max(image.size) > AVATAR_MAX_SIDE:
                image.thumbnail((AVATAR_MAX_SIDE, AVATAR_MAX_SIDE), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="JPEG", quality=AVATAR_QUALITY)
            return buffer.getvalue()
    except Exception as exc:  # noqa: BLE001 —— 任何编码异常都按「这张用不了」处理
        logger.debug("头像图片处理失败：%s", exc)
        return None


def delete_avatar_file(person_id: int) -> None:
    """删除人物头像文件（删除人物 / 清除头像时调用）。"""
    path = settings.storage_root / avatar_rel_path(person_id)
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        logger.warning(f"删除人物头像文件失败（person={person_id}）: {e}")


async def _get_blogger(db: AsyncSession, blogger_id: int) -> Blogger:
    blogger = await db.get(Blogger, blogger_id)
    if not blogger:
        raise HTTPException(status_code=404, detail="博主未找到")
    return blogger


async def set_blogger_avatar(
    db: AsyncSession, blogger_id: int, data: bytes, source: str = "upload"
) -> dict:
    """设置博主头像（覆盖旧的）：优先从照片里**提取人像**，提不出来才用整张照片。

    Args:
        db: 数据库会话。
        blogger_id: 博主 id。
        data: 原始图片字节（本地照片，或从素材读到的图片/视频首帧）。
        source: 来源标记（upload 本地照片 / inspiration 素材），仅用于日志与返回。

    Returns:
        {"avatar_path": str, "face_cropped": bool, "message": str}
        ``face_cropped`` 为真表示头像是照片里裁出来的人脸特写，否则是整张照片
        （照片里没检出人脸，或人脸子服务未配置/不可用）。

    Raises:
        HTTPException: 博主不存在 404；图片无法解析 400。
    """
    from app.services.face_crop import face_avatar

    blogger = await _get_blogger(db, blogger_id)
    normalized = await asyncio.to_thread(normalize_image, data)
    if normalized is None:
        raise HTTPException(
            status_code=400,
            detail="这张图片无法解析（支持 JPG/PNG/WebP），请换一张 TA 的清晰照片",
        )

    # 先尝试提取人像（取照片里最大的人脸）；失败/无人脸回退整张照片。
    # 送检与裁剪用的是同一份归一化字节，bbox 坐标系才对得上
    avatar_bytes = await face_avatar(normalized)
    face_cropped = avatar_bytes is not None
    if not face_cropped:
        avatar_bytes = await asyncio.to_thread(prepare_avatar_image, normalized)
        if avatar_bytes is None:
            raise HTTPException(status_code=400, detail="这张图片处理失败，请换一张照片")

    rel_path = avatar_rel_path(blogger_id)
    target = settings.storage_root / rel_path

    def _write() -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(avatar_bytes)

    await asyncio.to_thread(_write)
    blogger.avatar_path = rel_path
    await db.commit()
    await db.refresh(blogger)
    logger.info(
        f"博主 #{blogger_id}「{blogger.name}」头像已更新（来源 {source}，"
        f"{'人脸特写' if face_cropped else '整张照片'}）"
    )

    from app.services.person.services import blogger_service

    return {
        "blogger": blogger_service._to_dict(blogger),
        "avatar_path": rel_path,
        "face_cropped": face_cropped,
        "message": (
            "已提取照片里的人脸作为头像" if face_cropped else "照片里没检出人脸，已用整张照片作为头像"
        ),
    }


async def clear_blogger_avatar(db: AsyncSession, blogger_id: int) -> dict:
    """清除博主头像（avatar_path 置空，前端回退为名字首字占位），并删除头像文件。"""
    blogger = await _get_blogger(db, blogger_id)
    blogger.avatar_path = None
    await db.commit()
    await db.refresh(blogger)
    await asyncio.to_thread(delete_avatar_file, blogger_id)

    return {
        "avatar_path": None,
        "face_cropped": False,
        "message": "已清除头像，列表与详情将显示名字首字占位",
    }


def avatar_file_exists(person_id: int) -> bool:
    """头像文件是否已存在（测试与排查用）。"""
    return (settings.storage_root / avatar_rel_path(person_id)).is_file()


def avatar_dir() -> Path:
    """头像目录（相对 storage_root）。"""
    return settings.storage_root / AVATAR_DIR
