"""标签别名服务测试（list / create / delete + 冲突与并发回滚）。

为什么值得测：别名决定「AI 识别到的同义词归到哪个主标签」。写错了不会报错，
只会让 AI 打的标签**静默分流成两个标签**，之后统计与搜索全都对不上。
"""

import pytest
from sqlalchemy import select

from app.database import async_session
from app.services.tag_crud import TagConflictError, TagNotFoundError
from app.models.tag import Tag, TagAlias
from app.services.tag_alias import create_alias, delete_alias, list_aliases


async def _tag(name: str, category: str = "item") -> int:
    async with async_session() as db:
        tag = Tag(name=name, category=category, source="seed")
        db.add(tag)
        await db.commit()
        return tag.id


async def test_create_and_list(client):
    tag_id = await _tag("针织开衫")
    async with async_session() as db:
        created = await create_alias(db, tag_id, "毛衣外搭")
        assert created.alias == "毛衣外搭"
        await db.commit()

        rows = await list_aliases(db)

    assert any(r["alias"] == "毛衣外搭" and r["tag_id"] == tag_id for r in rows)


async def test_unknown_tag_raises(client):
    async with async_session() as db:
        with pytest.raises(TagNotFoundError):
            await create_alias(db, 999999, "任意")


async def test_alias_conflicts_with_existing_tag_name(client):
    """别名不能与任何主标签同名，否则 AI 归一化时产生歧义。"""
    tag_id = await _tag("阔腿裤")
    await _tag("直筒裤")

    async with async_session() as db:
        with pytest.raises(TagConflictError) as exc:
            await create_alias(db, tag_id, "直筒裤")
        assert "同名" in str(exc.value)


async def test_duplicate_alias_conflicts(client):
    tag_id = await _tag("米色")
    async with async_session() as db:
        await create_alias(db, tag_id, "奶白")
        await db.commit()

        with pytest.raises(TagConflictError) as exc:
            await create_alias(db, tag_id, "奶白")
        assert "已存在" in str(exc.value)


async def test_same_alias_on_other_tag_conflicts(client):
    """别名全局唯一：同一个别名不能挂到两个主标签上。"""
    first = await _tag("A 主标签")
    second = await _tag("B 主标签")

    async with async_session() as db:
        await create_alias(db, first, "共用别名")
        await db.commit()

        with pytest.raises(TagConflictError):
            await create_alias(db, second, "共用别名")


async def test_delete_alias(client):
    tag_id = await _tag("待删别名标签")
    async with async_session() as db:
        created = await create_alias(db, tag_id, "临时别名")
        await db.commit()
        alias_id = created.id

    async with async_session() as db:
        assert await delete_alias(db, alias_id) is True
        await db.commit()

    async with async_session() as db:
        assert await delete_alias(db, alias_id) is False  # 已删
        assert (
            await db.execute(select(TagAlias).where(TagAlias.id == alias_id))
        ).scalar_one_or_none() is None
