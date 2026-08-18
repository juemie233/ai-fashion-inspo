"""模特照片组（写真）服务：仅由 ModelService 组合使用。

照片组与穿搭素材分离：模特照片不进入素材库、不参与 AI 打标与检索，
仅按「模特 → 照片组 → 照片」浏览。文件独立落盘 person_photos/，
避免被完整性检查误判为孤立文件。
"""

from __future__ import annotations

import asyncio

from fastapi import HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.person import ModelPhoto, ModelPhotoSet
from app.services.file_service import delete_files, delete_files_counting, save_upload
from app.services.person.base import PersonConflictError, PersonNotFoundError
from app.utils.file_hash import file_sha256


class PhotoSetsMixin:
    """模特照片组方法集：依赖 ``photo_set_model`` / ``photo_model`` / ``has_photo_sets``
    与基类的 ``get`` 方法。仅模特服务（has_photo_sets=True）应组合本 Mixin。
    """

    # ── 序列化 ──

    def _to_photo_set_dict(
        self,
        photo_set: ModelPhotoSet,
        photo_count: int = 0,
        cover_path: str | None = None,
    ) -> dict:
        """将照片组 ORM 对象转为响应字典（含照片数与封面）。"""
        return {
            "id": photo_set.id,
            "model_id": photo_set.model_id,
            "name": photo_set.name,
            "photo_count": photo_count,
            "cover_path": cover_path,
            "created_at": photo_set.created_at,
            "updated_at": photo_set.updated_at,
        }

    def _to_photo_dict(self, photo: ModelPhoto) -> dict:
        """将照片 ORM 对象转为响应字典。"""
        return {
            "id": photo.id,
            "set_id": photo.set_id,
            "file_path": photo.file_path,
            "thumbnail_path": photo.thumbnail_path,
            "sort_order": photo.sort_order,
            "created_at": photo.created_at,
        }

    # ── 照片组 CRUD ──

    async def create_photo_set(
        self, db: AsyncSession, person_id: int, name: str | None = None
    ) -> dict:
        """创建照片组，返回响应字典；主体不存在抛 PersonNotFoundError。"""
        if not self.has_photo_sets:
            raise PersonNotFoundError("该类型不支持照片组")
        await self.get(db, person_id)
        resolved = (name or "").strip() or "未命名照片组"
        photo_set = self.photo_set_model(model_id=person_id, name=resolved)
        db.add(photo_set)
        await db.flush()
        await db.refresh(photo_set)
        return self._to_photo_set_dict(photo_set)

    async def list_photo_sets(
        self, db: AsyncSession, person_id: int, page: int, size: int
    ) -> tuple[list[dict], int]:
        """分页查询照片组（含照片数与封面），主体不存在抛 PersonNotFoundError。"""
        if not self.has_photo_sets:
            raise PersonNotFoundError("该类型不支持照片组")
        await self.get(db, person_id)
        photo_set_model, photo_model = self.photo_set_model, self.photo_model

        # 每组照片数统计子查询
        count_subq = (
            select(
                photo_model.set_id.label("sid"),
                func.count(photo_model.id).label("cnt"),
            )
            .group_by(photo_model.set_id)
            .subquery()
        )

        stmt = (
            select(photo_set_model, func.coalesce(count_subq.c.cnt, 0).label("cnt"))
            .outerjoin(count_subq, photo_set_model.id == count_subq.c.sid)
            .where(photo_set_model.model_id == person_id)
            .order_by(photo_set_model.created_at.desc(), photo_set_model.id.desc())
        )

        total = (
            await db.execute(
                select(func.count()).select_from(
                    select(photo_set_model.id)
                    .where(photo_set_model.model_id == person_id)
                    .subquery()
                )
            )
        ).scalar() or 0

        rows = (
            await db.execute(stmt.offset((page - 1) * size).limit(size))
        ).all()

        # 封面：每组第一条照片（按 sort_order, id），一次查询避免 N+1
        cover_map: dict[int, str] = {}
        set_ids = [row[0].id for row in rows]
        if set_ids:
            cover_result = await db.execute(
                select(
                    photo_model.set_id,
                    photo_model.file_path,
                    photo_model.thumbnail_path,
                )
                .where(photo_model.set_id.in_(set_ids))
                .order_by(photo_model.set_id, photo_model.sort_order, photo_model.id)
            )
            seen: set[int] = set()
            for sid, file_path, thumb_path in cover_result.all():
                if sid not in seen:
                    seen.add(sid)
                    cover_map[sid] = thumb_path or file_path

        items = [self._to_photo_set_dict(ps, cnt, cover_map.get(ps.id)) for ps, cnt in rows]
        return items, total

    async def get_photo_set(self, db: AsyncSession, set_id: int) -> ModelPhotoSet:
        """按 ID 获取照片组，不存在抛 PersonNotFoundError。"""
        photo_set = await db.get(self.photo_set_model, set_id)
        if not photo_set:
            raise PersonNotFoundError("照片组未找到")
        return photo_set

    async def get_photo_set_cover(self, db: AsyncSession, set_id: int) -> str | None:
        """返回照片组的封面路径（组内第一条照片的缩略图或原图），无照片返回 None。"""
        row = (
            await db.execute(
                select(self.photo_model.file_path, self.photo_model.thumbnail_path)
                .where(self.photo_model.set_id == set_id)
                .order_by(self.photo_model.sort_order, self.photo_model.id)
                .limit(1)
            )
        ).first()
        if not row:
            return None
        return row[1] or row[0]

    async def update_photo_set(self, db: AsyncSession, set_id: int, name: str) -> dict:
        """更新照片组名称，返回响应字典；空名称抛 PersonConflictError。"""
        photo_set = await self.get_photo_set(db, set_id)
        resolved = (name or "").strip()
        if not resolved:
            raise PersonConflictError("照片组名称不能为空")
        photo_set.name = resolved
        await db.flush()
        await db.refresh(photo_set)
        return self._to_photo_set_dict(photo_set)

    async def add_photo_to_set(
        self, db: AsyncSession, set_id: int, file: UploadFile, sort_order: int = 0
    ) -> dict:
        """上传一张照片到照片组（含组内内容去重），返回响应字典。

        照片落盘到 person_photos/ 与 person_thumbnails/（与素材库 images/ 分离，
        避免被完整性检查误判为孤立文件）。
        """
        await self.get_photo_set(db, set_id)

        file_path, thumb_path = await save_upload(
            file,
            images_dir=settings.person_photos_dir,
            thumbs_dir=settings.person_thumbnails_dir,
            image_prefix="person_photos",
            thumb_prefix="person_thumbnails",
        )

        # 组内去重：同一照片组内按内容哈希拦截重复照片（大文件哈希放线程池执行）
        content_hash = await asyncio.to_thread(
            file_sha256, settings.storage_root / file_path
        )
        if content_hash:
            dup = await db.execute(
                select(self.photo_model.id).where(
                    self.photo_model.set_id == set_id,
                    self.photo_model.content_hash == content_hash,
                )
            )
            if dup.scalar_one_or_none():
                delete_files(file_path, thumb_path)
                raise HTTPException(status_code=409, detail="该照片已存在于该照片组（内容重复）")

        photo = self.photo_model(
            set_id=set_id,
            file_path=file_path,
            thumbnail_path=thumb_path,
            content_hash=content_hash,
            sort_order=sort_order,
        )
        db.add(photo)
        await db.flush()
        await db.refresh(photo)
        return self._to_photo_dict(photo)

    async def list_set_photos(
        self, db: AsyncSession, set_id: int, page: int, size: int
    ) -> tuple[list[dict], int]:
        """分页查询照片组内的照片，返回 (items, total)；组不存在抛 PersonNotFoundError。"""
        await self.get_photo_set(db, set_id)
        total = (
            await db.execute(
                select(func.count()).select_from(self.photo_model).where(
                    self.photo_model.set_id == set_id
                )
            )
        ).scalar() or 0

        rows = (
            await db.execute(
                select(self.photo_model)
                .where(self.photo_model.set_id == set_id)
                .order_by(self.photo_model.sort_order, self.photo_model.id)
                .offset((page - 1) * size)
                .limit(size)
            )
        ).scalars().all()
        return [self._to_photo_dict(p) for p in rows], total

    async def delete_photo_set(self, db: AsyncSession, set_id: int) -> None:
        """删除照片组（级联删除照片记录），提交成功后清理照片物理文件。"""
        result = await db.execute(
            select(self.photo_set_model)
            .options(selectinload(self.photo_set_model.photos))
            .where(self.photo_set_model.id == set_id)
        )
        photo_set = result.scalar_one_or_none()
        if not photo_set:
            raise PersonNotFoundError("照片组未找到")

        photo_paths: list[str] = []
        for photo in photo_set.photos:
            photo_paths.extend([photo.file_path, photo.thumbnail_path])

        await db.delete(photo_set)
        await db.commit()

        if photo_paths:
            delete_files_counting(*photo_paths)

    async def delete_photo(
        self, db: AsyncSession, photo_id: int, set_id: int | None = None
    ) -> None:
        """删除单张照片（提交成功后清理物理文件），不存在抛 PersonNotFoundError。

        参数:
            set_id: 期望所属的照片组 ID。传入时校验照片归属（防跨组误删），
                归属不符与不存在同等对待（404，不泄露组间关系）。
        """
        photo = await db.get(self.photo_model, photo_id)
        if not photo:
            raise PersonNotFoundError("照片未找到")
        if set_id is not None and photo.set_id != set_id:
            raise PersonNotFoundError("照片未找到")

        file_path, thumb_path = photo.file_path, photo.thumbnail_path
        await db.delete(photo)
        await db.commit()

        delete_files(file_path, thumb_path)
