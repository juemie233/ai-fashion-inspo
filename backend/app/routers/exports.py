"""导出文件下载路由（P2，2026-11-20）：按 token 下载合集导出。

只负责 HTTP 转译与文件响应，不直连数据库（生成导出在 collections router，
本路由保持无 DB 依赖，符合 router/service 分层与 router-DB 耦合棘轮约束）。

契约见 docs/features/收藏增强-P2-导出与AI整理.md：
``GET /api/exports/{token}``：过期/不存在/非法 token 一律 404；下载头使用安全英文文件名，
响应不暴露服务器绝对路径。
"""

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.services import collection_export_service

router = APIRouter(prefix="/api", tags=["exports"])


@router.get("/exports/{token}")
async def download_collection_export(token: str) -> FileResponse:
    """用 token 下载导出文件（过期/不存在/非法 token 一律 404）。"""
    file_path, meta = collection_export_service.get_export_file(token)
    media_type = collection_export_service.FORMAT_MEDIA_TYPE.get(
        meta.get("format", ""), "application/octet-stream"
    )
    return FileResponse(
        file_path,
        media_type=media_type,
        filename=meta.get("download_filename") or "collection-export",
        headers={"X-Content-Type-Options": "nosniff"},
    )
