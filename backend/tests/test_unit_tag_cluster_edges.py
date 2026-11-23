"""标签自动聚类（tag_cluster）的直连边缘单测。

既有 tests/test_tag_cluster.py 走 API 覆盖了「扫描出组 / apply 合并+别名 / group_id
解析 / 缺源报错 / 空库」主路径；本文件直接 await 服务函数，补齐纯函数（归一化、
相似度、并查集）、共现加成（含垃圾桶排除）、扫描的过滤/分组/排序、apply 的
参数校验与单条失败汇总（合并失败 / 别名冲突 / 源与目标相同）。
"""

import pytest
from sqlalchemy import select

from app.database import async_session
from app.models.inspiration import Inspiration
from app.models.tag import InspirationTag, Tag
from app.services import tag_cluster as svc
from app.utils.time import utcnow


async def _tag(name: str, category: str = "free") -> Tag:
    async with async_session() as db:
        tag = Tag(name=name, category=category, source="ai_generated")
        db.add(tag)
        await db.commit()
        return tag


async def _insp(deleted: bool = False) -> Inspiration:
    async with async_session() as db:
        insp = Inspiration(
            file_path="images/cluster.jpg",
            media_type="image",
            deleted_at=utcnow() if deleted else None,
            **({"trash_reason": "其他", "trash_source": "manual"} if deleted else {}),
        )
        db.add(insp)
        await db.commit()
        return insp


async def _link(insp_id: str, tag_id: int) -> None:
    async with async_session() as db:
        db.add(InspirationTag(inspiration_id=insp_id, tag_id=tag_id))
        await db.commit()


# ═══════════════════════════════════════════════════════════════
#  纯函数：归一化 / 相似度 / 并查集
# ═══════════════════════════════════════════════════════════════


def test_normalize_for_compare_branches():
    """全角转半角、小写、去空白/标点；空串仍为空。"""
    assert svc._normalize_for_compare("ＡＢＣ１２３") == "abc123"
    assert svc._normalize_for_compare("  白色毛衣　") == "白色毛衣"
    assert svc._normalize_for_compare("白色☆毛衣!") == "白色毛衣"
    assert svc._normalize_for_compare("☆☆") == ""


def test_pair_similarity_containment_and_fallback(monkeypatch):
    """短名被长名包含且长度差 ≤4 → 0.8；更长差/其它情况回退编辑距离。"""
    assert svc._pair_similarity("", "白色") == 0.0
    assert svc._pair_similarity("白色", "") == 0.0
    assert svc._pair_similarity("白色", "白色长款外套") == 0.8  # 长度差 4
    assert svc._pair_similarity("白色长款外套", "白色") == 0.8  # 参数顺序无关

    monkeypatch.setattr(svc, "string_similarity", lambda _a, _b: 0.42)
    assert svc._pair_similarity("白色", "白色长款外套加长") == 0.42


def test_union_find_path_compression():
    uf = svc._UnionFind(5)
    uf.union(0, 1)
    uf.union(1, 2)
    uf.union(2, 3)
    assert uf.find(0) == uf.find(3)
    assert uf.find(4) == 4  # 未连通节点自成一类

    # 手工构造长链，验证 find 会把路径压扁
    uf2 = svc._UnionFind(4)
    uf2.parent = [1, 2, 3, 3]
    assert uf2.find(0) == 3
    assert uf2.parent[0] == 2  # 第一次跳两步压缩


# ═══════════════════════════════════════════════════════════════
#  共现加成
# ═══════════════════════════════════════════════════════════════


async def test_cooccurrence_boost_empty_pairs():
    async with async_session() as db:
        assert await svc._cooccurrence_boost(db, []) == []


async def test_cooccurrence_boost_counts_alive_only(client):
    """存活素材共现 ≥2 才 +0.1；垃圾桶素材不参与共现统计。"""
    a = await _tag("白色毛衣")
    b = await _tag("白色毛衫")
    c = await _tag("白色长裙")
    alive1 = await _insp()
    alive2 = await _insp()
    trashed = await _insp(deleted=True)
    # A/B 在两张存活素材共现；A/C 只有一张存活 + 一张垃圾桶
    for insp in (alive1, alive2):
        await _link(insp.id, a.id)
        await _link(insp.id, b.id)
    await _link(alive1.id, c.id)
    await _link(trashed.id, a.id)
    await _link(trashed.id, c.id)

    pairs = [(a.id, b.id, 0.6, "名称相似"), (a.id, c.id, 0.5, "名称相似")]
    async with async_session() as db:
        boosted = await svc._cooccurrence_boost(db, pairs)

    by_pair = {(x[0], x[1]): (x[2], x[3]) for x in boosted}
    assert by_pair[(a.id, b.id)] == (0.7, "名称相似（共现加成）")
    assert by_pair[(a.id, c.id)] == (0.5, "名称相似")


async def test_cooccurrence_boost_caps_at_one(client):
    a = await _tag("白色毛衣")
    b = await _tag("白色毛衫")
    for _ in range(2):
        insp = await _insp()
        await _link(insp.id, a.id)
        await _link(insp.id, b.id)

    async with async_session() as db:
        boosted = await svc._cooccurrence_boost(db, [(a.id, b.id, 0.95, "名称相似")])

    assert boosted[0][2] == 1.0


# ═══════════════════════════════════════════════════════════════
#  扫描：分组 / 排序 / 过滤
# ═══════════════════════════════════════════════════════════════


async def test_scan_clusters_empty_db(client):
    async with async_session() as db:
        result = await svc.scan_tag_clusters(db)

    assert result["total"] == 0
    assert result["groups"] == []
    assert result["threshold"] == 0.75


async def test_scan_clusters_groups_and_suggested_target(client):
    """归一化后同名成组；建议主标签 = 使用次数最高，成员按次数降序。"""
    main = await _tag("白色毛衣")
    variant = await _tag("白色毛衣☆")  # 归一化后与 main 一致
    for _ in range(2):
        insp = await _insp()
        await _link(insp.id, main.id)
    one = await _insp()
    await _link(one.id, variant.id)

    async with async_session() as db:
        result = await svc.scan_tag_clusters(db)
        too_small = await svc.scan_tag_clusters(db, min_group_size=3)

    assert result["total"] == 1
    group = result["groups"][0]
    assert group["id"] == "g1"
    assert group["suggested_target"]["id"] == main.id
    assert [m["id"] for m in group["members"]] == [main.id, variant.id]
    assert "相似度 1.00" in group["reason"]
    assert group["members"][0]["usage_count"] == 2
    assert too_small["total"] == 0 and too_small["groups"] == []


async def test_scan_clusters_category_isolation_and_first_char(client):
    """跨类别不比；同类别首字不同不比。"""
    await _tag("蓝色毛衣", "cat_a")
    await _tag("蓝色毛衣☆", "cat_b")  # 归一化同名但不同类别 → 不成组
    await _tag("红裙", "cat_c")
    await _tag("绿裙", "cat_c")  # 首字不同 → 不比

    async with async_session() as db:
        result = await svc.scan_tag_clusters(db)

    assert result["total"] == 0


async def test_scan_clusters_skips_empty_normalized_names(client):
    """归一化后为空的名称（纯符号）不参与比对，也不报错。"""
    await _tag("白色")
    await _tag("☆☆☆")  # 归一化后为空

    async with async_session() as db:
        result = await svc.scan_tag_clusters(db)

    assert result["total"] == 0


async def test_scan_clusters_reason_has_cooccurrence_suffix(client):
    """use_cooccurrence_boost 开关影响组理由文案（共现加成后缀）。"""
    a = await _tag("白色毛衣")
    b = await _tag("白色毛衣☆")
    for _ in range(2):
        insp = await _insp()
        await _link(insp.id, a.id)
        await _link(insp.id, b.id)

    async with async_session() as db:
        boosted = await svc.scan_tag_clusters(db, use_cooccurrence_boost=True)
        plain = await svc.scan_tag_clusters(db, use_cooccurrence_boost=False)

    assert "共现加成" in boosted["groups"][0]["reason"]
    assert "共现加成" not in plain["groups"][0]["reason"]
    assert boosted["groups"][0]["reason"].startswith("名称归一化后一致")


# ═══════════════════════════════════════════════════════════════
#  apply：校验 / 合并 / 别名 / 错误汇总
# ═══════════════════════════════════════════════════════════════


async def test_apply_tag_clusters_validation_errors(client):
    target = await _tag("白色")
    async with async_session() as db:
        result = await svc.apply_tag_clusters(
            db,
            [
                {"group_id": "g-missing-ids", "target_tag_id": None, "source_tag_ids": []},
                {"group_id": "g-no-target", "target_tag_id": 99999, "source_tag_ids": [target.id]},
                {
                    "group_id": "g-no-source",
                    "target_tag_id": target.id,
                    "source_tag_ids": [88888],
                },
            ],
            batch_id="batch-test",
        )

    assert result["applied"] == 0
    assert result["merged"] == 0
    messages = [e["message"] for e in result["errors"]]
    assert "缺少 target_tag_id 或 source_tag_ids" in messages
    assert "目标标签 99999 不存在" in messages
    assert "源标签不存在: [88888]" in messages


async def test_apply_tag_clusters_merges_without_alias(client):
    """keep_as_alias=False：合并源标签到目标，不产生别名。"""
    target = await _tag("白色")
    src1 = await _tag("白色毛衣")
    src2 = await _tag("白色毛衫")
    insp = await _insp()
    await _link(insp.id, src1.id)

    async with async_session() as db:
        result = await svc.apply_tag_clusters(
            db,
            [
                {
                    "group_id": "g1",
                    "target_tag_id": target.id,
                    "source_tag_ids": [src1.id, src2.id],
                    "keep_as_alias": False,
                }
            ],
            batch_id="batch-merge",
        )

    assert result == {
        "applied": 1,
        "merged": 2,
        "aliases_created": 0,
        "errors": [],
        "batch_id": "batch-merge",
    }
    async with async_session() as db:
        names = {t.name for t in (await db.execute(select(Tag))).scalars()}
        assert names == {"白色"}
        from app.models.tag import TagAlias

        aliases = (await db.execute(select(TagAlias))).scalars().all()
        assert aliases == []


async def test_apply_tag_clusters_skips_source_equal_target(client):
    """源标签与目标相同：记错误但组仍算 processed（不重复合并）。"""
    target = await _tag("白色")
    async with async_session() as db:
        result = await svc.apply_tag_clusters(
            db,
            [{"group_id": "g-self", "target_tag_id": target.id, "source_tag_ids": [target.id]}],
            batch_id="b",
        )

    assert result["applied"] == 1
    assert result["merged"] == 0
    assert result["errors"][0]["message"] == f"源标签 {target.id} 与目标相同"


async def test_apply_tag_clusters_collects_merge_exception(client, monkeypatch):
    """单条 merge 抛异常：不阻断其余组，错误汇总返回。"""
    target = await _tag("白色")
    src = await _tag("白色毛衣")
    import app.services.tag_crud as tag_crud

    async def _boom(_db, _source, _target, batch_id=None):
        raise RuntimeError("merge boom")

    monkeypatch.setattr(tag_crud, "merge_tags", _boom)

    async with async_session() as db:
        result = await svc.apply_tag_clusters(
            db,
            [{"target_tag_id": target.id, "source_tag_ids": [src.id], "keep_as_alias": True}],
            batch_id="b",
        )

    assert result["applied"] == 1 and result["merged"] == 0
    assert any("合并" in e["message"] and "merge boom" in e["message"] for e in result["errors"])


async def test_apply_tag_clusters_collects_alias_conflict(client, monkeypatch):
    """合并成功但保留别名冲突：errors 记录别名失败，merged 仍计数。"""
    target = await _tag("白色")
    src = await _tag("白色毛衣")
    import app.services.tag_alias as tag_alias
    from app.services.tag_crud import TagConflictError

    async def _conflict(_db, _tag_id, _alias, batch_id=None):
        raise TagConflictError("别名冲突")

    monkeypatch.setattr(tag_alias, "create_alias", _conflict)

    async with async_session() as db:
        result = await svc.apply_tag_clusters(
            db,
            [{"target_tag_id": target.id, "source_tag_ids": [src.id], "keep_as_alias": True}],
            batch_id="b",
        )

    assert result["merged"] == 1
    assert result["aliases_created"] == 0
    assert len(result["errors"]) == 1
    assert "保留别名" in result["errors"][0]["message"]


async def test_apply_tag_clusters_generates_batch_id(client):
    target = await _tag("白色")
    async with async_session() as db:
        result = await svc.apply_tag_clusters(
            db, [{"target_tag_id": target.id, "source_tag_ids": [], "keep_as_alias": True}]
        )

    assert result["batch_id"].startswith("cluster-")


@pytest.mark.parametrize("bad_groups", [[], None])
async def test_apply_tag_clusters_noop_inputs(client, bad_groups):
    target = await _tag("白色")
    assert target  # 确保库非空，排除「空库特判」干扰
    async with async_session() as db:
        result = await svc.apply_tag_clusters(db, bad_groups or [])

    assert result["applied"] == 0 and result["merged"] == 0
