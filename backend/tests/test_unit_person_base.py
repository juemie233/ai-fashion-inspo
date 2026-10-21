"""博主/模特通用服务的测试（PersonServiceBase：CRUD / 统计口径 / 删除保护 / 关联）。

这一层被博主与模特两套接口共用（子类只指定模型与关联表），所以它错了
是**两套功能一起错**。重点覆盖三处最容易出事的：

1. **删除保护**：有关联素材时禁止删除，且统计口径必须含垃圾桶素材
   （delete-orphan 级联会物理删除垃圾桶素材的关联行，恢复后人物信息就永久丢了）；
2. **唯一冲突降级**：xhs_id 重复要 409 而不是 500（并发导入很常见）；
3. **计数口径**：`count_all_inspirations`（含垃圾桶，供删除校验）与
   `count_inspirations`（排除垃圾桶，供展示）必须分开。
"""

import pytest

from app.database import async_session
from app.models.inspiration import Inspiration
from app.models.person import Blogger, InspirationBlogger, Model
from app.services.person.base import (
    PersonConflictError,
    PersonHasInspirationsError,
    PersonNotFoundError,
)
from app.services.person.services import BloggerService, ModelService


async def _insp(insp_id: str, *, deleted: bool = False) -> str:
    from datetime import datetime, timezone

    async with async_session() as db:
        db.add(
            Inspiration(
                id=insp_id,
                source_type="douyin",
                file_path=f"images/x/{insp_id}.jpg",
                media_type="image",
                quality_status="approved",
                deleted_at=datetime.now(timezone.utc) if deleted else None,
            )
        )
        await db.commit()
    return insp_id


async def _link(insp_id: str, blogger_id: int, confidence: float = 0.9) -> None:
    async with async_session() as db:
        db.add(
            InspirationBlogger(
                inspiration_id=insp_id, blogger_id=blogger_id, confidence=confidence
            )
        )
        await db.commit()


# ── CRUD ──


async def test_create_returns_dict_and_strips_name(client):
    async with async_session() as db:
        created = await BloggerService().create(db, "  测试博主  ", platform="douyin")
        await db.commit()

    assert created["name"] == "测试博主"
    assert created["source"] == "manual"
    assert created["inspiration_count"] == 0


async def test_create_rejects_blank_name(client):
    async with async_session() as db:
        with pytest.raises(PersonConflictError) as exc:
            await BloggerService().create(db, "   ")
    assert "名称不能为空" in str(exc.value)


async def test_create_xhs_id_conflict_returns_409_semantics(client):
    """xhs_id 唯一：重复时抛 PersonConflictError（路由映射 409），不是 500。"""
    async with async_session() as db:
        await BloggerService().create(db, "第一个", xhs_id="xhs-1")
        await db.commit()

        with pytest.raises(PersonConflictError) as exc:
            await BloggerService().create(db, "第二个", xhs_id="xhs-1")
        assert "小红书号" in str(exc.value)


async def test_get_and_404(client):
    async with async_session() as db:
        created = await BloggerService().create(db, "取用博主")
        await db.commit()
        person = await BloggerService().get(db, created["id"])
        assert person.name == "取用博主"

        with pytest.raises(PersonNotFoundError):
            await BloggerService().get(db, 999999)


async def test_update_fields_and_validation(client):
    async with async_session() as db:
        created = await BloggerService().create(db, "待改博主")
        await db.commit()

        updated = await BloggerService().update(
            db, created["id"], {"name": " 改后博主 ", "bio": "简介"}
        )
        assert updated["name"] == "改后博主"
        assert updated["bio"] == "简介"

        with pytest.raises(PersonConflictError):
            await BloggerService().update(db, created["id"], {"name": "   "})


async def test_update_xhs_conflict(client):
    async with async_session() as db:
        first = await BloggerService().create(db, "A", xhs_id="dup-1")
        second = await BloggerService().create(db, "B")
        await db.commit()

        with pytest.raises(PersonConflictError):
            await BloggerService().update(db, second["id"], {"xhs_id": "dup-1"})
        assert first["id"] != second["id"]


# ── 删除保护 ──


async def test_delete_blocked_by_any_link_including_trash(client):
    """垃圾桶素材也算关联：只按有效素材校验会让恢复后的素材丢失人物信息。"""
    async with async_session() as db:
        created = await BloggerService().create(db, "有关联博主")
        await db.commit()
        await _insp("del1", deleted=True)
        await _link("del1", created["id"])

        with pytest.raises(PersonHasInspirationsError):
            await BloggerService().delete(db, created["id"])


async def test_delete_ok_without_links(client):
    async with async_session() as db:
        created = await BloggerService().create(db, "无关联博主")
        await db.commit()

        await BloggerService().delete(db, created["id"])
        await db.commit()

        with pytest.raises(PersonNotFoundError):
            await BloggerService().get(db, created["id"])


async def test_delete_missing_404(client):
    async with async_session() as db:
        with pytest.raises(PersonNotFoundError):
            await BloggerService().delete(db, 999999)


# ── 计数口径 ──


async def test_count_口径_all_vs_live(client):
    async with async_session() as db:
        created = await BloggerService().create(db, "计数博主")
        await db.commit()
        await _insp("cnt1")
        await _insp("cnt2", deleted=True)
        await _link("cnt1", created["id"])
        await _link("cnt2", created["id"])

        service = BloggerService()
        all_count = await service.count_all_inspirations(db, created["id"])
        live_count = await service.count_inspirations(db, created["id"])

    assert all_count == 2   # 删除校验口径：含垃圾桶
    assert live_count == 1  # 展示口径：排除垃圾桶


# ── 素材列表 ──


async def test_list_inspirations_paging_and_sorts(client):
    async with async_session() as db:
        created = await BloggerService().create(db, "列表博主")
        await db.commit()
        await _insp("li1")
        await _insp("li2")
        await _link("li1", created["id"], confidence=0.5)
        await _link("li2", created["id"], confidence=0.95)

        data = await BloggerService().list_inspirations(
            db, created["id"], page=1, size=10, sort="confidence"
        )

    assert data["total"] == 2
    assert data["person"]["name"] == "列表博主"
    assert data["items"][0]["inspiration_id"] == "li2"  # confidence 降序
    assert data["items"][0]["confidence"] == 0.95


async def test_list_inspirations_returns_none_for_unknown(client):
    async with async_session() as db:
        assert await BloggerService().list_inspirations(db, 999999, 1, 10, "newest") is None


async def test_list_inspirations_excludes_trash(client):
    async with async_session() as db:
        created = await BloggerService().create(db, "垃圾桶列表博主")
        await db.commit()
        await _insp("lt1")
        await _insp("lt2", deleted=True)
        await _link("lt1", created["id"])
        await _link("lt2", created["id"])

        data = await BloggerService().list_inspirations(
            db, created["id"], page=1, size=10, sort="newest"
        )

    assert data["total"] == 1
    assert [i["inspiration_id"] for i in data["items"]] == ["lt1"]


# ── 列表筛选 / 排序 ──


async def test_list_items_pagination_and_search(client):
    async with async_session() as db:
        await BloggerService().create(db, "筛选博主甲", platform="douyin")
        await BloggerService().create(db, "筛选博主乙", platform="xiaohongshu")
        await db.commit()

        items, total = await BloggerService().list_items(
            db, page=1, size=10, search="筛选博主", platform="douyin"
        )

    assert total == 1
    assert items[0]["platform"] == "douyin"


async def test_list_items_sorts(client):
    async with async_session() as db:
        await BloggerService().create(db, "排序博主B")
        await BloggerService().create(db, "排序博主A")
        await db.commit()

        items, _total = await BloggerService().list_items(
            db, page=1, size=10, search="排序博主", sort="name", order="asc"
        )

    names = [i["name"] for i in items]
    assert names == sorted(names)


# ── 平台统计 / 建议 / 置顶 ──


async def test_platform_stats(client):
    async with async_session() as db:
        await BloggerService().create(db, "统计博主", platform="douyin")
        await db.commit()

        stats = await BloggerService().platform_stats(db)

    assert isinstance(stats, (dict, list))


async def test_suggest_by_prefix(client):
    async with async_session() as db:
        await BloggerService().create(db, "建议博主")
        await db.commit()

        suggested = await BloggerService().suggest(db, "建议")

    assert any(item["name"] == "建议博主" for item in suggested)


async def test_top_by_inspiration_count(client):
    async with async_session() as db:
        created = await BloggerService().create(db, "置顶博主")
        await db.commit()
        await _insp("top1")
        await _link("top1", created["id"])

        top = await BloggerService().top(db, limit=10)

    assert any(item["name"] == "置顶博主" for item in top)


async def test_ip_location_stats(client):
    async with async_session() as db:
        await BloggerService().create(db, "属地博主", ip_location="浙江")
        await db.commit()

        stats = await BloggerService().ip_location_stats(db, limit=10)

    assert isinstance(stats, (dict, list))


# ── 关联 ──


async def test_link_and_unlink(client):
    async with async_session() as db:
        created = await BloggerService().create(db, "关联博主")
        await db.commit()
        await _insp("lk1")

        await BloggerService().link(db, "lk1", created["id"])
        await db.commit()

        assert await BloggerService().count_inspirations(db, created["id"]) == 1

        removed = await BloggerService().unlink(db, "lk1", created["id"])
        await db.commit()
        assert removed is True
        assert await BloggerService().count_inspirations(db, created["id"]) == 0


async def test_get_or_create(client):
    """按 name+platform 复用；返回 ORM 对象（供调用方直接取 id 关联）。"""
    async with async_session() as db:
        first = await BloggerService().get_or_create(db, "自动博主")
        await db.commit()
        second = await BloggerService().get_or_create(db, "自动博主")
        await db.commit()

        assert first.id == second.id  # 幂等：同名复用
        assert second.source == "ai_generated"  # 自动建的来源与手工区分

        # 空名不建记录
        assert await BloggerService().get_or_create(db, "   ") is None


async def test_get_or_create_returns_none_for_ambiguous_name(client):
    """同名同平台有多条时返回 None，让调用方显式指定 ID（避免误关联）。"""
    async with async_session() as db:
        await BloggerService().create(db, "重名博主", platform="douyin")
        await BloggerService().create(db, "重名博主", platform="douyin")
        await db.commit()

        assert await BloggerService().get_or_create(db, "重名博主", "douyin") is None


# ── 模特子类：照片组相关字段存在性 ──


async def test_model_service_uses_model_table(client):
    async with async_session() as db:
        created = await ModelService().create(db, "测试模特")
        await db.commit()
        person = await ModelService().get(db, created["id"])

    assert isinstance(person, Model)
    assert created["name"] == "测试模特"


async def test_blogger_service_uses_blogger_table(client):
    async with async_session() as db:
        created = await BloggerService().create(db, "表类型博主")
        await db.commit()
        person = await BloggerService().get(db, created["id"])

    assert isinstance(person, Blogger)
