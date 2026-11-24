"""抖音收藏夹 → 穿搭大标签映射的管理接口（2026-11-24）。

口径见 docs/features/抖音收藏夹大标签映射.md：
- 映射按收藏夹名存，首次「获取我的收藏」前可预配置；
- 未配置的夹首次同步按同名建标签并进「待确认」；
- 同名标签类别冲突返回 409，由前端给三选一。
"""

from fastapi import APIRouter, Depends

from app.database import get_db
from app.schemas.folder_tag import (
    FolderTagApplyResult,
    FolderTagImpactOut,
    FolderTagMappingOut,
    FolderTagUpsertRequest,
)
# 注意：router 不 import sqlalchemy（架构棘轮要求 router 不直连数据层）；
# db 参数不加 AsyncSession 注解，逻辑全部在 folder_tag_service。
from app.services import folder_tag_service

router = APIRouter(prefix="/api/admin/douyin-folder-tags", tags=["admin"])


@router.get("", response_model=list[FolderTagMappingOut])
async def list_folder_tag_mappings(
    db=Depends(get_db),
) -> list[FolderTagMappingOut]:
    """映射总览：已有映射 + 本地抖音夹 + f2 已扫描到的夹名（可预配置）。"""
    rows = await folder_tag_service.list_mappings(db)
    return [FolderTagMappingOut(**row) for row in rows]


@router.post("", response_model=FolderTagMappingOut)
async def upsert_folder_tag_mapping(
    body: FolderTagUpsertRequest, db=Depends(get_db)
) -> FolderTagMappingOut:
    """新建/更新映射；同名标签类别冲突返回 409（前端三选一）。"""
    row = await folder_tag_service.upsert_mapping(
        db,
        body.folder_name,
        mode=body.mode,
        tag_id=body.tag_id,
        new_tag_name=body.new_tag_name,
        reuse_existing_category=body.reuse_existing_category,
    )
    return FolderTagMappingOut(**row)


@router.delete("/{folder_name}")
async def delete_folder_tag_mapping(
    folder_name: str, db=Depends(get_db)
) -> dict:
    """删除映射（回到待处理；已打的标签关联保留）。"""
    return await folder_tag_service.delete_mapping(db, folder_name)


@router.post("/apply-all", response_model=FolderTagApplyResult)
async def apply_all_folder_tags(
    db=Depends(get_db),
) -> FolderTagApplyResult:
    """按当前收藏夹成员全量重打（只处理已映射为 tag 的夹）。"""
    result = await folder_tag_service.apply_all(db)
    return FolderTagApplyResult(**result)


@router.post("/{folder_name}/apply", response_model=FolderTagApplyResult)
async def apply_folder_tags(
    folder_name: str, db=Depends(get_db)
) -> FolderTagApplyResult:
    """按当前成员重打单个收藏夹。"""
    result = await folder_tag_service.apply_all(db, folder_name)
    return FolderTagApplyResult(**result)


@router.get("/tag-impact/{tag_id}", response_model=FolderTagImpactOut)
async def folder_tag_impact(
    tag_id: int, db=Depends(get_db)
) -> FolderTagImpactOut:
    """标签影响面：改名/改类别前告知会影响多少素材与哪些收藏夹。"""
    data = await folder_tag_service.tag_impact(db, tag_id)
    return FolderTagImpactOut(**data)
