"""姿势参考图库接口（ai-pose-gallery-zh 的只读代理目录）。

为什么由本服务转发而不是前端直连源站：源站数据文件没有 CORS 头，浏览器跨域
``fetch`` 会被拦；本服务拉一次缓存到 storage，前端只跟这里打交道（源站抖动还有
本地缓存兜底）。**只读**：不写源站、不写仓库；图片在生成时按需下载。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.pose_prompt import PosePrompt
from app.services import generation_service, pose_gallery

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
    db: AsyncSession = Depends(get_db),
) -> dict:
    """分页查询姿势（换姿势选择器用）。

    Returns:
        ``{items, total, page, size, note}``；items 每项含 id/名称/体系/分类/角度与缩略图、
        原图地址（缩略图直接给源站地址，``<img>`` 展示不需要 CORS），以及
        ``usage_count``（该项目中该姿势被用于出图的批次数，见
        ``generation_service.pose_usage_counts``）。
    """
    data = await pose_gallery.list_poses(
        q=q, system=system, category=category, angle=angle, page=page, size=size
    )
    counts = await generation_service.pose_usage_counts(db)
    # 复制条目：pose_gallery 返回的是内存缓存里的 dict，直接写 usage_count 会污染
    # 缓存（还会让 get_pose 等其它接口莫名多一个字段）
    data["items"] = [
        {**item, "usage_count": counts.get(item["id"], 0)} for item in data["items"]
    ]
    return data


@router.get("/random")
async def random_pose(
    q: str | None = Query(None, max_length=100, description="关键字"),
    system: str | None = Query(None, max_length=50, description="体系"),
    category: str | None = Query(None, max_length=50, description="分类"),
    angle: str | None = Query(None, max_length=50, description="角度"),
    exclude: str | None = Query(
        None, max_length=64, description="排除的姿势 id（通常是当前已选）"
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """按当前筛选随机返回一个姿势（选择器「🎲 随机」按钮用）。

    返回单条姿势（与 ``GET /{pose_id}`` 同形），并带 ``usage_count``；筛选后无
    可选项时 404，前端提示「没有匹配的姿势」。
    """
    item = await pose_gallery.random_pose(
        q=q, system=system, category=category, angle=angle, exclude=exclude
    )
    counts = await generation_service.pose_usage_counts(db)
    item["usage_count"] = counts.get(item["id"], 0)
    return item


@router.get("/{pose_id}/prompt")
async def get_pose_prompt(pose_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """取该姿势已反推的提示词（`pose_prompts` 表；图生图自动使用）。

    Returns:
        ``{pose_id, prompt}``；图库无此姿势 404，库里尚未回填时 ``prompt=null``。
    """
    await pose_gallery.get_pose(pose_id)
    row = await db.execute(select(PosePrompt.prompt).where(PosePrompt.pose_id == pose_id))
    prompt = row.scalar_one_or_none()
    return {"pose_id": pose_id, "prompt": prompt}


@router.get("/{pose_id}")
async def get_pose(pose_id: str) -> dict:
    """按 id 取一条姿势（生成历史「带入表单」恢复已选姿势用）。"""
    return await pose_gallery.get_pose(pose_id)
