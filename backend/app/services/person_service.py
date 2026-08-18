"""人物服务：穿搭博主（Blogger）与职业模特（Model）的 CRUD 与关联管理。

原单表 persons 已拆为 bloggers / models 两张独立表；本模块以
``PersonServiceBase`` 基类收敛两者共用的查询/写入逻辑（模型与关联表
由子类指定），``blogger_service`` / ``model_service`` 两个实例对外服务。

人物关联一律使用 ID（不按名称匹配），规避「同名多人」的歧义；
素材-博主 / 素材-模特关联分别写入独立关联表（inspiration_bloggers /
inspiration_models）。模特另拥有写真照片组（model_photo_sets）。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import HTTPException, UploadFile
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.inspiration import Inspiration
from app.models.person import (
    Blogger,
    InspirationBlogger,
    InspirationModel,
    Model,
    ModelPhoto,
    ModelPhotoSet,
)
from app.models.tag import InspirationTag, Tag
from app.services.audit_service import record_audit_log
from app.services.file_service import (
    delete_files,
    delete_files_counting,
    save_upload,
)
from app.utils.file_hash import file_sha256

logger = logging.getLogger(__name__)

# 风格画像 top_tags 数量上限
STYLE_PROFILE_TOP_TAGS = 15

# CSV 导入错误明细上限：避免超大文件撑爆响应体
_IMPORT_ERROR_LIMIT = 100


class PersonNotFoundError(Exception):
    """人物或关联对象不存在（路由层转为 404）。"""

    def __init__(self, message: str = "人物未找到") -> None:
        super().__init__(message)
        self.message = message


class PersonConflictError(Exception):
    """人物数据冲突（路由层转为 409）。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class PersonHasInspirationsError(Exception):
    """人物仍关联素材，禁止删除（路由层转为 400）。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class PersonServiceBase:
    """博主/模特通用服务：通过子类指定模型与关联表实现复用。

    子类必须定义:
        model: 主体模型类（Blogger / Model）
        link_model: 素材关联模型类（InspirationBlogger / InspirationModel）
        link_id_field: 关联表外键字段名（"blogger_id" / "model_id"）
        link_entity_attr: 关联对象上指向主体的 relationship 属性名
        label: 中文称呼（用于错误文案）

    模特专属（照片组）:
        photo_set_model / photo_model / has_photo_sets
    """

    model: type[Blogger] | type[Model] | None = None
    link_model: type[InspirationBlogger] | type[InspirationModel] | None = None
    link_id_field: str = ""
    link_entity_attr: str = ""
    photo_set_model: type[ModelPhotoSet] | None = None
    photo_model: type[ModelPhoto] | None = None
    has_photo_sets: bool = False
    label: str = "人物"

    # ── 序列化 ──

    def _to_dict(self, person: Blogger | Model, inspiration_count: int = 0) -> dict:
        """将主体 ORM 对象转为响应字典（含素材数统计）。"""
        return {
            "id": person.id,
            "name": person.name,
            "platform": person.platform,
            "platform_user_id": person.platform_user_id,
            "xhs_id": person.xhs_id,
            "ip_location": person.ip_location,
            "profile_url": person.profile_url,
            "avatar_path": person.avatar_path,
            "bio": person.bio,
            "source": person.source,
            "created_at": person.created_at,
            "updated_at": person.updated_at,
            "inspiration_count": inspiration_count,
        }

    def _link_id_col(self):
        """关联表的主体外键列（blogger_id / model_id）。"""
        assert self.link_model is not None
        return getattr(self.link_model, self.link_id_field)

    # ── 列表 / 详情 ──

    async def list_items(
        self,
        db: AsyncSession,
        page: int,
        size: int,
        search: str | None = None,
        platform: str | None = None,
        sort: str = "newest",
    ) -> tuple[list[dict], int]:
        """分页查询（含素材数统计与多维筛选），返回 (items, total)。"""
        assert self.model is not None and self.link_model is not None
        model, link_model = self.model, self.link_model
        link_id = self._link_id_col()

        # 素材数统计子查询：人物 -> 有效素材关联数（排除软删除素材）
        count_subq = (
            select(
                link_id.label("pid"),
                func.count(link_model.inspiration_id).label("cnt"),
            )
            .join(Inspiration, Inspiration.id == link_model.inspiration_id)
            .where(Inspiration.deleted_at.is_(None))
            .group_by(link_id)
            .subquery()
        )

        stmt = (
            select(model, func.coalesce(count_subq.c.cnt, 0).label("cnt"))
            .outerjoin(count_subq, model.id == count_subq.c.pid)
        )
        if search:
            # 搜索覆盖昵称 / 小红书号 / IP 属地（任一命中即匹配）
            kw = search.strip()
            stmt = stmt.where(
                or_(
                    model.name.contains(kw),
                    model.xhs_id.contains(kw),
                    model.ip_location.contains(kw),
                )
            )
        if platform:
            stmt = stmt.where(model.platform == platform)

        # 排序：newest（创建时间倒序）| name（按名称）| count（按素材数倒序）
        if sort == "name":
            stmt = stmt.order_by(model.name.asc(), model.id.desc())
        elif sort == "count":
            stmt = stmt.order_by(func.coalesce(count_subq.c.cnt, 0).desc(), model.id.desc())
        else:
            stmt = stmt.order_by(model.created_at.desc(), model.id.desc())

        # 统计总数（不含分页）
        count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total = (await db.execute(count_stmt)).scalar() or 0

        stmt = stmt.offset((page - 1) * size).limit(size)
        rows = (await db.execute(stmt)).all()
        items = [self._to_dict(p, cnt) for p, cnt in rows]
        return items, total

    async def get(self, db: AsyncSession, person_id: int) -> Blogger | Model:
        """按 ID 获取主体，不存在抛 PersonNotFoundError。"""
        assert self.model is not None
        person = await db.get(self.model, person_id)
        if not person:
            raise PersonNotFoundError(f"{self.label}未找到")
        return person

    async def create(
        self,
        db: AsyncSession,
        name: str,
        platform: str = "other",
        platform_user_id: str | None = None,
        xhs_id: str | None = None,
        ip_location: str | None = None,
        profile_url: str | None = None,
        avatar_path: str | None = None,
        bio: str | None = None,
    ) -> dict:
        """创建主体，返回响应字典。"""
        assert self.model is not None
        clean_name = name.strip()
        if not clean_name:
            raise PersonConflictError(f"{self.label}名称不能为空")
        person = self.model(
            name=clean_name,
            platform=platform,
            platform_user_id=platform_user_id,
            xhs_id=xhs_id,
            ip_location=ip_location,
            profile_url=profile_url,
            avatar_path=avatar_path,
            bio=bio,
            source="manual",
        )
        db.add(person)
        try:
            # SAVEPOINT 隔离：xhs_id 唯一索引冲突（并发/重复导入）时降级为 409，
            # 避免直接 500
            async with db.begin_nested():
                await db.flush()
        except IntegrityError:
            db.expunge(person)
            raise PersonConflictError(
                f"{self.label}的小红书号（xhs_id）已被其它记录占用"
            )
        await db.refresh(person)
        return self._to_dict(person)

    async def update(self, db: AsyncSession, person_id: int, payload: dict) -> dict:
        """更新字段并返回响应字典，不存在抛 PersonNotFoundError。

        参数:
            payload: 由 Update.model_dump(exclude_unset=True) 得到的字段字典——
                未传的字段不在 dict 中（保持不变），显式传 None 的字段会被清空。
        """
        person = await self.get(db, person_id)
        for field, value in payload.items():
            if field == "name":
                value = value.strip() if value is not None else None
                if not value:
                    raise PersonConflictError(f"{self.label}名称不能为空")
            setattr(person, field, value)
        try:
            # SAVEPOINT 隔离：xhs_id 改到已被占用的值时降级为 409，避免 500
            async with db.begin_nested():
                await db.flush()
        except IntegrityError:
            raise PersonConflictError(
                f"{self.label}的小红书号（xhs_id）已被其它记录占用"
            )
        await db.refresh(person)
        return self._to_dict(person)

    async def delete(self, db: AsyncSession, person_id: int) -> None:
        """删除主体（仅允许无关联素材时删除），不存在抛 404。

        删除前校验**全部关联数**（含软删除的垃圾桶素材）：
        - 关联素材数 > 0 时抛 PersonHasInspirationsError，禁止删除。
        - 必须统计全部关联而非仅未删除素材：ORM 的 delete-orphan 级联会物理
          删除垃圾桶（可恢复）素材的关联行，若只按有效素材校验，恢复素材后
          博主/模特信息会永久丢失。
        通过校验后，模特会级联删除照片组与照片记录；照片物理文件在提交成功后
        统一清理，避免「文件已删但事务失败」产生悬空记录。
        """
        assert self.model is not None
        stmt = select(self.model).where(self.model.id == person_id)
        if self.has_photo_sets:
            assert self.photo_set_model is not None and self.photo_model is not None
            stmt = stmt.options(
                selectinload(self.model.photo_sets).selectinload(
                    self.photo_set_model.photos
                )
            )
        result = await db.execute(stmt)
        person = result.scalar_one_or_none()
        if not person:
            raise PersonNotFoundError(f"{self.label}未找到")

        inspiration_count = await self.count_all_inspirations(db, person_id)
        if inspiration_count > 0:
            raise PersonHasInspirationsError(
                f"该{self.label}下仍有 {inspiration_count} 个素材（含垃圾桶素材）关联，无法删除"
            )

        # 先收集照片文件路径（DB 级联删除后无法再拿到），提交成功后再物理删除；
        # 头像文件同样收集，避免删除后残留孤儿文件
        photo_paths: list[str] = []
        if self.has_photo_sets:
            for photo_set in person.photo_sets:
                for photo in photo_set.photos:
                    photo_paths.extend([photo.file_path, photo.thumbnail_path])
        if person.avatar_path:
            photo_paths.append(person.avatar_path)

        await db.delete(person)
        await db.commit()

        # 记录审计：删除人物属破坏性操作（级联删除照片组/照片），留痕便于追溯
        await record_audit_log(
            action="delete_person",
            target_type=self.label,
            count=1,
            detail=f"删除{self.label} {person.name}（级联删除照片组与照片记录）",
        )

        if photo_paths:
            delete_files_counting(*photo_paths)

    async def count_all_inspirations(self, db: AsyncSession, person_id: int) -> int:
        """统计主体的全部关联素材数（**含软删除的垃圾桶素材**）。

        仅供删除前校验使用：删除级联会物理删除全部关联行（含垃圾桶素材的），
        因此校验口径必须覆盖全部关联，与展示口径（count_inspirations 排除
        软删除）区分开。
        """
        assert self.link_model is not None
        link_model = self.link_model
        link_id = self._link_id_col()
        result = await db.execute(
            select(func.count(link_model.inspiration_id)).where(
                link_id == person_id,
            )
        )
        return result.scalar() or 0

    # ── 素材统计 / 列表 / 风格画像 ──

    async def count_inspirations(self, db: AsyncSession, person_id: int) -> int:
        """统计主体的有效素材数（排除软删除素材）。"""
        assert self.link_model is not None
        link_model = self.link_model
        link_id = self._link_id_col()
        result = await db.execute(
            select(func.count(link_model.inspiration_id))
            .join(Inspiration, Inspiration.id == link_model.inspiration_id)
            .where(
                link_id == person_id,
                Inspiration.deleted_at.is_(None),
            )
        )
        return result.scalar() or 0

    async def list_inspirations(
        self, db: AsyncSession, person_id: int, page: int, size: int, sort: str
    ) -> dict | None:
        """获取主体的素材列表（含分页与统计）。主体不存在返回 None。"""
        assert self.model is not None and self.link_model is not None
        model, link_model = self.model, self.link_model
        link_id = self._link_id_col()

        person = await db.get(model, person_id)
        if not person:
            return None

        # 统计总数（排除软删除素材）
        count_result = await db.execute(
            select(func.count())
            .select_from(link_model)
            .join(Inspiration, Inspiration.id == link_model.inspiration_id)
            .where(link_id == person_id, Inspiration.deleted_at.is_(None))
        )
        total = count_result.scalar() or 0

        # 分页获取素材 — 只查需要的列，避免 Inspiration 的 selectin 预加载
        stmt = (
            select(
                Inspiration.id,
                Inspiration.file_path,
                Inspiration.thumbnail_path,
                Inspiration.media_type,
                Inspiration.created_at,
                link_model.confidence,
            )
            .join(Inspiration, link_model.inspiration_id == Inspiration.id)
            .where(link_id == person_id, Inspiration.deleted_at.is_(None))
            .order_by(
                link_model.confidence.desc()
                if sort == "confidence"
                else Inspiration.created_at.asc()
                if sort == "oldest"
                else Inspiration.created_at.desc()
            )
            .offset((page - 1) * size)
            .limit(size)
        )
        rows = (await db.execute(stmt)).all()

        items = [
            {
                "inspiration_id": row[0],
                "file_path": row[1],
                "thumbnail_path": row[2],
                "media_type": row[3],
                "confidence": round(row[5], 2) if row[5] else 0,
                "created_at": str(row[4]) if row[4] else None,
            }
            for row in rows
        ]

        return {
            "person": {
                "id": person.id,
                "name": person.name,
                "platform": person.platform,
            },
            "items": items,
            "total": total,
            "page": page,
            "size": size,
        }

    async def style_profile(self, db: AsyncSession, person_id: int) -> dict:
        """主体风格画像：聚合其所有有效素材的标签频次/类别分布/时间趋势。"""
        assert self.link_model is not None
        link_model = self.link_model
        link_id = self._link_id_col()

        # 该主体的有效素材 id 子查询
        insp_ids_subq = (
            select(link_model.inspiration_id)
            .join(Inspiration, Inspiration.id == link_model.inspiration_id)
            .where(link_id == person_id, Inspiration.deleted_at.is_(None))
        )

        # top_tags：素材标签聚合
        top_result = await db.execute(
            select(
                Tag.id,
                Tag.name,
                Tag.category,
                func.count().label("cnt"),
            )
            .join(InspirationTag, InspirationTag.tag_id == Tag.id)
            .where(InspirationTag.inspiration_id.in_(insp_ids_subq))
            .group_by(Tag.id)
            .order_by(func.count().desc(), Tag.name.asc())
            .limit(STYLE_PROFILE_TOP_TAGS)
        )
        top_tags = [
            {"tag_id": r[0], "name": r[1], "category": r[2], "count": r[3]}
            for r in top_result.all()
        ]

        # by_category：按标签类别聚合
        cat_result = await db.execute(
            select(Tag.category, func.count().label("cnt"))
            .join(InspirationTag, InspirationTag.tag_id == Tag.id)
            .where(InspirationTag.inspiration_id.in_(insp_ids_subq))
            .group_by(Tag.category)
            .order_by(func.count().desc())
        )
        by_category = {r[0]: r[1] for r in cat_result.all()}

        # trend：按素材创建时间月分桶
        trend_result = await db.execute(
            select(
                func.strftime("%Y-%m", Inspiration.created_at).label("bucket"),
                func.count().label("cnt"),
            )
            .join(link_model, link_model.inspiration_id == Inspiration.id)
            .where(link_id == person_id, Inspiration.deleted_at.is_(None))
            .group_by("bucket")
            .order_by("bucket")
        )
        trend = [{"bucket": r[0], "count": r[1]} for r in trend_result.all()]

        return {"top_tags": top_tags, "by_category": by_category, "trend": trend}

    async def top(self, db: AsyncSession, limit: int) -> list[dict]:
        """按素材数倒序返回排行。"""
        assert self.model is not None and self.link_model is not None
        model, link_model = self.model, self.link_model
        link_id = self._link_id_col()
        count_subq = (
            select(
                link_id.label("pid"),
                func.count(link_model.inspiration_id).label("cnt"),
            )
            .join(Inspiration, Inspiration.id == link_model.inspiration_id)
            .where(Inspiration.deleted_at.is_(None))
            .group_by(link_id)
            .subquery()
        )
        result = await db.execute(
            select(model, func.coalesce(count_subq.c.cnt, 0).label("cnt"))
            .outerjoin(count_subq, model.id == count_subq.c.pid)
            .order_by(func.coalesce(count_subq.c.cnt, 0).desc(), model.id.desc())
            .limit(limit)
        )
        return [self._to_dict(p, cnt) for p, cnt in result.all()]

    async def suggest(self, db: AsyncSession, name: str, limit: int = 10) -> list[dict]:
        """按名称模糊匹配主体（用于前端选择去重）。"""
        assert self.model is not None
        result = await db.execute(
            select(self.model)
            .where(self.model.name.contains(name.strip()))
            .order_by(self.model.name.asc())
            .limit(limit)
        )
        return [self._to_dict(p) for p in result.scalars().all()]

    async def get_or_create(
        self,
        db: AsyncSession,
        name: str,
        platform: str = "other",
    ) -> Blogger | Model | None:
        """按 name + platform 查或建主体（供采集器/导入用）。

        与 get_or_create_tag 的差异：name 不唯一，无法依赖唯一约束做并发安全；
        命中多条（同名多人）时返回 None，由调用方显式传入 ID 以免误关联。
        """
        assert self.model is not None
        name = name.strip()
        if not name:
            return None
        result = await db.execute(
            select(self.model).where(
                self.model.name == name, self.model.platform == platform
            )
        )
        rows = result.scalars().all()
        if len(rows) == 1:
            return rows[0]
        if len(rows) > 1:
            logger.warning(
                f"{self.label}名 '{name}'（平台 {platform}）存在多条记录，跳过自动关联，"
                "请调用方显式指定 ID"
            )
            return None
        person = self.model(name=name, platform=platform, source="ai_generated")
        db.add(person)
        await db.flush()
        return person

    # ── 素材关联 ──

    async def link(
        self,
        db: AsyncSession,
        inspiration_id: str,
        person_id: int,
        confidence: float = 1.0,
    ):
        """建立单条素材-主体关联（幂等，已存在跳过）。

        内部兼容 wrapper：委托给批量版 link_batch。素材或主体不存在返回 None。
        """
        result = await self.link_batch(
            db, inspiration_id, [person_id], confidence=confidence
        )
        return result["links"][0] if result["links"] else None

    async def link_batch(
        self,
        db: AsyncSession,
        inspiration_id: str,
        person_ids: list[int],
        confidence: float = 1.0,
    ) -> dict:
        """批量建立素材-主体关联（幂等，已存在跳过），减少 N+1 查询。

        返回:
            {"links": [...], "missing_ids": [...], "skipped": int,
             "inspiration_exists": bool}
        """
        assert self.model is not None and self.link_model is not None
        model, link_model = self.model, self.link_model
        link_id = self._link_id_col()
        person_ids = list(dict.fromkeys(person_ids))  # 去重保序
        links = []

        inspiration = await db.get(Inspiration, inspiration_id)
        if not inspiration:
            return {
                "links": links,
                "missing_ids": person_ids,
                "skipped": 0,
                "inspiration_exists": False,
            }

        # 一次查出所有候选主体，缺失的记入 missing_ids
        persons_result = await db.execute(
            select(model).where(model.id.in_(person_ids))
        )
        persons = {p.id: p for p in persons_result.scalars().all()}
        missing_ids = [pid for pid in person_ids if pid not in persons]

        # 一次查出已存在的关联对，跳过
        existing_result = await db.execute(
            select(link_model.inspiration_id, link_id).where(
                link_model.inspiration_id == inspiration_id,
                link_id.in_(persons.keys()),
            )
        )
        existing_pairs = {(r[0], r[1]) for r in existing_result.all()}

        skipped = 0
        for pid in persons:
            if (inspiration_id, pid) in existing_pairs:
                skipped += 1
                continue
            link = link_model(
                inspiration_id=inspiration_id, confidence=confidence
            )
            setattr(link, self.link_id_field, pid)
            # 预绑定已查到的对象，避免路由层访问 link.<entity> 时触发懒加载
            setattr(link, self.link_entity_attr, persons[pid])
            db.add(link)
            try:
                # SAVEPOINT 隔离插入：并发建立同一关联时后者触发 IntegrityError，
                # 回滚后重查并复用已存在的关联，避免 500。
                async with db.begin_nested():
                    await db.flush()
            except IntegrityError:
                db.expunge(link)
                existing = await db.execute(
                    select(link_model).where(
                        link_model.inspiration_id == inspiration_id,
                        link_id == pid,
                    )
                )
                existing_link = existing.scalar_one_or_none()
                if existing_link:
                    skipped += 1
                    continue
                raise
            links.append(link)

        return {
            "links": links,
            "missing_ids": missing_ids,
            "skipped": skipped,
            "inspiration_exists": True,
        }

    async def unlink(self, db: AsyncSession, inspiration_id: str, person_id: int) -> bool:
        """解除素材-主体关联，返回是否解除成功。"""
        assert self.link_model is not None
        result = await db.execute(
            delete(self.link_model).where(
                self.link_model.inspiration_id == inspiration_id,
                self._link_id_col() == person_id,
            )
        )
        return result.rowcount > 0

    # ── 模特照片组（仅模特服务暴露路由）──

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


class BloggerService(PersonServiceBase):
    """穿搭博主服务。"""

    model = Blogger
    link_model = InspirationBlogger
    link_id_field = "blogger_id"
    link_entity_attr = "blogger"
    label = "博主"

    async def import_from_csv(self, db: AsyncSession, file: UploadFile) -> dict:
        """从 CSV 批量导入博主（按 xhs_id upsert），返回导入统计。

        CSV 要求:
            - 编码 UTF-8（自动去除 BOM）
            - 表头含 ``nickname`` 与 ``xhs_id``（必填），``ip_location`` 可选；
              列顺序不限，按表头名称匹配（大小写/首尾空白容错）
            - nickname 与 xhs_id 非空，ip_location 可为空
            - xhs_id 已存在 → 更新昵称与 IP 属地（upsert，避免重复导入）
            - CSV 文件内重复的 xhs_id 合并为一行（后出现者覆盖，计入 skipped）

        返回:
            {"imported": 新增数, "updated": 更新数, "skipped": 跳过数,
             "failed": 失败行数, "errors": [{"row", "nickname", "reason"}, ...]}
        """
        import csv
        import io

        raw = await file.read()
        try:
            text = raw.decode("utf-8-sig")  # utf-8-sig 自动去除 BOM
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=400, detail="文件编码不是 UTF-8，请转换为 UTF-8 后重试"
            )

        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise HTTPException(status_code=400, detail="CSV 文件为空或缺少表头")

        # 表头名规范化：去首尾空白 + 小写，兼容 nickname/NickName/昵称等变体
        header_map: dict[str, str] = {}
        for h in reader.fieldnames:
            if h is None:
                continue
            key = h.strip().lower()
            if key and key not in header_map:
                header_map[key] = h.strip()
        for required in ("nickname", "xhs_id"):
            if required not in header_map:
                raise HTTPException(status_code=400, detail=f"CSV 缺少必填列：{required}")

        errors: list[dict] = []
        # 合法行按 xhs_id 合并（CSV 内重复 → 后出现者覆盖昵称/IP）
        merged: dict[str, dict] = {}
        duplicate_in_file = 0
        row_no = 0  # 数据行号（表头为第 0 行，数据从 1 起）
        for row in reader:
            row_no += 1
            # 行键规范化：DictReader 的键是原始表头（不自动 strip），
            # 表头带首尾空白时直接 row.get("nickname") 会恒为 None——
            # 统一按 strip+lower 后的键读取，兑现「首尾空白容错」声明
            norm_row = {
                (k.strip().lower() if k else ""): v for k, v in row.items()
            }
            nickname = (norm_row.get("nickname") or "").strip()
            xhs_id = (norm_row.get("xhs_id") or "").strip()
            ip_location = (norm_row.get("ip_location") or "").strip()

            if not nickname:
                errors.append({"row": row_no, "nickname": None, "reason": "昵称为空"})
                continue
            if not xhs_id:
                errors.append({"row": row_no, "nickname": nickname, "reason": "小红书号为空"})
                continue
            if len(xhs_id) > 64:
                errors.append(
                    {"row": row_no, "nickname": nickname, "reason": "小红书号超过 64 字符"}
                )
                continue

            if xhs_id in merged:
                duplicate_in_file += 1  # CSV 内重复：保留后出现者
            merged[xhs_id] = {
                "nickname": nickname,
                "ip_location": ip_location,
                "row": row_no,
            }

        # 批量查库：一次取出所有已存在的 xhs_id，避免逐行查询（N+1）
        existing_result = await db.execute(
            select(Blogger).where(Blogger.xhs_id.in_(list(merged.keys())))
        )
        existing_map = {p.xhs_id: p for p in existing_result.scalars().all()}

        imported = 0
        updated = 0
        for xhs_id, entry in merged.items():
            person = existing_map.get(xhs_id)
            new_person: Blogger | None = None
            try:
                if person:
                    # upsert：更新昵称与 IP 属地（小红书号本身不变）
                    person.name = entry["nickname"]
                    person.ip_location = entry["ip_location"] or None
                    updated += 1
                else:
                    new_person = Blogger(
                        name=entry["nickname"],
                        platform="xiaohongshu",
                        xhs_id=xhs_id,
                        ip_location=entry["ip_location"] or None,
                        source="manual",
                    )
                    db.add(new_person)
                    imported += 1
                async with db.begin_nested():
                    await db.flush()
            except IntegrityError:
                # SAVEPOINT 已回滚：新建对象仍在 pending，移除避免后续 commit 重复插入
                if new_person is not None:
                    db.expunge(new_person)
                # 并发下同一 xhs_id 已被其它请求插入：重查后按「更新」处理
                retry = (
                    await db.execute(select(Blogger).where(Blogger.xhs_id == xhs_id))
                ).scalar_one_or_none()
                if retry:
                    retry.name = entry["nickname"]
                    retry.ip_location = entry["ip_location"] or None
                    if imported > 0:
                        imported -= 1
                    updated += 1
                else:
                    errors.append(
                        {
                            "row": entry["row"],
                            "nickname": entry["nickname"],
                            "reason": "导入冲突",
                        }
                    )

        await db.commit()

        return {
            "imported": imported,
            "updated": updated,
            "skipped": duplicate_in_file,
            "failed": len(errors),
            "errors": errors[:_IMPORT_ERROR_LIMIT],
        }


class ModelService(PersonServiceBase):
    """职业模特服务（含写真照片组）。"""

    model = Model
    link_model = InspirationModel
    link_id_field = "model_id"
    link_entity_attr = "model"
    photo_set_model = ModelPhotoSet
    photo_model = ModelPhoto
    has_photo_sets = True
    label = "模特"


blogger_service = BloggerService()
model_service = ModelService()
