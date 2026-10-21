"""从素材反推 prompt 的测试。

这个功能是「出图」相对裸 ComfyUI 的核心增量（复用库里 1.3 万标签的沉淀），
但它是纯拼装逻辑、出错只会「描述难看」不会报错，所以必须靠断言锁住：
类别顺序、每类截断、主色命名、空输入兜底、脏数据（非法 hex / 坏 JSON）不崩。
"""

import json

from sqlalchemy import select

from app.database import async_session
from app.models.inspiration import Inspiration
from app.models.tag import InspirationTag, Tag
from app.services.comfyui import prompt_builder as pb


async def _add_inspiration(
    insp_id: str,
    *,
    colors: str | None = None,
    caption: str | None = None,
    tags: list[tuple[str, str]] | None = None,
) -> str:
    """造素材 + 可选标签（tags 为 [(name, category)]，置信度按顺序递减）。"""
    async with async_session() as db:
        db.add(
            Inspiration(
                id=insp_id,
                source_type="douyin",
                file_path=f"images/x/{insp_id}.jpg",
                media_type="image",
                quality_status="approved",
                dominant_colors=colors,
                caption=caption,
            )
        )
        for index, (name, category) in enumerate(tags or []):
            # 标签名全局唯一：同名复用（不同用例会重复用「针织开衫」等名字）
            existing = (
                await db.execute(select(Tag).where(Tag.name == name))
            ).scalar_one_or_none()
            if existing is None:
                existing = Tag(name=name, category=category, source="ai_generated")
                db.add(existing)
                await db.flush()
            db.add(
                InspirationTag(
                    inspiration_id=insp_id,
                    tag_id=existing.id,
                    confidence=0.95 - index * 0.05,
                    source="ai_generated",
                )
            )
        await db.commit()
    return insp_id


def _sources(result: dict) -> list[str]:
    return [part["source"] for part in result["parts"]]


async def test_empty_input_returns_quality_suffix_only(client):
    """没有素材时不能抛错，只给画质词（前端「反推」按钮在空输入时也不会崩）。"""
    async with async_session() as db:
        result = await pb.build_prompt(db, [])
    assert result["parts"] == [{"text": pb.QUALITY_SUFFIX, "source": "画质词"}]
    assert result["prompt"] == pb.QUALITY_SUFFIX


async def test_parts_follow_category_order(client):
    """分段顺序固定为 item → style → color → scene → material → other。

    顺序不是审美问题：prompt 里主体必须靠前，否则模型容易把「场景」当主体。
    """
    await _add_inspiration(
        "pb-order",
        tags=[
            ("咖啡馆", "scene"),
            ("米色", "color"),
            ("针织开衫", "item"),
        ],
    )
    async with async_session() as db:
        result = await pb.build_prompt(db, ["pb-order"])

    assert _sources(result) == ["标签·item", "标签·color", "标签·scene", "画质词"]
    assert "针织开衫" in result["parts"][0]["text"]
    assert result["prompt"].endswith(pb.QUALITY_SUFFIX)


async def test_each_category_is_truncated(client):
    """每类最多 6 个标签：堆太多会稀释主体。"""
    tags = [(f"单品{i}", "item") for i in range(9)]
    await _add_inspiration("pb-truncate", tags=tags)
    async with async_session() as db:
        result = await pb.build_prompt(db, ["pb-truncate"])

    item_part = next(p for p in result["parts"] if p["source"] == "标签·item")
    assert len(item_part["text"].split("、")) == 6


async def test_unknown_category_is_appended_at_tail(client):
    """未来新增的类别不丢：按「标签·<类别>」兜底追加到最后（画质词之前）。"""
    await _add_inspiration("pb-unknown", tags=[("亮片", "texture")])
    async with async_session() as db:
        result = await pb.build_prompt(db, ["pb-unknown"])

    assert _sources(result) == ["标签·texture", "画质词"]


async def test_colors_and_caption_become_parts(client):
    """主色转中文色名；caption 截断进「正文」段。"""
    await _add_inspiration(
        "pb-color",
        colors=json.dumps(["#f5f5dc", "#000000", "#ffffff"]),
        caption="  米色针织开衫配白色阔腿裤，极简工作室背景，柔和自然光，时尚编辑摄影多拍了几张备用 ",
    )
    async with async_session() as db:
        result = await pb.build_prompt(db, ["pb-color"])

    color_part = next(p for p in result["parts"] if p["source"] == "主色")
    assert color_part["text"] == "配色：米色、黑色、白色"
    caption_part = next(p for p in result["parts"] if p["source"] == "正文")
    assert len(caption_part["text"]) <= 60 + 2  # 截断 + 可能的连接符
    assert caption_part["text"].startswith("米色针织开衫")


async def test_invalid_color_payload_does_not_crash(client):
    """历史数据里 dominant_colors 可能是坏 JSON 或非法 hex：不能因此反推失败。"""
    await _add_inspiration("pb-bad-json", colors="{not json")
    await _add_inspiration("pb-bad-hex", colors=json.dumps(["#zzzzzz", "#123"]))
    async with async_session() as db:
        result = await pb.build_prompt(db, ["pb-bad-json"])
        assert "配色" not in " ".join(p["text"] for p in result["parts"])

        result = await pb.build_prompt(db, ["pb-bad-hex"])
        color_part = next(p for p in result["parts"] if p["source"] == "主色")
        # 非法值原样保留而不是抛错（至少不丢信息）
        assert "#zzzzzz" in color_part["text"]


async def test_color_label_boundaries():
    """色名映射：命中表 / 极亮 / 极暗 / 其它回退 hex。"""
    assert pb._color_label("#F5F5DC") == "米色"  # 大小写不敏感
    assert pb._color_label("fefefe") == "白色"  # 极亮 → 白
    assert pb._color_label("010101") == "黑色"  # 极暗 → 黑
    assert pb._color_label("3366cc").startswith("#")  # 未命中 → 回退
    assert pb._color_label("nothex") == "nothex"  # 非 6 位原样返回


async def test_multiple_inspirations_merge_tags_and_take_first_colors(client):
    """多个素材：标签合并去重，主色只取第一个有值的素材（避免配色过载）。"""
    await _add_inspiration("pb-m1", colors=json.dumps(["#000000"]), tags=[("针织开衫", "item")])
    await _add_inspiration("pb-m2", colors=json.dumps(["#ffffff"]), tags=[("针织开衫", "item")])

    async with async_session() as db:
        result = await pb.build_prompt(db, ["pb-m1", "pb-m2"])

    item_part = next(p for p in result["parts"] if p["source"] == "标签·item")
    assert item_part["text"] == "针织开衫"  # 去重后只剩一次
    color_part = next(p for p in result["parts"] if p["source"] == "主色")
    assert "黑色" in color_part["text"] and "白色" not in color_part["text"]
