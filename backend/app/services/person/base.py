"""人物服务基座：异常类 + 博主/模特通用的 CRUD、列表、风格画像与素材关联。

模型与关联表由子类（BloggerService / ModelService）指定，本基类以
``self.model`` / ``self.link_model`` 等类属性参数化查询，避免两份重复实现。
"""

from __future__ import annotations

import logging

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.inspiration import Inspiration
from app.models.person import (
    Blogger,
    InspirationBlogger,
    InspirationModel,
    Model,
    ModelPhotoSet,
)
from app.models.tag import InspirationTag, Tag
from app.services.audit_service import record_audit_log
from app.services.file_service import delete_files_counting

logger = logging.getLogger(__name__)

# 风格画像 top_tags 数量上限
STYLE_PROFILE_TOP_TAGS = 15


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
        （照片组方法由 services/person/photo_sets.py 的 PhotoSetsMixin 提供）
    """

    model: type[Blogger] | type[Model] | None = None
    link_model: type[InspirationBlogger] | type[InspirationModel] | None = None
    link_id_field: str = ""
    link_entity_attr: str = ""
    photo_set_model: type[ModelPhotoSet] | None = None
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
            assert self.photo_set_model is not None
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

    async def batch_link_inspirations(
        self,
        db: AsyncSession,
        inspiration_ids: list[str],
        person_ids: list[int],
        confidence: float = 1.0,
    ) -> dict:
        """批量建立「多个素材 × 多个博主」关联（素材库批量选择场景）。

        逐素材复用 link_batch（幂等：已关联自动跳过；缺失的素材/博主计入
        not_found 静默跳过，不抛错），汇总整体统计。

        返回:
            {"linked": 新增关联数, "affected": 发生变更的素材数,
             "not_found_count": 不存在的素材数, "skipped": 已存在关联跳过数}
        """
        inspiration_ids = list(dict.fromkeys(inspiration_ids))
        person_ids = list(dict.fromkeys(person_ids))
        linked = 0
        affected = 0
        not_found = 0
        skipped = 0
        for inspiration_id in inspiration_ids:
            result = await self.link_batch(
                db, inspiration_id, person_ids, confidence=confidence
            )
            added = len(result["links"])
            if not result["inspiration_exists"]:
                not_found += 1
                continue
            linked += added
            skipped += result["skipped"]
            if added:
                affected += 1

        # 批量关联属批量写操作，按项目批量操作留痕惯例记录审计
        # （独立会话，失败不影响主流程）
        if linked > 0:
            try:
                await record_audit_log(
                    action="batch_link_bloggers",
                    target_type=self.label,
                    count=linked,
                    detail=(
                        f"{self.label} {len(person_ids)} 个 × 素材 "
                        f"{len(inspiration_ids)} 个（新增 {linked} 条关联，"
                        f"跳过已关联 {skipped} 条，缺失素材 {not_found} 个）"
                    ),
                )
            except Exception as e:
                logger.warning(f"写入批量关联审计失败（忽略）: {e}")

        return {
            "linked": linked,
            "affected": affected,
            "not_found_count": not_found,
            "skipped": skipped,
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
