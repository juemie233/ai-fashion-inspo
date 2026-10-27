"""人物服务基座：异常类 + 博主/模特通用的 CRUD、列表、风格画像与素材关联。

模型与关联表由子类（BloggerService / ModelService）指定，本基类以
``self.model`` / ``self.link_model`` 等类属性参数化查询，避免两份重复实现。
"""

from __future__ import annotations

import contextlib
import logging
from datetime import datetime

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

    def _to_dict(
        self,
        person: Blogger | Model,
        inspiration_count: int = 0,
        last_inspiration_at: datetime | None = None,
    ) -> dict:
        """将主体 ORM 对象转为响应字典（含素材数统计与最近素材时间）。"""
        return {
            "id": person.id,
            "name": person.name,
            "platform": person.platform,
            "platform_user_id": person.platform_user_id,
            "xhs_id": person.xhs_id,
            "ip_location": person.ip_location,
            # 是否已注册人脸特征（face_embedding 非空）：人脸检测只匹配库内人物。
            # 用 __dict__.get 避免同步序列化触发懒加载 IO（MissingGreenlet）；
            # 列表查询已 selectinload 预加载，未预加载路径（create/update/top/suggest）
            # 无该字段时按 False 处理（前端仅人物列表消费此列）
            "face_registered": person.__dict__.get("face_embedding") is not None,
            "profile_url": person.profile_url,
            "avatar_path": person.avatar_path,
            "bio": person.bio,
            # 按人人脸匹配阈值（None = 前端显示为「跟随全局」）
            "face_match_threshold": getattr(person, "face_match_threshold", None),
            "source": person.source,
            "created_at": person.created_at,
            "updated_at": person.updated_at,
            "inspiration_count": inspiration_count,
            # 最近一次入库的素材时间（None = 还没有素材）：判断博主是否仍在更新，
            # 不用点进详情。与素材数同一子查询算出，不额外多一次往返
            "last_inspiration_at": last_inspiration_at,
            # 人物组（方案 B）：博主有值（null=独立账号）；模特恒为 None
            "person_group_id": getattr(person, "person_group_id", None),
            # 平台统计快照（仅博客表有这些列；模特返回 None）
            "follower_count": getattr(person, "follower_count", None),
            "following_count": getattr(person, "following_count", None),
            "aweme_count": getattr(person, "aweme_count", None),
            "total_favorited": getattr(person, "total_favorited", None),
            "bio_source": getattr(person, "bio_source", None),
            "stats_synced_at": getattr(person, "stats_synced_at", None),
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
        source: str | None = None,
        sort: str = "newest",
        order: str = "desc",
        face_registered_only: bool = False,
        grouped: bool = False,
    ) -> tuple[list[dict], int]:
        """分页查询（含素材数统计与多维筛选），返回 (items, total)。

        人脸检测筛选约束：
        - face_registered_only=true：仅保留已注册人脸库的人物（确保人脸检测
          只匹配候选人脸库内的人）

        人物组折叠（方案 B，仅穿搭博主）：
        - grouped=true 时按 person_group_id 折叠：同组只返回一条主记录
          （素材数最多者），组内其余账号放 ``group_members`` 数组，附
          ``group_platforms`` 供前端展示多平台徽标；
        - 折叠仅在「不指定平台」时生效：按平台筛选是单平台视角，保持平铺
          （同组该平台账号各自显示，但仍带 person_group_id 便于前端提示）；
        - 折叠后分页/排序以主记录为准。
        """
        assert self.model is not None and self.link_model is not None
        model, link_model = self.model, self.link_model
        link_id = self._link_id_col()

        # 素材数 / 最近素材时间统计子查询：人物 -> 有效素材（排除软删除素材）
        count_subq = (
            select(
                link_id.label("pid"),
                func.count(link_model.inspiration_id).label("cnt"),
                func.max(Inspiration.created_at).label("last_at"),
            )
            .join(Inspiration, Inspiration.id == link_model.inspiration_id)
            .where(Inspiration.deleted_at.is_(None))
            .group_by(link_id)
            .subquery()
        )

        stmt = (
            select(
                model,
                func.coalesce(count_subq.c.cnt, 0).label("cnt"),
                count_subq.c.last_at,
            )
            .outerjoin(count_subq, model.id == count_subq.c.pid)
            # 预加载人脸特征 relationship：_to_dict 同步序列化需要（face_registered 列）
            .options(selectinload(model.face_embedding))
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
        # 来源筛选（manual 手动 / auto_collect 采集自动登记）：自动登记的博主动辄几百个，
        # 混在追踪名单里既看不清也点不过来，必须能按来源分开看
        if source:
            stmt = stmt.where(model.source == source)

        # 过滤：仅保留已注册人脸库的人物（人脸检测只匹配库内人物）
        # face_embedding 是**关系**（特征在独立的一对一表里），关系属性不支持
        # isnot(None)（SQLAlchemy 会抛 NotImplementedError → 接口 500）；
        # 用 has() 生成 EXISTS 子查询，等价于「该人物有特征行」。
        if face_registered_only:
            stmt = stmt.where(model.face_embedding.has())

        # 人物组折叠：仅穿搭博主且不指定平台时生效（平铺分页交给 _list_grouped）
        if grouped and self.model is Blogger and not platform:
            return await self._list_grouped(db, stmt, sort, order, page, size)

        # 排序：newest（创建时间倒序）| name（按名称）| count（按素材数，order 控制升/降）
        if sort == "name":
            stmt = stmt.order_by(model.name.asc(), model.id.desc())
        elif sort == "count":
            # 素材数排序：order 控制方向；同数时用 id 兜底，保证分页稳定
            cnt_col = func.coalesce(count_subq.c.cnt, 0)
            if order == "asc":
                stmt = stmt.order_by(cnt_col.asc(), model.id.asc())
            else:
                stmt = stmt.order_by(cnt_col.desc(), model.id.desc())
        else:
            stmt = stmt.order_by(model.created_at.desc(), model.id.desc())

        # 统计总数（不含分页）
        count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total = (await db.execute(count_stmt)).scalar() or 0

        stmt = stmt.offset((page - 1) * size).limit(size)
        rows = (await db.execute(stmt)).all()
        items = [self._to_dict(p, cnt, last_at) for p, cnt, last_at in rows]
        return items, total

    async def _list_grouped(
        self,
        db: AsyncSession,
        stmt,
        sort: str,
        order: str,
        page: int,
        size: int,
    ) -> tuple[list[dict], int]:
        """人物组折叠列表：同组只返回主记录，分页以主记录为准。

        主账号确定规则：组手动指定（primary_blogger_id）优先；
        否则素材数最多者；同数取 id 较小者（先建）。
        折叠后按主记录在内存排序/分页（SQL 排序对折叠列表无意义）。
        """
        rows = (await db.execute(stmt)).all()

        # 收集有组的 id → 组手动主账号（一次查出全部组）
        from app.models.person import PersonGroup

        group_ids = {
            person.person_group_id
            for person, _cnt, _last in rows
            if person.person_group_id is not None
        }
        manual_primary: dict[int, int] = {}
        if group_ids:
            group_rows = await db.execute(
                select(PersonGroup.id, PersonGroup.primary_blogger_id).where(
                    PersonGroup.id.in_(group_ids)
                )
            )
            manual_primary = {
                gid: pid
                for gid, pid in group_rows.all()
                if pid is not None
            }

        # 按组折叠：主记录 = 手动指定优先，否则素材数最多（同数取 id 小者）
        groups: dict[int | None, list[tuple]] = {}
        for person, cnt, last_at in rows:
            groups.setdefault(person.person_group_id, []).append((person, cnt, last_at))

        def _sort_key(item: tuple) -> tuple:
            # (素材数倒序, id 正序)：主账号 = 素材数最多者
            return (-item[1], item[0].id)

        folded: list[dict] = []
        for gid, members in groups.items():
            members.sort(key=_sort_key)
            if gid is None:
                # 独立账号：各自一行（不折叠）
                for person, cnt, last_at in members:
                    folded.append(self._to_dict(person, cnt, last_at))
                continue
            # 手动指定的主账号优先；否则素材数最多者
            manual = manual_primary.get(gid)
            if manual is not None:
                primary = next(
                    (m for m in members if m[0].id == manual), members[0]
                )
            else:
                primary = members[0]
            item = self._to_dict(primary[0], primary[1], primary[2])
            # 组内其余账号（含素材数），供前端展开显示
            item["group_members"] = [
                self._to_dict(m[0], m[1], m[2])
                for m in members
                if m[0].id != primary[0].id
            ]
            # 组内平台去重列表（多平台徽标）
            item["group_platforms"] = sorted(
                {m[0].platform for m in members}
            )
            folded.append(item)

        # 折叠后分页/排序以主记录为准
        total = len(folded)
        if sort == "name":
            folded.sort(key=lambda x: (x["name"], x["id"]))
        elif sort == "count":
            # 与平铺路径同一口径：order 控制方向，同数按 id 兜底
            folded.sort(
                key=lambda x: (x["inspiration_count"], x["id"])
                if order == "asc"
                else (-x["inspiration_count"], x["id"])
            )
        else:
            folded.sort(
                key=lambda x: (
                    x["created_at"] is not None,
                    x["created_at"],
                    x["id"],
                ),
                reverse=True,
            )
        start = (page - 1) * size
        return folded[start : start + size], total

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
            # SAVEPOINT 回滚后对象可能**已被移出 session**，此时 expunge 会抛
            # InvalidRequestError，把本该是 409 的冲突变成 500。这里容错清理。
            with contextlib.suppress(Exception):
                db.expunge(person)
            raise PersonConflictError(
                f"{self.label}的小红书号（xhs_id）已被其它记录占用"
            ) from None
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
            ) from None
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

    def _inspiration_filter_clauses(self, filters: dict):
        """把素材筛选条件翻译成 SQL 条件列表（人设无关，博主/模特共用）。"""
        from app.models.face import InspirationFaceDetection

        clauses = []
        if filters.get("media_type"):
            clauses.append(Inspiration.media_type == filters["media_type"])
        if filters.get("quality_status"):
            clauses.append(Inspiration.quality_status == filters["quality_status"])
        if filters.get("rating_min"):
            clauses.append(Inspiration.rating >= filters["rating_min"])
        if filters.get("favorite"):
            clauses.append(Inspiration.is_favorite.is_(True))
        if filters.get("tag_name"):
            from app.models.tag import InspirationTag, Tag

            tag_subq = (
                select(InspirationTag.inspiration_id)
                .join(Tag, Tag.id == InspirationTag.tag_id)
                .where(Tag.name == filters["tag_name"])
            )
            clauses.append(Inspiration.id.in_(tag_subq))
        if filters.get("face_matched"):
            # 基类没有 kind 属性：用关联表外键名推导（blogger_id / model_id）
            person_kind = "blogger" if self.link_id_field == "blogger_id" else "model"
            person_col = (
                InspirationFaceDetection.matched_blogger_id
                if person_kind == "blogger"
                else InspirationFaceDetection.matched_model_id
            )
            # 与「人脸候选」同一口径：只认人工确认过 / 历史单素材结果，排除被标记
            # 「不匹配」的检测——否则筛选会把 AI 候选、甚至复核时否掉的脸也算成已匹配
            face_subq = select(InspirationFaceDetection.inspiration_id).where(
                person_col == filters["person_id"],
                or_(
                    InspirationFaceDetection.match_status.is_(None),
                    InspirationFaceDetection.match_status == "confirmed",
                ),
                InspirationFaceDetection.match_excluded.is_not(True),
            )
            clauses.append(Inspiration.id.in_(face_subq))
        return clauses

    async def list_inspirations(
        self,
        db: AsyncSession,
        person_id: int,
        page: int,
        size: int,
        sort: str,
        *,
        media_type: str | None = None,
        quality_status: str | None = None,
        tag_name: str | None = None,
        rating_min: int | None = None,
        favorite: bool = False,
        face_matched: bool = False,
    ) -> dict | None:
        """获取主体的素材列表（分页 + 排序 + 筛选）。主体不存在返回 None。

        筛选为什么放在服务层：详情页素材区与「质量构成/标签」入口要共用同一口径，
        前端换筛选条件只需透传参数，不必自己拼多套请求。全部筛选走同一张关联表的
        IN 子查询，避免 join 造成分页重复。
        """
        assert self.model is not None and self.link_model is not None
        model, link_model = self.model, self.link_model
        link_id = self._link_id_col()

        person = await db.get(model, person_id)
        if not person:
            return None

        filters = {
            "person_id": person_id,
            "media_type": media_type,
            "quality_status": quality_status,
            "tag_name": tag_name,
            "rating_min": rating_min,
            "favorite": favorite,
            "face_matched": face_matched,
        }
        extra = self._inspiration_filter_clauses(filters)

        base_where = [link_id == person_id, Inspiration.deleted_at.is_(None), *extra]

        # 统计总数（排除软删除素材 + 筛选条件一致，否则分页分母与列表对不上）
        count_result = await db.execute(
            select(func.count())
            .select_from(link_model)
            .join(Inspiration, Inspiration.id == link_model.inspiration_id)
            .where(*base_where)
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
                Inspiration.quality_status,
                Inspiration.rating,
                Inspiration.is_favorite,
            )
            .join(Inspiration, link_model.inspiration_id == Inspiration.id)
            .where(*base_where)
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
                "quality_status": row[6],
                "rating": row[7],
                "is_favorite": bool(row[8]),
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

    async def list_works(
        self, db: AsyncSession, person_id: int, page: int, size: int
    ) -> dict | None:
        """按「作品」分组分页（B1）：同一抖音作品的 6~9 张图算一组。

        分组键是 ``inspirations.source_platform_id``（形如 ``f2:{aweme_id}#image3``）：
        取 ``#`` 之前的部分作为作品 ID，没有该字段的素材自成一组（``work_id=None``）。

        为什么要这个视图：9 位博主的素材量 500+（最大 3,426），逐张瀑布流翻不动；
        作品视图按「一次发布」的粒度看，才和她主页上的观感一致。

        实现：先在 SQL 里取该人物的**全部**素材的 (workkey, id)，在 Python 归组后
        分页——因为要同时给出「组数」与「组封面」，用窗口函数在 SQLite 里写会明显更慢
        且难读；单人物素材上限 3,426 条，内存归组是毫秒级。
        """
        assert self.model is not None and self.link_model is not None
        link_model = self.link_model
        link_id = self._link_id_col()

        person = await db.get(self.model, person_id)
        if not person:
            return None

        rows = (
            await db.execute(
                select(
                    Inspiration.id,
                    Inspiration.source_platform_id,
                    Inspiration.thumbnail_path,
                    Inspiration.file_path,
                    Inspiration.media_type,
                    Inspiration.created_at,
                )
                .join(link_model, link_model.inspiration_id == Inspiration.id)
                .where(link_id == person_id, Inspiration.deleted_at.is_(None))
                .order_by(Inspiration.created_at.desc())
            )
        ).all()

        groups: dict[str | None, dict] = {}
        for iid, platform_id, thumb, file_path, media_type, created_at in rows:
            work_id = None
            if platform_id:
                work_id = str(platform_id).split("#", 1)[0] or None
            # 组内预览项带上缩略图/原图路径：前端卡片直接铺小图、点击开灯箱，
            # 不必再为每个作品发一次请求（3426 条素材的博主有 3382 个作品组）。
            preview = {
                "inspiration_id": iid,
                "thumbnail_path": thumb,
                "file_path": file_path,
            }
            group = groups.get(work_id)
            if group is None:
                groups[work_id] = {
                    "work_id": work_id,
                    "count": 1,
                    "cover": preview,
                    "media_type": media_type,
                    "created_at": str(created_at) if created_at else None,
                    "items": [preview],
                }
            else:
                group["count"] += 1
                if len(group["items"]) < 12:  # 组内预览上限：一屏能看够就行
                    group["items"].append(preview)

        ordered = list(groups.values())
        total_works = len(ordered)
        start = (page - 1) * size
        return {
            "person": {"id": person.id, "name": person.name, "platform": person.platform},
            "works": ordered[start : start + size],
            "total": total_works,
            "total_items": len(rows),
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

    async def ip_location_stats(self, db: AsyncSession, limit: int = 30) -> dict:
        """按 IP 属地分组统计主体数量（空属地归入「未知」）。

        供人物管理页展示属地分布（如穿搭博主的地域画像）。

        返回:
            {"total": 主体总数, "items": [{"ip_location", "count"}, ...]}
            items 按数量降序、同名按属地排序，最多 limit 条。
        """
        assert self.model is not None
        model = self.model
        total = (await db.execute(select(func.count(model.id)))).scalar() or 0
        result = await db.execute(
            select(
                func.coalesce(model.ip_location, "未知").label("ip"),
                func.count(model.id).label("cnt"),
            )
            .group_by("ip")
            .order_by(func.count(model.id).desc(), "ip")
            .limit(limit)
        )
        items = [{"ip_location": r[0], "count": r[1]} for r in result.all()]
        return {"total": total, "items": items}

    async def platform_stats(self, db: AsyncSession) -> dict:
        """按平台分组统计主体数量（人物管理页顶部统计卡片用）。

        返回:
            {"total": 主体总数, "items": [{"platform", "count"}, ...]}
        """
        assert self.model is not None
        model = self.model
        total = (await db.execute(select(func.count(model.id)))).scalar() or 0
        result = await db.execute(
            select(model.platform, func.count(model.id).label("cnt")).group_by(
                model.platform
            )
        )
        items = [{"platform": r[0], "count": r[1]} for r in result.all()]
        return {"total": total, "items": items}

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
        pending = []
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
            pending.append(link)

        if pending:
            try:
                # 快路径：一次 flush 全部新增关联。逐条 SAVEPOINT + flush 在
                # 批量场景（200 素材 × 50 博主 = 1 万条）下耗时 40s+，超过
                # 前端 30s 超时导致「实际成功却提示失败」；合并为单事务批量
                # 插入后降到秒级。
                async with db.begin_nested():
                    await db.flush()
                links.extend(pending)
            except IntegrityError:
                # 并发冲突（多请求同时建立同一关联）慢路径：回滚后逐条重插，
                # 冲突条按已存在跳过，保证幂等且不 500。
                for link in pending:
                    db.expunge(link)
                for link in pending:
                    db.add(link)
                    try:
                        # SAVEPOINT 隔离插入：并发建立同一关联时后者触发
                        # IntegrityError，回滚后重查并复用已存在的关联。
                        async with db.begin_nested():
                            await db.flush()
                        links.append(link)
                    except IntegrityError:
                        db.expunge(link)
                        existing = await db.execute(
                            select(link_model).where(
                                link_model.inspiration_id == inspiration_id,
                                link_id == getattr(link, self.link_id_field),
                            )
                        )
                        existing_link = existing.scalar_one_or_none()
                        if existing_link:
                            skipped += 1
                            continue
                        raise

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
            # 先提交主事务释放 SQLite 写锁，再写审计：独立会话写审计会被
            # 本事务持有的写锁挡住，互等 30s 后审计超时失败（SQLite 单写者
            # 死锁），接口被拖慢到超过前端超时，造成「实际成功却提示失败」。
            # 与批量移垃圾桶 / 删除人物的审计时机一致（先 commit 再审计）。
            await db.commit()
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
