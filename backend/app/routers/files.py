"""图片和缩略图的静态文件服务路由。"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import settings

router = APIRouter(prefix="/api/files", tags=["files"])

# 扩展名 → 强制 Content-Type 白名单：只允许图片/视频媒体，杜绝把误入库的
# HTML/SVG 等按 text/html 返回（存储型 XSS 的第二道防线，配合 nosniff）。
_MEDIA_EXT_TO_TYPE = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".gif": "image/gif",
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska", ".webm": "video/webm",
}


@router.get("/{file_path:path}")
async def serve_file(file_path: str):
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
