"""姿势参考图库接口（ai-pose-gallery-zh 的只读代理目录）。

为什么由本服务转发而不是前端直连源站：源站数据文件没有 CORS 头，浏览器跨域
``fetch`` 会被拦；本服务拉一次缓存到 storage，前端只跟这里打交道（源站抖动还有
本地缓存兜底）。**只读**：不写源站、不写仓库；图片在生成时按需下载。
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.services import pose_gallery

router = APIRouter(prefix="/api/pose-gallery", tags=["pose-gallery"])


@router.get("/meta")
async def pose_gallery_meta() -> dict:
    """姿势库元信息：总数与体系/分类/角度筛选项（前端下拉用）。"""
    return await pose_gallery.meta()


@router.get("")
async def list_poses(
    q: str | None = Query(None, max_length=100, description="关键字（姿势名/分类/编号/id）"),
    system: str | None = Query(None, max_length=50, description="体系"),
    category: str | None = Query(None, max_length=50, description="分类"),
    angle: str | None = Query(None, max_length=50, description="角度（正面/侧面/…）"),
    page: int = Query(1, ge=1, description="页码（从 1 开始）"),
    size: int = Query(40, ge=1, le=100, description="每页条数"),
) -> dict:
    """分页查询姿势（换姿势选择器用）。

    Returns:
        ``{items, total, page, size, note}``；items 每项含 id/名称/体系/分类/角度与缩略图、
        原图地址（缩略图直接给源站地址，``<img>`` 展示不需要 CORS）。
    """
    return await pose_gallery.list_poses(
        q=q, system=system, category=category, angle=angle, page=page, size=size
    )
