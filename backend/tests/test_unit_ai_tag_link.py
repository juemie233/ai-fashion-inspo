"""`ai_tag_saver.link_tag` 与颜色推断的测试。

`link_tag` 是「素材 ↔ 标签」关联的**唯一写入入口**（`save_tags` / 视频融合 /
多模型组合分析都走它）。它有三个容易写错、且写错了不会报错只会让数据慢慢变脏的语义：

1. **重复关联不新建**：同一 (素材, 标签) 只能有一行，否则标签面板会把同一个标签
   显示多次、统计翻倍；
2. **置信度只升不降**：二次分析（更高置信度）要更新，低置信度的重新分析不能把好结果
   覆盖成更差的值；
3. **不把手动关联改写成 AI 关联**：`source` 只在新建时写入，已存在关联保留原 source
   ——否则用户手动打的标签会变成「AI 生成」，人工修正记录被抹掉；
4. **并发撞唯一约束**（SAVEPOINT 隔离）时不能整体失败：回滚后重查并更新置信度，
   同事务里其它关联也不受影响（这是批量分析并发的常态）。

颜色推断（`guess_color_from_hex`）喂给「主色调」标签：灰度/基本色/棕色粉色边界错，
用户按颜色筛选就会筛不到。
"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database import async_session
from app.models.inspiration import Inspiration
from app.models.tag import InspirationTag, Tag
from app.services.ai_tag_saver import guess_color_from_hex, link_tag, normalize_color


async def _insp(insp_id: str = "link-1") -> str:
    async with async_session() as db:
        db.add(
            Inspiration(
                id=insp_id,
                source_type="douyin",
                file_path=f"images/2026-10/{insp_id}.jpg",
                media_type="image",
            )
        )
        await db.commit()
    return insp_id


async def _tag(name: str = "针织开衫") -> int:
    async with async_session() as db:
        tag = Tag(name=name, category="单品", source="manual")
        db.add(tag)
        await db.commit()
        return tag.id


async def _links(insp_id: str) -> list[InspirationTag]:
    async with async_session() as db:
        return list(
            (
                await db.execute(
                    select(InspirationTag).where(
                        InspirationTag.inspiration_id == insp_id
                    )
                )
            )
            .scalars()
            .all()
        )


# ── link_tag ──


async def test_link_tag_creates_link_with_source(client):
    insp = await _insp()
    tag_id = await _tag()

    async with async_session() as db:
        await link_tag(db, insp, tag_id, confidence=0.8, source="ai_generated")
        await db.commit()

    links = await _links(insp)
    assert len(links) == 1
    assert links[0].confidence == pytest.approx(0.8)
    assert links[0].source == "ai_generated"


async def test_link_tag_is_idempotent(client):
    """重复关联不新建第二行（否则标签面板重复显示、统计翻倍）。"""
    insp = await _insp("link-dup")
    tag_id = await _tag("阔腿裤")

    async with async_session() as db:
        await link_tag(db, insp, tag_id, confidence=0.5)
        await link_tag(db, insp, tag_id, confidence=0.5)
        await db.commit()

    assert len(await _links(insp)) == 1


async def test_link_tag_raises_confidence_but_never_lowers_it(client):
    """置信度只升不降：低置信度的二次分析不能覆盖更可信的结果。"""
    insp = await _insp("link-conf")
    tag_id = await _tag("极简")

    async with async_session() as db:
        await link_tag(db, insp, tag_id, confidence=0.4)
        await link_tag(db, insp, tag_id, confidence=0.9)  # 升
        await db.commit()
    assert (await _links(insp))[0].confidence == pytest.approx(0.9)

    async with async_session() as db:
        await link_tag(db, insp, tag_id, confidence=0.2)  # 降：应被忽略
        await db.commit()
    assert (await _links(insp))[0].confidence == pytest.approx(0.9)


async def test_link_tag_keeps_manual_source_on_reanalysis(client):
    """已存在的手动关联不能被二次分析改写成 ai_generated（否则人工修正记录丢失）。"""
    insp = await _insp("link-manual")
    tag_id = await _tag("通勤")
    async with async_session() as db:
        await link_tag(db, insp, tag_id, confidence=0.5, source="manual")
        await db.commit()

    async with async_session() as db:
        await link_tag(db, insp, tag_id, confidence=0.95, source="ai_generated")
        await db.commit()

    link = (await _links(insp))[0]
    assert link.source == "manual"  # 保留原来源
    assert link.confidence == pytest.approx(0.95)  # 但置信度可以更新


async def test_link_tag_upsert_is_atomic_and_keeps_max_confidence(client):
    """并发安全靠单条 upsert（不是「先查后写 + 捕异常」）。

    背景：原实现「查到就改 / 查不到就插，撞唯一约束再补偿」——补偿路径在
    ``begin_nested`` 回滚后 Session 已处于 pending-rollback，下一次 execute 直接抛
    ``PendingRollbackError``，于是恢复逻辑自己成了新异常、整批分析失败。
    改成 ``INSERT … ON CONFLICT DO UPDATE`` 后：同一对 (素材, 标签) 反复写只有一行，
    置信度取最大值，且不需要任何异常处理。
    """
    insp = await _insp("link-upsert")
    tag_id = await _tag("法式")

    async with async_session() as db:
        await link_tag(db, insp, tag_id, confidence=0.3)
        await link_tag(db, insp, tag_id, confidence=0.9)  # 抬高
        await link_tag(db, insp, tag_id, confidence=0.5)  # 不应降低
        await db.commit()

    links = await _links(insp)
    assert len(links) == 1
    assert links[0].confidence == pytest.approx(0.9)


async def test_link_tag_upsert_keeps_original_source(client):
    """upsert 的 set_ 不含 source：已存在的手动关联不会被改写成 ai_generated。"""
    insp = await _insp("link-upsert-src")
    tag_id = await _tag("复古")
    async with async_session() as db:
        await link_tag(db, insp, tag_id, confidence=0.4, source="manual")
        await link_tag(db, insp, tag_id, confidence=0.99, source="ai_generated")
        await db.commit()

    link = (await _links(insp))[0]
    assert link.source == "manual"
    assert link.confidence == pytest.approx(0.99)


# ── 颜色推断 ──


@pytest.mark.parametrize(
    "hex_str,expect",
    [
        ("#000000", "黑色"),
        ("#101010", "黑色"),
        ("#ffffff", "白色"),
        ("#f0f0f0", "白色"),
        ("#404040", "深灰色"),
        ("#808080", "浅灰色"),
        ("#ff0000", "红色"),
        ("#00ff00", "绿色"),
        ("#0000ff", "蓝色"),
        ("#786446", "棕色"),  # 红略占优且 g>b → 棕色
        ("#ffdce6", "粉色"),  # 红占优、接近蓝且亮度高 → 粉色
    ],
)
def test_guess_color_from_hex_basic(hex_str, expect):
    assert guess_color_from_hex(hex_str) == expect


@pytest.mark.parametrize("bad", ["", "#", "#12", "zzzzzz", "#gggggg"])
def test_guess_color_from_hex_invalid_returns_empty(bad):
    """非法输入返回空串（调用方据此跳过颜色标签，而不是写一个「None」标签）。"""
    assert guess_color_from_hex(bad) == ""


def test_guess_color_from_hex_supports_short_form():
    """三位缩写（#f00）按每位 ×17 展开，与六位结果一致。"""
    assert guess_color_from_hex("#f00") == guess_color_from_hex("#ff0000")


# ── normalize_color：模型输出的颜色值格式很杂（中英文、带不带 #、六位/三位） ──


@pytest.mark.parametrize(
    "raw,expect",
    [
        ("", ""),
        ("黑色", "黑色"),
        ("#黑色", "黑色"),  # 中文但带 #
        ("BLACK", "黑色"),
        ("#black", "黑色"),
        ("  white  ", "白色"),
        ("#FFFFFF", "白色"),
        ("#808080", "灰色"),
        ("#8B4513", "棕色"),  # 精确命中颜色表
        ("#000039", "深蓝色"),  # 前缀模糊匹配：按颜色表顺序取首个命中
        ("C8C832", "橙色"),  # 不带 # 前缀（模型输出常见）也要能认
        ("#C832C8", ""),  # 红蓝并列且绿很低：无法归类 → 空串
    ],
)
def test_normalize_color_formats(raw, expect):
    from app.services.ai_tag_saver import normalize_color

    assert normalize_color(raw) == expect


def test_normalize_color_unknown_returns_empty():
    """认不出的颜色返回空串（调用方跳过，不写一个「None」标签）。"""
    from app.services.ai_tag_saver import normalize_color

    assert normalize_color("#12345") == ""
