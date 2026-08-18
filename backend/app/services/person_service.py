"""人物服务：CRUD、风格画像聚合、人物-素材关联管理。

对标 tag_service.py 的写法与惯例；人物关联一律使用 person_id（不按名称匹配），
规避「同名多人」的歧义（详见人物模块移植报告 5.4）。
"""

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
    InspirationPerson,
    Person,
    PersonPhoto,
    PersonPhotoSet,
)
from app.models.tag import InspirationTag, Tag
from app.services.file_service import (
    delete_files,
    delete_files_counting,
    save_upload,
)
from app.utils.file_hash import file_sha256

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


def _to_person_dict(person: Person, inspiration_count: int = 0) -> dict:
    """将 Person ORM 对象转为响应字典（含素材数统计）。"""
    return {
        "id": person.id,
        "name": person.name,
        "person_type": person.person_type,
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
        # 搜索覆盖昵称 / 小红书号 / IP 属地（任一命中即匹配）
        kw = search.strip()
        stmt = stmt.where(
            or_(
                Person.name.contains(kw),
                Person.xhs_id.contains(kw),
                Person.ip_location.contains(kw),
            )
        )
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


# CSV 导入错误明细上限：避免超大文件撑爆响应体
_IMPORT_ERROR_LIMIT = 100


async def import_persons_from_csv(db: AsyncSession, file: UploadFile) -> dict:
    """从 CSV 批量导入人物（按 xhs_id upsert），返回导入统计。

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
    nick_col = header_map["nickname"]
    xhs_col = header_map["xhs_id"]
    ip_col = header_map.get("ip_location")

    errors: list[dict] = []
    # 合法行按 xhs_id 合并（CSV 内重复 → 后出现者覆盖昵称/IP）
    merged: dict[str, dict] = {}
    duplicate_in_file = 0
    row_no = 0  # 数据行号（表头为第 0 行，数据从 1 起）
    for row in reader:
        row_no += 1
        nickname = (row.get(nick_col) or "").strip()
        xhs_id = (row.get(xhs_col) or "").strip()
        ip_location = (row.get(ip_col) or "").strip() if ip_col else ""

        if not nickname:
            errors.append({"row": row_no, "nickname": None, "reason": "昵称为空"})
            continue
        if not xhs_id:
            errors.append({"row": row_no, "nickname": nickname, "reason": "小红书号为空"})
            continue
        if len(xhs_id) > 64:
            errors.append({"row": row_no, "nickname": nickname, "reason": "小红书号超过 64 字符"})
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
        select(Person).where(Person.xhs_id.in_(list(merged.keys())))
    )
    existing_map = {p.xhs_id: p for p in existing_result.scalars().all()}

    imported = 0
    updated = 0
    for xhs_id, entry in merged.items():
        person = existing_map.get(xhs_id)
        new_person: Person | None = None
        try:
            if person:
                # upsert：更新昵称与 IP 属地（小红书号本身不变）
                person.name = entry["nickname"]
                person.ip_location = entry["ip_location"] or None
                updated += 1
            else:
                new_person = Person(
                    name=entry["nickname"],
                    person_type="blogger",
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
                await db.execute(select(Person).where(Person.xhs_id == xhs_id))
            ).scalar_one_or_none()
            if retry:
                retry.name = entry["nickname"]
                retry.ip_location = entry["ip_location"] or None
                if imported > 0:
                    imported -= 1
                updated += 1
            else:
                errors.append(
                    {"row": entry["row"], "nickname": entry["nickname"], "reason": "导入冲突"}
                )

    await db.commit()

    return {
        "imported": imported,
        "updated": updated,
        "skipped": duplicate_in_file,
        "failed": len(errors),
        "errors": errors[:_IMPORT_ERROR_LIMIT],
    }


async def update_person(
    db: AsyncSession,
    person_id: int,
    payload: dict,
) -> dict:
    """更新人物字段并返回响应字典，不存在抛 PersonNotFoundError。

    参数:
        payload: 由 PersonUpdate.model_dump(exclude_unset=True) 得到的字段字典——
            未传的字段不在 dict 中（保持不变），显式传 None 的字段会被清空。
    """
    person = await get_person(db, person_id)
    for field, value in payload.items():
        if field == "name":
            value = value.strip() if value is not None else None
            if not value:
                raise PersonConflictError("人物名称不能为空")
        setattr(person, field, value)
    await db.flush()
    await db.refresh(person)
    return _to_person_dict(person)


async def delete_person(db: AsyncSession, person_id: int) -> None:
    """删除人物（仅允许无关联素材时删除），不存在抛 404。

    删除前校验该人物的有效素材数（与列表/详情页展示口径一致，排除软删除素材）：
    关联素材数 > 0 时抛 PersonHasInspirationsError，禁止删除以免误伤素材关联。
    通过校验后，除 inspiration_persons 关联外，还会级联删除该人物的照片组与
    照片记录；照片物理文件在提交成功后统一清理，避免「文件已删但事务失败」
    产生悬空记录。
    """
    result = await db.execute(
        select(Person)
        .options(selectinload(Person.photo_sets).selectinload(PersonPhotoSet.photos))
        .where(Person.id == person_id)
    )
    person = result.scalar_one_or_none()
    if not person:
        raise PersonNotFoundError("人物未找到")

    inspiration_count = await get_person_inspiration_count(db, person_id)
    if inspiration_count > 0:
        raise PersonHasInspirationsError(
            f"该模特下仍有 {inspiration_count} 个素材，无法删除"
        )

    # 先收集照片文件路径（DB 级联删除后无法再拿到），提交成功后再物理删除
    photo_paths: list[str] = []
    for photo_set in person.photo_sets:
        for photo in photo_set.photos:
            photo_paths.extend([photo.file_path, photo.thumbnail_path])

    await db.delete(person)
    await db.commit()

    if photo_paths:
        delete_files_counting(*photo_paths)


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
    """建立单条素材-人物关联（幂等，已存在跳过）。

    内部兼容 wrapper：委托给批量版 link_persons_batch（一次查询 + SAVEPOINT
    竞态保护），供采集器 / AI 识别等按单条关联的调用方使用。
    素材或人物不存在返回 None（由调用方决定是否抛 404）。
    """
    result = await link_persons_batch(
        db, inspiration_id, [person_id], confidence=confidence
    )
    return result["links"][0] if result["links"] else None


async def link_persons_batch(
    db: AsyncSession,
    inspiration_id: str,
    person_ids: list[int],
    confidence: float = 1.0,
) -> dict:
    """批量建立素材-人物关联（幂等，已存在跳过），减少 N+1 查询。

    对标 tag_service.batch_add_tags 的批量模式：一次校验素材与人物存在性、
    一次查询已有关联，再批量插入缺失关联；SAVEPOINT 隔离插入，
    并发建立同一关联时回滚后重查返回已存在的关联，避免 500。

    返回:
        {"links": [InspirationPerson, ...], "missing_ids": [...], "skipped": int,
         "inspiration_exists": bool}
        - links: 本次新增的关联对象列表
        - missing_ids: 不存在的人物 ID
        - skipped: 已存在而跳过的关联数
        - inspiration_exists: 素材是否存在（供路由层决定是否抛 404）
    """
    person_ids = list(dict.fromkeys(person_ids))  # 去重保序
    links: list[InspirationPerson] = []

    inspiration = await db.get(Inspiration, inspiration_id)
    if not inspiration:
        return {
            "links": links,
            "missing_ids": person_ids,
            "skipped": 0,
            "inspiration_exists": False,
        }

    # 一次查出所有候选人物，缺失的记入 missing_ids
    persons_result = await db.execute(
        select(Person).where(Person.id.in_(person_ids))
    )
    persons = {p.id: p for p in persons_result.scalars().all()}
    missing_ids = [pid for pid in person_ids if pid not in persons]

    # 一次查出已存在的关联对，跳过
    existing_result = await db.execute(
        select(InspirationPerson.inspiration_id, InspirationPerson.person_id).where(
            InspirationPerson.inspiration_id == inspiration_id,
            InspirationPerson.person_id.in_(persons.keys()),
        )
    )
    existing_pairs = {(r[0], r[1]) for r in existing_result.all()}

    skipped = 0
    for pid in persons:
        if (inspiration_id, pid) in existing_pairs:
            skipped += 1
            continue
        link = InspirationPerson(
            inspiration_id=inspiration_id, person_id=pid, confidence=confidence
        )
        # 预绑定已查到的 Person 对象，避免路由层访问 link.person 时触发懒加载
        link.person = persons[pid]
        db.add(link)
        try:
            # SAVEPOINT 隔离插入：并发建立同一关联时后者触发 IntegrityError，
            # 回滚后重查并复用已存在的关联，避免 500。
            async with db.begin_nested():
                await db.flush()
        except IntegrityError:
            db.expunge(link)
            existing = await db.execute(
                select(InspirationPerson).where(
                    InspirationPerson.inspiration_id == inspiration_id,
                    InspirationPerson.person_id == pid,
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


# ── 人物照片组（人物 → 照片组 → 照片）──


def _to_photo_set_dict(
    photo_set: PersonPhotoSet,
    photo_count: int = 0,
    cover_path: str | None = None,
) -> dict:
    """将 PersonPhotoSet ORM 对象转为响应字典（含照片数与封面）。"""
    return {
        "id": photo_set.id,
        "person_id": photo_set.person_id,
        "name": photo_set.name,
        "photo_count": photo_count,
        "cover_path": cover_path,
        "created_at": photo_set.created_at,
        "updated_at": photo_set.updated_at,
    }


def _to_photo_dict(photo: PersonPhoto) -> dict:
    """将 PersonPhoto ORM 对象转为响应字典。"""
    return {
        "id": photo.id,
        "set_id": photo.set_id,
        "file_path": photo.file_path,
        "thumbnail_path": photo.thumbnail_path,
        "sort_order": photo.sort_order,
        "created_at": photo.created_at,
    }


async def create_photo_set(
    db: AsyncSession, person_id: int, name: str | None = None
) -> dict:
    """创建人物照片组，返回响应字典；人物不存在抛 PersonNotFoundError。"""
    await get_person(db, person_id)
    resolved = (name or "").strip() or "未命名照片组"
    photo_set = PersonPhotoSet(person_id=person_id, name=resolved)
    db.add(photo_set)
    await db.flush()
    await db.refresh(photo_set)
    return _to_photo_set_dict(photo_set)


async def list_photo_sets(
    db: AsyncSession, person_id: int, page: int, size: int
) -> tuple[list[dict], int]:
    """分页查询人物照片组（含照片数与封面），人物不存在抛 PersonNotFoundError。"""
    person = await db.get(Person, person_id)
    if not person:
        raise PersonNotFoundError("人物未找到")

    # 每组照片数统计子查询
    count_subq = (
        select(
            PersonPhoto.set_id.label("sid"),
            func.count(PersonPhoto.id).label("cnt"),
        )
        .group_by(PersonPhoto.set_id)
        .subquery()
    )

    stmt = (
        select(PersonPhotoSet, func.coalesce(count_subq.c.cnt, 0).label("cnt"))
        .outerjoin(count_subq, PersonPhotoSet.id == count_subq.c.sid)
        .where(PersonPhotoSet.person_id == person_id)
        .order_by(PersonPhotoSet.created_at.desc(), PersonPhotoSet.id.desc())
    )

    total = (
        await db.execute(
            select(func.count()).select_from(
                select(PersonPhotoSet.id)
                .where(PersonPhotoSet.person_id == person_id)
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
                PersonPhoto.set_id,
                PersonPhoto.file_path,
                PersonPhoto.thumbnail_path,
            )
            .where(PersonPhoto.set_id.in_(set_ids))
            .order_by(PersonPhoto.set_id, PersonPhoto.sort_order, PersonPhoto.id)
        )
        seen: set[int] = set()
        for sid, file_path, thumb_path in cover_result.all():
            if sid not in seen:
                seen.add(sid)
                cover_map[sid] = thumb_path or file_path

    items = [
        _to_photo_set_dict(ps, cnt, cover_map.get(ps.id))
        for ps, cnt in rows
    ]
    return items, total


async def get_photo_set(db: AsyncSession, set_id: int) -> PersonPhotoSet:
    """按 ID 获取照片组，不存在抛 PersonNotFoundError。"""
    photo_set = await db.get(PersonPhotoSet, set_id)
    if not photo_set:
        raise PersonNotFoundError("照片组未找到")
    return photo_set


async def get_photo_set_cover(db: AsyncSession, set_id: int) -> str | None:
    """返回照片组的封面路径（组内第一条照片的缩略图或原图），无照片返回 None。"""
    row = (
        await db.execute(
            select(PersonPhoto.file_path, PersonPhoto.thumbnail_path)
            .where(PersonPhoto.set_id == set_id)
            .order_by(PersonPhoto.sort_order, PersonPhoto.id)
            .limit(1)
        )
    ).first()
    if not row:
        return None
    return row[1] or row[0]


async def update_photo_set(db: AsyncSession, set_id: int, name: str) -> dict:
    """更新照片组名称，返回响应字典；空名称抛 PersonConflictError。"""
    photo_set = await get_photo_set(db, set_id)
    resolved = (name or "").strip()
    if not resolved:
        raise PersonConflictError("照片组名称不能为空")
    photo_set.name = resolved
    await db.flush()
    await db.refresh(photo_set)
    return _to_photo_set_dict(photo_set)


async def add_photo_to_set(
    db: AsyncSession, set_id: int, file: UploadFile, sort_order: int = 0
) -> dict:
    """上传一张照片到照片组（含组内内容去重），返回响应字典。

    照片落盘到 person_photos/ 与 person_thumbnails/（与素材库 images/ 分离，
    避免被完整性检查误判为孤立文件）。
    """
    await get_photo_set(db, set_id)

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
            select(PersonPhoto.id).where(
                PersonPhoto.set_id == set_id,
                PersonPhoto.content_hash == content_hash,
            )
        )
        if dup.scalar_one_or_none():
            delete_files(file_path, thumb_path)
            raise HTTPException(status_code=409, detail="该照片已存在于该照片组（内容重复）")

    photo = PersonPhoto(
        set_id=set_id,
        file_path=file_path,
        thumbnail_path=thumb_path,
        content_hash=content_hash,
        sort_order=sort_order,
    )
    db.add(photo)
    await db.flush()
    await db.refresh(photo)
    return _to_photo_dict(photo)


async def list_set_photos(
    db: AsyncSession, set_id: int, page: int, size: int
) -> tuple[list[dict], int]:
    """分页查询照片组内的照片，返回 (items, total)；组不存在抛 PersonNotFoundError。"""
    await get_photo_set(db, set_id)
    total = (
        await db.execute(
            select(func.count()).select_from(PersonPhoto).where(
                PersonPhoto.set_id == set_id
            )
        )
    ).scalar() or 0

    rows = (
        await db.execute(
            select(PersonPhoto)
            .where(PersonPhoto.set_id == set_id)
            .order_by(PersonPhoto.sort_order, PersonPhoto.id)
            .offset((page - 1) * size)
            .limit(size)
        )
    ).scalars().all()
    return [_to_photo_dict(p) for p in rows], total


async def delete_photo_set(db: AsyncSession, set_id: int) -> None:
    """删除照片组（级联删除照片记录），提交成功后清理照片物理文件。"""
    result = await db.execute(
        select(PersonPhotoSet)
        .options(selectinload(PersonPhotoSet.photos))
        .where(PersonPhotoSet.id == set_id)
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
    db: AsyncSession, photo_id: int, set_id: int | None = None
) -> None:
    """删除单张照片（提交成功后清理物理文件），不存在抛 PersonNotFoundError。

    参数:
        set_id: 期望所属的照片组 ID。传入时校验照片归属（防跨组误删），
            归属不符与不存在同等对待（404，不泄露组间关系）。
    """
    photo = await db.get(PersonPhoto, photo_id)
    if not photo:
        raise PersonNotFoundError("照片未找到")
    if set_id is not None and photo.set_id != set_id:
        raise PersonNotFoundError("照片未找到")

    file_path, thumb_path = photo.file_path, photo.thumbnail_path
    await db.delete(photo)
    await db.commit()

    delete_files(file_path, thumb_path)
