"""人物服务：CRUD、风格画像聚合、人物-素材关联管理。

对标 tag_service.py 的写法与惯例；人物关联一律使用 person_id（不按名称匹配），
规避「同名多人」的歧义（详见人物模块移植报告 5.4）。
"""

import logging

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inspiration import Inspiration
from app.models.person import InspirationPerson, Person
from app.models.tag import InspirationTag, Tag

logger = logging.getLogger(__name__)

# 风格画像 top_tags 数量上限
STYLE_PROFILE_TOP_TAGS = 15


class PersonNotFoundError(Exception):
    """人物或关联对象不存在（路由层转为 404）。"""

    def __init__(self, message: str = "人物未找到"):
        super().__init__(message)
        self.message = message


class PersonConflictError(Exception):
    """人物数据冲突（路由层转为 409）。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _to_person_dict(person: Person, inspiration_count: int = 0) -> dict:
    """将 Person ORM 对象转为响应字典（含素材数统计）。"""
    return {
        "id": person.id,
        "name": person.name,
        "person_type": person.person_type,
        "platform": person.platform,
        "platform_user_id": person.platform_user_id,
        "profile_url": person.profile_url,
        "avatar_path": person.avatar_path,
        "bio": person.bio,
        "source": person.source,
        "created_at": person.created_at,
        "updated_at": person.updated_at,
        "inspiration_count": inspiration_count,
    }


async def list_persons(
    db: AsyncSession,
    page: int,
    size: int,
    search: str | None = None,
    person_type: str | None = None,
    platform: str | None = None,
    sort: str = "newest",
) -> tuple[list[dict], int]:
    """人物分页查询（含素材数统计与多维筛选）。

    返回 (items, total)；items 为响应字典列表。
    """
    # 素材数统计子查询：人物 -> 有效素材关联数（排除软删除素材）
    count_subq = (
        select(
            InspirationPerson.person_id.label("pid"),
            func.count(InspirationPerson.inspiration_id).label("cnt"),
        )
        .join(Inspiration, Inspiration.id == InspirationPerson.inspiration_id)
        .where(Inspiration.deleted_at.is_(None))
        .group_by(InspirationPerson.person_id)
        .subquery()
    )

    stmt = (
        select(Person, func.coalesce(count_subq.c.cnt, 0).label("cnt"))
        .outerjoin(count_subq, Person.id == count_subq.c.pid)
    )
    if search:
        stmt = stmt.where(Person.name.contains(search.strip()))
    if person_type:
        stmt = stmt.where(Person.person_type == person_type)
    if platform:
        stmt = stmt.where(Person.platform == platform)

    # 排序：newest（创建时间倒序）| name（按名称）| count（按素材数倒序）
    if sort == "name":
        stmt = stmt.order_by(Person.name.asc(), Person.id.desc())
    elif sort == "count":
        stmt = stmt.order_by(func.coalesce(count_subq.c.cnt, 0).desc(), Person.id.desc())
    else:
        stmt = stmt.order_by(Person.created_at.desc(), Person.id.desc())

    # 统计总数（不含分页）
    count_stmt = select(func.count()).select_from(
        stmt.order_by(None).subquery()
    )
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = stmt.offset((page - 1) * size).limit(size)
    rows = (await db.execute(stmt)).all()
    items = [_to_person_dict(p, cnt) for p, cnt in rows]
    return items, total


async def get_person(db: AsyncSession, person_id: int) -> Person:
    """按 ID 获取人物，不存在抛 PersonNotFoundError。"""
    person = await db.get(Person, person_id)
    if not person:
        raise PersonNotFoundError("人物未找到")
    return person


async def create_person(
    db: AsyncSession,
    name: str,
    person_type: str = "blogger",
    platform: str = "other",
    platform_user_id: str | None = None,
    profile_url: str | None = None,
    avatar_path: str | None = None,
    bio: str | None = None,
) -> dict:
    """创建人物，返回响应字典。"""
    person = Person(
        name=name.strip(),
        person_type=person_type,
        platform=platform,
        platform_user_id=platform_user_id,
        profile_url=profile_url,
        avatar_path=avatar_path,
        bio=bio,
        source="manual",
    )
    db.add(person)
    await db.flush()
    await db.refresh(person)
    return _to_person_dict(person)


async def update_person(
    db: AsyncSession,
    person_id: int,
    name: str | None = None,
    person_type: str | None = None,
    platform: str | None = None,
    platform_user_id: str | None = None,
    profile_url: str | None = None,
    avatar_path: str | None = None,
    bio: str | None = None,
) -> dict:
    """更新人物字段并返回响应字典，不存在抛 PersonNotFoundError。"""
    person = await get_person(db, person_id)
    if name is not None:
        person.name = name.strip()
    if person_type is not None:
        person.person_type = person_type
    if platform is not None:
        person.platform = platform
    if platform_user_id is not None:
        person.platform_user_id = platform_user_id
    if profile_url is not None:
        person.profile_url = profile_url
    if avatar_path is not None:
        person.avatar_path = avatar_path
    if bio is not None:
        person.bio = bio
    await db.flush()
    await db.refresh(person)
    return _to_person_dict(person)


async def delete_person(db: AsyncSession, person_id: int) -> None:
    """删除人物（inspiration_persons 关联随外键级联删除），不存在抛 404。"""
    person = await get_person(db, person_id)
    await db.delete(person)
    await db.flush()


async def get_person_inspiration_count(db: AsyncSession, person_id: int) -> int:
    """统计人物的有效素材数（排除软删除素材）。"""
    result = await db.execute(
        select(func.count(InspirationPerson.inspiration_id))
        .join(Inspiration, Inspiration.id == InspirationPerson.inspiration_id)
        .where(
            InspirationPerson.person_id == person_id,
            Inspiration.deleted_at.is_(None),
        )
    )
    return result.scalar() or 0


async def list_person_inspirations(
    db: AsyncSession, person_id: int, page: int, size: int, sort: str
) -> dict | None:
    """获取人物的素材列表（含分页与统计）。人物不存在返回 None。

    对标 tag_service.list_tag_inspirations 的 join 查询写法。
    """
    person = await db.get(Person, person_id)
    if not person:
        return None

    # 统计总数（排除软删除素材）
    count_result = await db.execute(
        select(func.count())
        .select_from(InspirationPerson)
        .join(Inspiration, Inspiration.id == InspirationPerson.inspiration_id)
        .where(
            InspirationPerson.person_id == person_id,
            Inspiration.deleted_at.is_(None),
        )
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
            InspirationPerson.confidence,
        )
        .join(Inspiration, InspirationPerson.inspiration_id == Inspiration.id)
        .where(
            InspirationPerson.person_id == person_id,
            Inspiration.deleted_at.is_(None),
        )
        .order_by(
            InspirationPerson.confidence.desc()
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
            "person_type": person.person_type,
            "platform": person.platform,
        },
        "items": items,
        "total": total,
        "page": page,
        "size": size,
    }


async def get_person_style_profile(db: AsyncSession, person_id: int) -> dict:
    """人物风格画像：聚合该人物所有有效素材的标签。

    返回 {top_tags, by_category, trend}：
    - top_tags: 标签频次降序前 N 个
    - by_category: 按标签类别聚合的关联数
    - trend: 按素材 created_at 月分桶的数量
    """
    # 该人物的有效素材 id 子查询
    insp_ids_subq = (
        select(InspirationPerson.inspiration_id)
        .join(Inspiration, Inspiration.id == InspirationPerson.inspiration_id)
        .where(
            InspirationPerson.person_id == person_id,
            Inspiration.deleted_at.is_(None),
        )
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
        select(
            Tag.category,
            func.count().label("cnt"),
        )
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
        .join(InspirationPerson, InspirationPerson.inspiration_id == Inspiration.id)
        .where(
            InspirationPerson.person_id == person_id,
            Inspiration.deleted_at.is_(None),
        )
        .group_by("bucket")
        .order_by("bucket")
    )
    trend = [{"bucket": r[0], "count": r[1]} for r in trend_result.all()]

    return {"top_tags": top_tags, "by_category": by_category, "trend": trend}


async def top_persons(db: AsyncSession, limit: int) -> list[dict]:
    """按素材数倒序返回人物排行。"""
    count_subq = (
        select(
            InspirationPerson.person_id.label("pid"),
            func.count(InspirationPerson.inspiration_id).label("cnt"),
        )
        .join(Inspiration, Inspiration.id == InspirationPerson.inspiration_id)
        .where(Inspiration.deleted_at.is_(None))
        .group_by(InspirationPerson.person_id)
        .subquery()
    )
    result = await db.execute(
        select(Person, func.coalesce(count_subq.c.cnt, 0).label("cnt"))
        .outerjoin(count_subq, Person.id == count_subq.c.pid)
        .order_by(func.coalesce(count_subq.c.cnt, 0).desc(), Person.id.desc())
        .limit(limit)
    )
    return [_to_person_dict(p, cnt) for p, cnt in result.all()]


async def suggest_persons(db: AsyncSession, name: str, limit: int = 10) -> list[dict]:
    """按名称模糊匹配人物（用于前端选择去重）。"""
    result = await db.execute(
        select(Person)
        .where(Person.name.contains(name.strip()))
        .order_by(Person.name.asc())
        .limit(limit)
    )
    return [_to_person_dict(p) for p in result.scalars().all()]


async def get_or_create_person(
    db: AsyncSession,
    name: str,
    platform: str = "other",
    person_type: str = "blogger",
) -> Person | None:
    """按 name + platform 查或建人物（供采集器/导入用）。

    与 get_or_create_tag 的差异：Person.name 不唯一，无法依赖唯一约束做
    并发安全；命中多条（同名多人）时返回 None，由调用方显式传入 person_id
    以免误关联（详见移植报告 5.4）。
    """
    name = name.strip()
    if not name:
        return None
    result = await db.execute(
        select(Person).where(Person.name == name, Person.platform == platform)
    )
    rows = result.scalars().all()
    if len(rows) == 1:
        return rows[0]
    if len(rows) > 1:
        logger.warning(
            f"人物名 '{name}'（平台 {platform}）存在多条记录，跳过自动关联，"
            "请调用方显式指定 person_id"
        )
        return None
    person = Person(
        name=name, platform=platform, person_type=person_type, source="ai_generated"
    )
    db.add(person)
    await db.flush()
    return person


async def link_person(
    db: AsyncSession,
    inspiration_id: str,
    person_id: int,
    confidence: float = 1.0,
) -> InspirationPerson | None:
    """建立素材-人物关联（幂等，已存在跳过）。

    素材或人物不存在返回 None（由路由层决定是否抛 404）。
    """
    inspiration = await db.get(Inspiration, inspiration_id)
    person = await db.get(Person, person_id)
    if not inspiration or not person:
        return None

    existing = await db.execute(
        select(InspirationPerson).where(
            InspirationPerson.inspiration_id == inspiration_id,
            InspirationPerson.person_id == person_id,
        )
    )
    link = existing.scalar_one_or_none()
    if link:
        return link
    link = InspirationPerson(
        inspiration_id=inspiration_id, person_id=person_id, confidence=confidence
    )
    db.add(link)
    await db.flush()
    return link


async def unlink_person(
    db: AsyncSession, inspiration_id: str, person_id: int
) -> bool:
    """解除素材-人物关联，返回是否解除成功。"""
    result = await db.execute(
        delete(InspirationPerson).where(
            InspirationPerson.inspiration_id == inspiration_id,
            InspirationPerson.person_id == person_id,
        )
    )
    return result.rowcount > 0
