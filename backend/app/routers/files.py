"""图片和缩略图的静态文件服务路由。"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import settings

router = APIRouter(prefix="/api/files", tags=["files"])


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

    # nosniff：防止浏览器将非图片文件（如误入库的 HTML/SVG）按 HTML/SVG 渲染执行
    return FileResponse(
        full_path,
        headers={"X-Content-Type-Options": "nosniff"},
    )
