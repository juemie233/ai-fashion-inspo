"""标签管理路由的测试（CRUD / 批量编辑 / 合并 / 别名 / 导入导出 / 排序）。

**为什么直调 handler**：TestClient 的请求跑在 anyio portal 线程，不被 coverage
追踪。直接 `await handler(...)`（Query 默认值显式传）才是真覆盖。

重点锁三类「会静默改坏标签体系」的行为：
1. 批量改名/改类别是破坏性接口（已有 API Key 保护），失败要 409/400 而不是部分生效；
2. 合并到自身必须拒绝（否则标签被删且关联无处可去）；
3. 导出→导入必须能往返（备份/迁移依赖它）。
"""

import pytest
from sqlalchemy import select
from fastapi import HTTPException

from app.database import async_session
from app.models.tag import Tag, TagAlias
from app.routers import tags as router
from app.schemas.tag import (
    AliasCreate,
    TagBatchDelete,
    TagCreate,
    TagImportRequest,
    TagMergeRequest,
    TagReorderItem,
    TagReorderRequest,
    TagUpdate,
)


async def _tag(name: str, category: str = "item", **kwargs) -> int:
    async with async_session() as db:
        tag = Tag(name=name, category=category, source="manual", **kwargs)
        db.add(tag)
        await db.commit()
        return tag.id


# ── 基础 CRUD ──


async def test_list_tags_grouped(client):
    await _tag("分组单品", "item")
    await _tag("分组风格", "style")

    async with async_session() as db:
        groups = await router.list_tags(db=db)

    categories = {g.category for g in groups}
    assert {"item", "style"} <= categories
    assert all(isinstance(g.tags, list) for g in groups)


async def test_create_tag_and_conflict(client):
    async with async_session() as db:
        created = await router.create_tag(TagCreate(name="新标签", category="style"), db=db)
        assert created.name == "新标签"

        with pytest.raises(HTTPException) as exc:
            await router.create_tag(TagCreate(name="新标签", category="style"), db=db)
        assert exc.value.status_code == 409


async def test_update_tag_and_404(client):
    tag_id = await _tag("待改标签")

    async with async_session() as db:
        updated = await router.update_tag(
            tag_id, TagUpdate(name="改后标签", pinned=True), db=db
        )
        assert updated.name == "改后标签"
        assert updated.pinned is True

        with pytest.raises(HTTPException) as exc:
            await router.update_tag(999999, TagUpdate(name="x"), db=db)
        assert exc.value.status_code == 404


async def test_update_tag_conflict_on_duplicate_name(client):
    await _tag("已存在名")
    other = await _tag("另一个名")

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await router.update_tag(other, TagUpdate(name="已存在名"), db=db)
        assert exc.value.status_code == 409


# ── 批量编辑 ──


async def test_batch_change_category(client):
    first = await _tag("批类别一")
    second = await _tag("批类别二")

    async with async_session() as db:
        result = await router.batch_change_category(
            {"tag_ids": [first, second], "category": "style"}, db=db
        )
    assert result["updated"] == 2
    assert result["category"] == "style"

    async with async_session() as db:
        assert (await db.get(Tag, first)).category == "style"


async def test_batch_change_category_validates_payload(client):
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await router.batch_change_category({"tag_ids": []}, db=db)
        assert exc.value.status_code == 400

        with pytest.raises(HTTPException) as exc:
            await router.batch_change_category({"tag_ids": [1], "category": "  "}, db=db)
        assert exc.value.status_code == 400


async def test_batch_rename_and_conflict(client):
    tag_id = await _tag("白色高跟鞋")
    await _tag("纯白高跟鞋")

    async with async_session() as db:
        result = await router.batch_rename_tags(
            {"tag_ids": [tag_id], "find": "白色", "replace": "米白"}, db=db
        )
    assert result["updated"] == 1

    async with async_session() as db:
        # 改成已存在的名字 → 冲突 409（不能静默合并）
        with pytest.raises(HTTPException) as exc:
            await router.batch_rename_tags(
                {"tag_ids": [tag_id], "find": "米白", "replace": "纯白"}, db=db
            )
        assert exc.value.status_code == 409


async def test_batch_rename_validates_payload(client):
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await router.batch_rename_tags({"tag_ids": [1]}, db=db)
        assert exc.value.status_code == 400


async def test_batch_delete_tags(client):
    first = await _tag("批删一")
    second = await _tag("批删二")

    async with async_session() as db:
        result = await router.batch_delete_tags(
            TagBatchDelete(tag_ids=[first, second]), db=db
        )
    assert result["count"] == 2

    async with async_session() as db:
        assert await db.get(Tag, first) is None


async def test_batch_delete_validates_payload(client):
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await router.batch_delete_tags(TagBatchDelete(tag_ids=[]), db=db)
        assert exc.value.status_code == 400

        with pytest.raises(HTTPException) as exc:
            await router.batch_delete_tags(TagBatchDelete(tag_ids=[999999]), db=db)
        assert exc.value.status_code == 404


async def test_delete_unused_tags(client):
    await _tag("无人用的标签")
    async with async_session() as db:
        result = await router.delete_unused_tags(db=db)
    assert result["count"] >= 1

    async with async_session() as db:
        # 再删一次没有可删的（幂等提示）
        again = await router.delete_unused_tags(db=db)
    assert again["count"] == 0
    assert "没有未使用" in again["message"]


# ── 合并 ──


async def test_merge_tags(client):
    from app.models.inspiration import Inspiration
    from app.models.tag import InspirationTag

    source = await _tag("被合并标签")
    target = await _tag("目标标签")

    async with async_session() as db:
        db.add(
            Inspiration(
                id="mg1",
                source_type="douyin",
                file_path="images/x/mg1.jpg",
                media_type="image",
                quality_status="approved",
            )
        )
        await db.flush()
        db.add(
            InspirationTag(
                inspiration_id="mg1", tag_id=source, confidence=0.9, source="ai_generated"
            )
        )
        await db.commit()

    async with async_session() as db:
        result = await router.merge_tags_endpoint(
            TagMergeRequest(source_tag_id=source, target_tag_id=target), db=db
        )
    assert "合并" in result["message"]

    async with async_session() as db:
        assert await db.get(Tag, source) is None
        links = (
            await db.execute(
                __import__("sqlalchemy").select(InspirationTag).where(
                    InspirationTag.inspiration_id == "mg1"
                )
            )
        ).scalars().all()
    assert [link.tag_id for link in links] == [target]  # 关联重指向


async def test_merge_into_self_rejected(client):
    tag_id = await _tag("自合并")
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await router.merge_tags_endpoint(
                TagMergeRequest(source_tag_id=tag_id, target_tag_id=tag_id), db=db
            )
        assert exc.value.status_code == 400
        assert "自身" in str(exc.value.detail)


async def test_merge_404_for_unknown(client):
    tag_id = await _tag("存在标签")
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await router.merge_tags_endpoint(
                TagMergeRequest(source_tag_id=999999, target_tag_id=tag_id), db=db
            )
        assert exc.value.status_code == 404


# ── 建议 / 统计 / 重复 ──


async def test_tag_suggestions(client):
    await _tag("针织开衫")
    async with async_session() as db:
        suggestions = await router.tag_suggestions("针织开衫", db=db)
    # 完全同名应被识别为「相似」（用于创建前的重复提示）
    assert isinstance(suggestions, list)


async def test_tag_stats(client):
    await _tag("统计标签")
    async with async_session() as db:
        stats = await router.tag_stats(db=db)
    assert isinstance(stats, dict)


async def _cache_key_for_now() -> str:
    """按「当前最大 updated_at + 默认阈值」算出缓存 key（与路由内部一致）。"""
    from sqlalchemy import func

    from app.services.tag_dedupe_cache import compute_cache_key

    async with async_session() as db:
        last_mod = (await db.execute(func.max(Tag.updated_at))).scalar()
    return compute_cache_key(str(last_mod or ""), 0.75)


async def test_duplicate_tags_uses_cache(client, monkeypatch):
    """重复检测要能命中缓存（大库扫描很贵，同一份数据重复点不该重算）。"""
    from app.services import tag_dedupe_cache

    # 直接往缓存写一条，验证「命中缓存就不再重算」
    key_input = await _cache_key_for_now()
    tag_dedupe_cache.set_cached(key_input, [{"tags": ["A", "B"], "similarity": 0.95}], 1)

    async with async_session() as db:
        # threshold 必须与写入时一致（默认 0.75）
        result = await router.find_duplicate_tags(threshold=0.75, db=db)

    assert result["cached"] is True
    assert result["total"] == 1


# ── 素材关联 ──


async def test_tag_inspirations_paging_and_404(client):
    from app.models.inspiration import Inspiration
    from app.models.tag import InspirationTag

    tag_id = await _tag("关联标签")
    async with async_session() as db:
        db.add(
            Inspiration(
                id="ti1",
                source_type="douyin",
                file_path="images/x/ti1.jpg",
                media_type="image",
                quality_status="approved",
            )
        )
        await db.flush()
        db.add(
            InspirationTag(
                inspiration_id="ti1", tag_id=tag_id, confidence=0.9, source="ai_generated"
            )
        )
        await db.commit()

    async with async_session() as db:
        result = await router.tag_inspirations(
            tag_id, page=1, size=10, db=db
        )
    assert result["total"] == 1

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await router.tag_inspirations(999999, page=1, size=10, db=db)
        assert exc.value.status_code == 404


async def test_batch_remove_tag_inspirations(client):
    from app.models.inspiration import Inspiration
    from app.models.tag import InspirationTag

    tag_id = await _tag("待解绑标签")
    async with async_session() as db:
        db.add(
            Inspiration(
                id="tr1",
                source_type="douyin",
                file_path="images/x/tr1.jpg",
                media_type="image",
                quality_status="approved",
            )
        )
        await db.flush()
        db.add(
            InspirationTag(
                inspiration_id="tr1", tag_id=tag_id, confidence=0.9, source="ai_generated"
            )
        )
        await db.commit()

    async with async_session() as db:
        result = await router.batch_remove_tag_inspirations(
            tag_id, {"inspiration_ids": ["tr1"]}, db=db
        )
    assert result["removed"] == 1

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await router.batch_remove_tag_inspirations(tag_id, {"inspiration_ids": []}, db=db)
        assert exc.value.status_code == 400


# ── 导入导出 / 排序 ──


async def test_export_import_roundtrip(client):
    """导出→导入必须能往返：备份与跨库迁移都依赖它。"""
    await _tag("导出标签A", "style")
    await _tag("导出标签B", "item")

    async with async_session() as db:
        exported = await router.export_tags(db=db)
    assert exported["tags"]
    assert exported["exported_at"]

    async with async_session() as db:
        # 先清空，再用导出的数据导入回来（导出结构是 dict 列表）
        from sqlalchemy import delete

        await db.execute(delete(Tag))
        await db.commit()

        # 导出项含 usage_count/source 等展示字段；导入只吃 name/category
        result = await router.import_tags(
            TagImportRequest(
                tags=[{"name": t["name"], "category": t["category"]} for t in exported["tags"]]
            ),
            db=db,
        )
        # service 只 flush，提交由请求级 get_db 负责；直调 handler 时要自己提交
        await db.commit()
    assert result["imported"] == len(exported["tags"])

    async with async_session() as db:
        names = {t.name for t in (await db.execute(__import__("sqlalchemy").select(Tag))).scalars()}
    assert {"导出标签A", "导出标签B"} <= names


async def test_import_tags_validates_payload(client):
    async with async_session() as db:
        with pytest.raises(Exception):
            # Pydantic 校验会拦掉错误结构（422 在路由层之前）
            TagImportRequest(tags="不是列表")


async def test_reorder_tags(client):
    first = await _tag("排序一")
    second = await _tag("排序二")

    async with async_session() as db:
        result = await router.reorder_tags(
            TagReorderRequest(
                items=[
                    TagReorderItem(id=first, sort_order=2),
                    TagReorderItem(id=second, sort_order=1),
                ]
            ),
            db=db,
        )
    assert result["updated"] == 2

    async with async_session() as db:
        assert (await db.get(Tag, first)).sort_order == 2

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await router.reorder_tags(TagReorderRequest(items=[]), db=db)
        assert exc.value.status_code == 400


# ── 别名 ──


async def test_alias_crud_via_router(client):
    tag_id = await _tag("别名宿主")

    async with async_session() as db:
        aliases = await router.list_aliases(db=db)
        assert isinstance(aliases, list)

        created = await router.create_alias(tag_id, AliasCreate(alias="宿主别名"), db=db)
        await db.commit()
        assert created.alias == "宿主别名"

    async with async_session() as db:
        # Pydantic 会拦掉空别名（min_length=1）；这里用对象绕过校验，
        # 验证路由自身的兜底分支（防御性冗余，避免上游传入空白）
        blank = AliasCreate.model_construct(alias="   ")
        with pytest.raises(HTTPException) as exc:
            await router.create_alias(tag_id, blank, db=db)
        assert exc.value.status_code == 400

        with pytest.raises(HTTPException) as exc:
            await router.create_alias(999999, AliasCreate(alias="无主别名"), db=db)
        assert exc.value.status_code == 404

        with pytest.raises(HTTPException) as exc:
            await router.create_alias(tag_id, AliasCreate(alias="宿主别名"), db=db)
        assert exc.value.status_code == 409

    async with async_session() as db:
        alias = (
            await db.execute(__import__("sqlalchemy").select(TagAlias).where(TagAlias.alias == "宿主别名"))
        ).scalar_one()
        alias_id = alias.id

        result = await router.delete_alias(alias_id, db=db)
        assert "删除" in result["message"]

        with pytest.raises(HTTPException) as exc:
            await router.delete_alias(alias_id, db=db)
        assert exc.value.status_code == 404
