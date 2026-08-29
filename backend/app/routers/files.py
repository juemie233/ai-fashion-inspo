"""图片和缩略图的静态文件服务路由。"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.inspiration import Inspiration
from app.services import video_service

router = APIRouter(prefix="/api/files", tags=["files"])

# 扩展名 → 强制 Content-Type 白名单：只允许图片/视频媒体，杜绝把误入库的
# HTML/SVG 等按 text/html 返回（存储型 XSS 的第二道防线，配合 nosniff）。
_MEDIA_EXT_TO_TYPE = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".gif": "image/gif",
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska", ".webm": "video/webm",
}


# 注意：本路由必须注册在下方「/{file_path:path}」通配路由之前，
# 否则 /api/files/keyframes/{id} 会被通配路由抢先匹配
@router.get("/keyframes/{inspiration_id}")
async def list_keyframes(inspiration_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """返回视频素材的关键帧 URL 列表（按时间序）。

    行为:
        - 非视频素材返回空 frames 列表（不报错，前端无需特判）；
        - 视频素材首次访问时懒提取关键帧（ffmpeg 幂等提取，
          失败静默降级返回空列表，不阻断页面渲染）；
        - 帧文件本体由下方通配静态路由提供（storage/keyframes 在 storage_root 下，
          .jpg 在媒体扩展名白名单内）。
    """
    insp = await db.get(Inspiration, inspiration_id)
    if insp is None:
        raise HTTPException(status_code=404, detail="素材未找到")
    if insp.media_type != "video":
        return {"inspiration_id": inspiration_id, "media_type": insp.media_type, "frames": []}

    frames = await video_service.get_keyframes(inspiration_id)
    if not frames:
        frames = await video_service.extract_keyframes(insp)
    urls = [f"/api/files/keyframes/{inspiration_id}/{f.name}" for f in frames]
    return {
        "inspiration_id": inspiration_id,
        "media_type": "video",
        "count": len(urls),
        "frames": urls,
    }


@router.get("/{file_path:path}")
async def serve_file(file_path: str) -> FileResponse:
    """提供存储文件的访问。例如 /api/files/images/2026-08/abc123.jpg"""
    root = settings.storage_root.resolve()
    full_path = (settings.storage_root / file_path).resolve()

    # 安全检查：确保解析后的路径仍在 storage_root 下。
    # 用 is_relative_to 而非字符串 startswith：前缀比较会被
    # 「storage 开头的兄弟目录」（如 ../storage_backup）绕过。
    try:
        if not full_path.is_relative_to(root):
            raise HTTPException(status_code=403, detail="访问被拒绝")
    except (ValueError, OSError):
        raise HTTPException(status_code=400, detail="无效路径")

    if not full_path.exists() or not full_path.is_file():
        raise HTTPException(status_code=404, detail="文件未找到")

    # 只允许提供白名单内的图片/视频扩展名，并强制对应 MIME 类型
    media_type = _MEDIA_EXT_TO_TYPE.get(full_path.suffix.lower())
    if media_type is None:
        raise HTTPException(status_code=404, detail="文件未找到")

    # nosniff：防止浏览器按内容嗅探，把非图片文件当 HTML/SVG 渲染执行
    return FileResponse(
        full_path,
        media_type=media_type,
        headers={"X-Content-Type-Options": "nosniff"},
    )
