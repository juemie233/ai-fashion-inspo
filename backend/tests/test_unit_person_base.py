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


# ── 人物组折叠（grouped 列表）──
#
# 方案 B 的「同一个人多平台」：同组只展示一行（主记录），组内其它账号折叠进
# group_members。主账号规则是「手动指定优先，否则素材数最多，同数取 id 小者」——
# 这条规则错了，用户会看到「点开详情却是另一个平台的号在当主」。

async def _blogger(name: str, platform: str = "other") -> int:
    """建一个**独立**账号（不属任何人物组）。"""
    from app.models.person import Blogger

    async with async_session() as db:
        b = Blogger(name=name, platform=platform, source="manual")
        db.add(b)
        await db.commit()
        return b.id


async def _group(name: str, *, primary_blogger_id: int | None = None) -> int:
    from app.models.person import PersonGroup

    async with async_session() as db:
        g = PersonGroup(name=name, primary_blogger_id=primary_blogger_id)
        db.add(g)
        await db.commit()
        return g.id


async def _blogger_in_group(name: str, platform: str, group_id: int) -> int:
    from app.models.person import Blogger

    async with async_session() as db:
        b = Blogger(name=name, platform=platform, source="manual", person_group_id=group_id)
        db.add(b)
        await db.commit()
        return b.id


async def _link_count(blogger_id: int, n: int, prefix: str) -> None:
    for i in range(n):
        await _insp(f"{prefix}{i}")
        await _link(f"{prefix}{i}", blogger_id)


async def test_grouped_list_folds_group_into_one_row(client):
    """同组多平台账号只占一行，其余进 group_members，并汇总 group_platforms。"""
    gid = await _group("同一个人")
    primary = await _blogger_in_group("主账号", "douyin", gid)
    secondary = await _blogger_in_group("副账号", "xiaohongshu", gid)
    await _link_count(primary, 3, "g1-")
    await _link_count(secondary, 1, "g2-")

    async with async_session() as db:
        items, total = await BloggerService().list_items(
            db, page=1, size=10, grouped=True
        )

    assert total == 1  # 折叠后只算一行
    row = items[0]
    assert row["id"] == primary  # 素材数最多的当主
    assert [m["id"] for m in row["group_members"]] == [secondary]
    assert row["group_platforms"] == ["douyin", "xiaohongshu"]
    assert row["inspiration_count"] == 3


async def test_grouped_list_prefers_manual_primary(client):
    """组里手动指定了主账号时，即使素材数更少也以它为主。"""
    gid = await _group("手动指定组")
    manual = await _blogger_in_group("手动主号", "douyin", gid)
    bigger = await _blogger_in_group("素材更多", "xiaohongshu", gid)
    await _link_count(bigger, 2, "g3-")

    # 先建组再把主账号指过去（组先建时还不知道 id）
    from app.models.person import PersonGroup

    async with async_session() as db:
        g = await db.get(PersonGroup, gid)
        g.primary_blogger_id = manual
        await db.commit()

    async with async_session() as db:
        items, _total = await BloggerService().list_items(db, page=1, size=10, grouped=True)

    assert items[0]["id"] == manual


async def test_grouped_list_keeps_standalone_accounts_separate(client):
    """没有组归属的账号各自一行，不参与折叠。"""
    await _blogger(name="独立博主甲")
    gid = await _group("成组")
    a = await _blogger_in_group("成组号", "douyin", gid)

    async with async_session() as db:
        items, total = await BloggerService().list_items(db, page=1, size=10, grouped=True)

    assert total == 2
    ids = {i["id"] for i in items}
    assert a in ids


async def test_grouped_list_paginates_after_folding(client):
    """分页以折叠后的主记录为准（否则一页可能只有 1 条、或同个人被拆到两页）。"""
    gid = await _group("大组")
    p1 = await _blogger_in_group("组员一", "douyin", gid)
    p2 = await _blogger_in_group("组员二", "xiaohongshu", gid)
    await _blogger(name="独立一号")
    await _blogger(name="独立二号")

    async with async_session() as db:
        page1, total = await BloggerService().list_items(db, page=1, size=2, grouped=True)
        page2, _ = await BloggerService().list_items(db, page=2, size=2, grouped=True)

    assert total == 3  # 一个折叠组 + 两个独立账号
    assert (len(page1), len(page2)) == (2, 1)  # 两页条数之和 = 折叠后的总数
    shown = {i["id"] for i in page1} | {i["id"] for i in page2}
    assert len(shown) == 3  # 无重复、无遗漏
    assert ({p1, p2} & shown)  # 组以主账号身份出现且只出现一次


async def test_grouped_list_sorts_by_count_and_name(client):
    gid = await _group("排序组")
    small = await _blogger_in_group("小号", "douyin", gid)
    await _blogger(name="AAA独立")
    await _link_count(small, 2, "g4-")

    async with async_session() as db:
        by_count, _ = await BloggerService().list_items(
            db, page=1, size=10, sort="count", order="desc", grouped=True
        )
        by_name, _ = await BloggerService().list_items(
            db, page=1, size=10, sort="name", order="asc", grouped=True
        )

    assert by_count[0]["inspiration_count"] >= by_count[-1]["inspiration_count"]
    assert [i["name"] for i in by_name] == sorted(i["name"] for i in by_name)


async def test_grouped_list_empty_library(client):
    async with async_session() as db:
        items, total = await BloggerService().list_items(db, page=1, size=10, grouped=True)
    assert (items, total) == ([], 0)


# ── 风格画像（style_profile）──
#
# 人物详情页的「风格画像」：聚合该主体所有**未删除**素材的标签频次、类别分布与月度趋势。
# 垃圾篓里的素材必须排除——否则用户把素材删了，画像还挂着它的标签。

async def _tag_link(insp_id: str, name: str, category: str) -> None:
    """给素材挂一个标签（直接写关联表，走的是画像查询真正读取的那张表）。"""
    from sqlalchemy import select

    from app.models.tag import InspirationTag, Tag

    async with async_session() as db:
        # 标签名唯一：复用已存在的标签（测试库里可能已被种子数据建过）
        tag = (
            await db.execute(select(Tag).where(Tag.name == name))
        ).scalar_one_or_none()
        if tag is None:
            tag = Tag(name=name, category=category, source="ai_generated")
            db.add(tag)
            await db.flush()
        db.add(InspirationTag(inspiration_id=insp_id, tag_id=tag.id, confidence=0.9))
        await db.commit()


async def test_style_profile_aggregates_tags_categories_and_trend(client):
    bid = await _blogger("画像博主")
    await _insp("sp1")
    await _insp("sp2")
    await _link("sp1", bid)
    await _link("sp2", bid)
    await _tag_link("sp1", "针织开衫", "item")
    await _tag_link("sp1", "极简", "style")
    await _tag_link("sp2", "针织开衫", "item")

    async with async_session() as db:
        profile = await BloggerService().style_profile(db, bid)

    assert profile["top_tags"][0]["name"] == "针织开衫"  # 出现最多的排第一
    assert profile["top_tags"][0]["count"] == 2
    assert profile["by_category"]["item"] == 2
    assert profile["by_category"]["style"] == 1
    assert len(profile["trend"]) >= 1
    assert sum(t["count"] for t in profile["trend"]) == 2  # 两个素材各计一次


async def test_style_profile_excludes_trashed_material(client):
    """垃圾桶素材不参与画像（否则删了素材画像还挂着它的标签）。"""
    bid = await _blogger("有垃圾素材的博主")
    await _insp("sp3")
    await _insp("sp4", deleted=True)
    await _link("sp3", bid)
    await _link("sp4", bid)
    await _tag_link("sp3", "阔腿裤", "item")
    await _tag_link("sp4", "已删标签", "item")

    async with async_session() as db:
        profile = await BloggerService().style_profile(db, bid)

    names = [t["name"] for t in profile["top_tags"]]
    assert "阔腿裤" in names
    assert "已删标签" not in names
    assert sum(t["count"] for t in profile["trend"]) == 1


async def test_style_profile_empty_for_person_without_material(client):
    bid = await _blogger("空博主")
    async with async_session() as db:
        profile = await BloggerService().style_profile(db, bid)
    assert profile == {"top_tags": [], "by_category": {}, "trend": []}


# ── 批量关联（batch_link_inspirations）──
#
# 「素材库多选 × 多个博主」是最常见的批量写操作，两条关键语义：
# 1. **幂等**：已关联的跳过、缺失的素材只计数不抛错（否则一次误点整个批量失败）；
# 2. **审计留痕**：有新增才写审计，且写审计前先 commit 主事务——SQLite 单写者，
#    不先释放写锁会互等 30s，接口超时「实际成功却提示失败」。

async def test_batch_link_counts_linked_affected_and_not_found(client):
    b1 = await _blogger("批量甲")
    b2 = await _blogger("批量乙")
    await _insp("bl1")
    await _insp("bl2")

    async with async_session() as db:
        result = await BloggerService().batch_link_inspirations(
            db, ["bl1", "bl2", "不存在的"], [b1, b2]
        )

    assert result["linked"] == 4  # 2 素材 × 2 博主
    assert result["affected"] == 2
    assert result["not_found_count"] == 1
    assert result["skipped"] == 0


async def test_batch_link_is_idempotent(client):
    bid = await _blogger("幂等博主")
    await _insp("bl3")

    async with async_session() as db:
        await BloggerService().batch_link_inspirations(db, ["bl3"], [bid])
        again = await BloggerService().batch_link_inspirations(db, ["bl3"], [bid])

    assert again["linked"] == 0
    assert again["affected"] == 0
    assert again["skipped"] == 1


async def test_batch_link_dedupes_input_ids(client):
    bid = await _blogger("去重博主")
    await _insp("bl4")

    async with async_session() as db:
        result = await BloggerService().batch_link_inspirations(
            db, ["bl4", "bl4"], [bid, bid]
        )

    assert result["linked"] == 1  # 重复入参不会产生两条关联


async def test_batch_link_writes_audit_only_when_linked(client):
    from app.models.person import Blogger
    from sqlalchemy import func, select

    from app.models.audit import AuditLog

    bid = await _blogger("审计博主")
    await _insp("bl5")

    async def _audit_count() -> int:
        async with async_session() as db:
            return (
                await db.execute(
                    select(func.count()).select_from(AuditLog).where(
                        AuditLog.action == "batch_link_bloggers"
                    )
                )
            ).scalar() or 0

    before = await _audit_count()
    async with async_session() as db:
        await BloggerService().batch_link_inspirations(db, ["bl5"], [bid])
    after = await _audit_count()
    assert after == before + 1  # 有新增 → 写审计

    # 第二次全部跳过（无新增）→ 不再写审计
    async with async_session() as db:
        await BloggerService().batch_link_inspirations(db, ["bl5"], [bid])
    assert await _audit_count() == after

    assert await _audit_count() >= 1


# ── 列表筛选/排序的边界分支 ──


async def test_list_filters_by_face_registered_only(client):
    """「仅看已注册人脸库」筛选：只保留有人脸特征的账号（人脸匹配只会命中库内人物）。"""
    from app.models.face import BloggerFaceEmbedding

    registered = await _blogger("已注册人脸")
    await _blogger("没注册人脸")
    async with async_session() as db:
        # 人脸特征是一对一关系表（不是 bloggers 表上的列），注册 = 该表有行
        db.add(BloggerFaceEmbedding(blogger_id=registered, embedding=b"\x00" * 2048))
        await db.commit()

    async with async_session() as db:
        items, total = await BloggerService().list_items(
            db, page=1, size=10, face_registered_only=True
        )

    assert total == 1
    assert items[0]["name"] == "已注册人脸"


async def test_list_filters_by_source(client):
    """来源筛选：手动登记与采集自动登记分开看（自动登记动辄几百个）。"""
    await _blogger("手动账号")
    from app.models.person import Blogger as B

    async with async_session() as db:
        db.add(B(name="采集账号", platform="douyin", source="auto_collect"))
        await db.commit()

    async with async_session() as db:
        manual, mtotal = await BloggerService().list_items(
            db, page=1, size=10, source="manual"
        )
        auto, atotal = await BloggerService().list_items(
            db, page=1, size=10, source="auto_collect"
        )

    assert [i["name"] for i in manual] == ["手动账号"]
    assert [i["name"] for i in auto] == ["采集账号"]
    assert (mtotal, atotal) == (1, 1)


async def test_list_count_sort_ascending(client):
    """素材数升序：order=asc 时少的在前（与降序互为反向）。"""
    many = await _blogger("素材多")
    few = await _blogger("素材少")
    await _link_count(many, 2, "cs-")

    async with async_session() as db:
        asc, _ = await BloggerService().list_items(
            db, page=1, size=10, sort="count", order="asc"
        )
        desc, _ = await BloggerService().list_items(
            db, page=1, size=10, sort="count", order="desc"
        )

    assert asc[0]["id"] == few
    assert desc[0]["id"] == many
    assert [i["id"] for i in asc] == [i["id"] for i in desc][::-1]


async def test_list_unknown_sort_falls_back_to_newest(client):
    """未知排序值回退到「最新创建」而不是报错（前端传错不该 500）。"""
    await _blogger("甲")
    await _blogger("乙")

    async with async_session() as db:
        items, total = await BloggerService().list_items(db, page=1, size=10, sort="不认识")

    assert total == 2
    assert len(items) == 2
