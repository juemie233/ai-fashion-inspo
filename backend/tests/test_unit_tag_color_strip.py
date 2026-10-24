"""单品标签「颜色剥离」治理的测试（dry-run 与 apply 一致性）。

这套逻辑会**批量改标签名、删标签、重指向关联行**，一旦算错就是不可逆的数据损坏
（所以才有操作历史与回滚）。测试重点：

1. 剥离规则：长词优先（「米白色」不能剥成「米」+「白色」）、纯颜色名不剥空；
2. dry-run 的预计数必须与 apply 的实际结果一致（否则用户按预览决定，落地却不同）；
3. 撞名合并：关联重指向 + 同素材双关联去重 + 别名搬迁 + 跨类别不合并；
4. apply 写操作历史，可用于回滚。
"""

import pytest

from sqlalchemy import select

from app.database import async_session
from app.models.inspiration import Inspiration
from app.models.tag import InspirationTag, Tag, TagAlias
from app.models.tag_history import TagHistory
from app.services.tag_color_strip import (
    _strip_color_prefix,
    apply_color_strip,
    build_color_prefixes,
    dry_run_color_strip,
)


async def _tag(name: str, category: str) -> int:
    async with async_session() as db:
        tag = Tag(name=name, category=category, source="ai_generated")
        db.add(tag)
        await db.commit()
        return tag.id


async def _insp(insp_id: str) -> None:
    async with async_session() as db:
        db.add(
            Inspiration(
                id=insp_id,
                source_type="douyin",
                file_path=f"images/x/{insp_id}.jpg",
                media_type="image",
                quality_status="approved",
            )
        )
        await db.commit()


async def _link(insp_id: str, tag_id: int) -> None:
    async with async_session() as db:
        db.add(InspirationTag(inspiration_id=insp_id, tag_id=tag_id, confidence=0.9, source="ai_generated"))
        await db.commit()


# ── 纯函数：剥离规则 ──


def test_strip_prefers_longest_prefix():
    """「米白色」必须先于「白色」匹配，否则会剥出「色」这种残字。"""
    prefixes = ["米白色", "白色", "米色"]
    assert _strip_color_prefix("米白色针织开衫", prefixes) == ("针织开衫", "米白色")
    assert _strip_color_prefix("白色阔腿裤", prefixes) == ("阔腿裤", "白色")


def test_strip_keeps_pure_color_name():
    """标签本身就是颜色词时原样返回（否则会产生空名标签）。"""
    assert _strip_color_prefix("白色", ["白色"]) == ("白色", None)


def test_strip_returns_original_when_no_prefix():
    assert _strip_color_prefix("针织开衫", ["白色"]) == ("针织开衫", None)


async def test_build_prefixes_sorted_by_length(client):
    await _tag("白色", "color")
    await _tag("米白色", "color")

    async with async_session() as db:
        prefixes = await build_color_prefixes(db)

    assert prefixes[:2] == ["米白色", "白色"]


# ── dry-run / apply ──


async def _prepare_scene() -> dict:
    """场景：颜色词 + 三个单品标签（首尾两个剥完撞名）+ 两条素材关联。"""
    ids = {
        "white": await _tag("白色", "color"),
        "blue": await _tag("蓝色", "color"),
        "strip_a": await _tag("白色尖头高跟鞋", "item_type"),
        "strip_b": await _tag("蓝色尖头高跟鞋", "item_type"),
        "other": await _tag("通勤", "style"),
    }
    await _insp("ci1")
    await _insp("ci2")
    await _link("ci1", ids["strip_a"])
    await _link("ci2", ids["strip_b"])
    return ids


async def test_dry_run_reports_merge_and_repoint(client):
    """两个颜色前缀标签剥完都叫「尖头高跟鞋」：先 rename、后到的 merge 到它。"""
    ids = await _prepare_scene()

    async with async_session() as db:
        preview = await dry_run_color_strip(db, category="item_type")

    assert preview["total_scanned"] == 2  # 场景里只有两个 item_type 标签
    assert preview["renamed"] == 1
    assert preview["merged"] == 1
    assert preview["links_repointed"] == 1  # 第二个标签的关联要重指向
    assert preview["color_links_added"] == 2  # 两条素材都补上对应颜色关联
    assert {s["action"] for s in preview["samples"]} == {"rename", "merge"}
    assert ids["white"] > 0


async def test_apply_matches_dry_run_and_mutates(client):
    """apply 的实际数字必须与 dry-run 一致（用户是按预览决定要不要执行的）。"""
    ids = await _prepare_scene()

    async with async_session() as db:
        preview = await dry_run_color_strip(db, category="item_type")

    async with async_session() as db:
        result = await apply_color_strip(db, category="item_type")

    for key in ("renamed", "merged", "skipped", "links_repointed", "color_links_added"):
        assert result[key] == preview[key], key
    assert result["batch_id"]

    async with async_session() as db:
        tags = {t.name for t in (await db.execute(select(Tag))).scalars()}
    # 「白色尖头高跟鞋」被改名合并掉；只剩不带颜色的单品名
    assert "白色尖头高跟鞋" not in tags
    assert "蓝色尖头高跟鞋" not in tags
    assert "尖头高跟鞋" in tags
    assert "综合" not in tags


async def test_apply_keeps_other_categories_untouched(client):
    ids = await _prepare_scene()

    async with async_session() as db:
        await apply_color_strip(db, category="item_type")

    async with async_session() as db:
        style = await db.get(Tag, ids["other"])
    assert style.name == "通勤"  # 非 item_type 类别不参与


async def test_apply_adds_color_links_deduped(client):
    """补建颜色关联要按 (素材, 颜色标签) 去重：同一素材已关联则不重复插。"""
    ids = await _prepare_scene()
    await _link("ci1", ids["white"])  # ci1 已有白色关联

    async with async_session() as db:
        result = await apply_color_strip(db, category="item_type")

    async with async_session() as db:
        rows = (
            await db.execute(
                select(InspirationTag.inspiration_id, InspirationTag.tag_id).where(
                    InspirationTag.tag_id == ids["white"]
                )
            )
        ).all()
    pairs = [(iid, tid) for iid, tid in rows]
    assert pairs.count(("ci1", ids["white"])) == 1
    assert result["color_links_added"] >= 1


async def test_apply_skips_when_alias_collision(client):
    """剥离后的新名若与某个标签的**别名**同名，跳过（否则别名归一化会指向两个标签）。"""
    await _tag("白色", "color")
    await _tag("白色针织开衫", "item_type")
    other = await _tag("毛衣", "item_type")
    async with async_session() as db:
        db.add(TagAlias(tag_id=other, alias="针织开衫"))
        await db.commit()

    async with async_session() as db:
        preview = await dry_run_color_strip(db, category="item_type")

    assert preview["alias_skipped"] == 1
    assert preview["renamed"] == 0


async def test_apply_skips_cross_category_merge(client):
    """新名被其它类别标签占用时跳过，避免把 item_type 融进 body_part。"""
    await _tag("白色", "color")
    await _tag("白色尖头高跟鞋", "item_type")
    await _tag("尖头高跟鞋", "body_part")  # 跨类别占位（标签名全局唯一）

    async with async_session() as db:
        preview = await dry_run_color_strip(db, category="item_type")

    assert preview["cross_category_skipped"] == 1
    assert preview["merged"] == 0


async def test_apply_no_op_returns_null_batch(client):
    """没有可治理的标签时 batch_id=None（前端据此提示"无需处理"）。"""
    await _tag("干净标签", "item_type")

    async with async_session() as db:
        result = await apply_color_strip(db, category="item_type")

    assert result["batch_id"] is None
    assert result["renamed"] == 0 and result["merged"] == 0


async def test_apply_respects_limit(client):
    """limit 只处理前 N 个标签（大库分批治理，避免一次锁太久）。"""
    await _tag("白色", "color")
    for i in range(4):
        await _tag(f"白色单品{i}", "item_type")

    async with async_session() as db:
        preview = await dry_run_color_strip(db, category="item_type", limit=2)

    assert preview["total_scanned"] == 2


async def test_apply_writes_history(client):
    """写操作历史（供回滚）：batch_id 应能在 tag_history 里查到。"""
    await _prepare_scene()

    async with async_session() as db:
        result = await apply_color_strip(db, category="item_type")
        batch_id = result["batch_id"]

    async with async_session() as db:
        rows = (await db.execute(select(TagHistory).where(TagHistory.batch_id == batch_id))).scalars().all()

    assert rows, "颜色剥离必须留下可回滚的操作历史"


async def test_fetch_link_map_chunks_large_id_lists(client):
    """回归：上千个标签 id 也不再触发 `too many SQL variables`。

    实测 2026-10：本库 item_type 有 11,726 个标签，dry-run 一次性拼进
    ``IN (...)`` 直接抛 ``sqlite3.OperationalError: too many SQL variables``——
    这个治理接口第一次真跑就炸在这里。修复是按 500 分片查询。
    """
    from app.services.tag_color_strip import _fetch_link_map

    # 1500 个不存在的 id：重点不是结果，而是「不炸」
    ids = list(range(10_000_000, 10_001_500))
    async with async_session() as db:
        link_map = await _fetch_link_map(db, ids)
    assert len(link_map) == len(ids)
    assert all(v == [] for v in link_map.values())


async def test_count_existing_color_links_chunks_large_inspiration_lists(client):
    """回归：上千个素材 id 同样要分片（补建颜色关联前的去重查询走这条）。"""
    from app.services.tag_color_strip import _count_existing_color_links

    color_id = await _tag("分片色", "color")
    async with async_session() as db:
        found = await _count_existing_color_links(db, [color_id], [f"chunk-{i}" for i in range(1500)])
    assert found == set()
