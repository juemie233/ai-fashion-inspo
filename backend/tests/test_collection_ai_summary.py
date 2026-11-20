"""合集 AI 整理预览测试（2026-11-20 P2）。

覆盖：正常 JSON、markdown 包裹 JSON、解析失败回退、调用失败 503、空合集 400、
最多 80 条采样、规则分组（标签词频 top5 / 成员≤12 / 无标签按颜色或评分兜底 1 组）、
不写库（只返回预览）。
"""

import json

from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.models.collection import CollectionItem
from app.models.inspiration import Inspiration
from app.models.tag import InspirationTag, Tag
from app.services import collection_ai_service


def _create_collection(client, name: str) -> int:
    resp = client.post("/api/collections", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _seed_item(
    insp_id: str,
    *,
    tags: tuple[str, ...] = (),
    rating: int = 0,
    author: str = "",
    colors: list[str] | None = None,
    caption: str | None = None,
) -> None:
    async with async_session() as db:
        db.add(
            Inspiration(
                id=insp_id,
                source_type="manual_upload",
                file_path=f"images/{insp_id}.jpg",
                thumbnail_path=f"thumbnails/{insp_id}.jpg",
                media_type="image",
                quality_status="approved",
                rating=rating,
                source_author=author or None,
                caption=caption,
                dominant_colors=json.dumps(colors) if colors else None,
            )
        )
        for tag_name in tags:
            tag = (
                await db.execute(select(Tag).where(Tag.name == tag_name))
            ).scalar_one_or_none()
            if tag is None:
                tag = Tag(name=tag_name, category="free")
                db.add(tag)
                await db.flush()
            db.add(
                InspirationTag(
                    inspiration_id=insp_id, tag_id=tag.id, source="manual"
                )
            )
        await db.commit()


async def _add_members(collection_id: int, inspiration_ids: list[str]) -> None:
    async with async_session() as db:
        for position, insp_id in enumerate(inspiration_ids):
            db.add(
                CollectionItem(
                    collection_id=collection_id,
                    inspiration_id=insp_id,
                    position=position,
                )
            )
        await db.commit()


def _ai_payload(groups: list[dict]) -> str:
    return json.dumps(
        {
            "summary": "这是一组法式与通勤混搭的穿搭灵感。",
            "name_suggestions": ["法式通勤精选", "优雅日常收藏", "法式灵感簿"],
            "reason": "素材以法式长裙与通勤单品为主，适合按场景收集。",
            "theme_tags": ["法式", "通勤", "长裙", "优雅", "日常", "收藏"],
            "groups": groups,
        },
        ensure_ascii=False,
    )


async def test_ai_summary_normal_json(client, monkeypatch):
    collection_id = _create_collection(client, "法式通勤")
    await _seed_item("a1", tags=("法式",), author="作者A")
    await _seed_item("a2", tags=("法式", "长裙"), author="作者B")
    await _seed_item("a3", tags=("通勤",), author="作者C")
    await _add_members(collection_id, ["a1", "a2", "a3"])

    async def fake_chat(prompt: str) -> str:
        return _ai_payload(
            [
                {"tag": "法式", "name": "法式优雅"},
                {"tag": "通勤", "name": "通勤日常"},
            ]
        )

    monkeypatch.setattr(collection_ai_service, "_request_ai", fake_chat)
    resp = client.post(f"/api/collections/{collection_id}/ai-summary")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["summary"]
    assert len(data["name_suggestions"]) == 3
    assert data["reason"]
    assert 5 <= len(data["theme_tags"]) <= 8
    assert data["model"] == settings.ollama_vision_model
    assert data["generated_at"].endswith("Z")

    groups = data["groups"]
    # 标签词频：法式 2 次排在首位；长裙/通勤各 1 次（都进入规则分组）
    assert groups[0]["tag"] == "法式"
    assert {group["tag"] for group in groups} == {"法式", "长裙", "通勤"}
    group_map = {group["tag"]: group for group in groups}
    assert group_map["法式"]["name"] == "法式优雅"
    assert group_map["法式"]["count"] == 2
    assert set(group_map["法式"]["item_ids"]) == {"a1", "a2"}
    assert group_map["通勤"]["name"] == "通勤日常"
    assert group_map["长裙"]["name"] == "长裙"  # AI 未给该组名字 → 回退 tag
    assert group_map["法式"]["items"][0]["id"] in {"a1", "a2"}
    assert "thumbnail_path" in group_map["法式"]["items"][0]
    assert "source_author" in group_map["法式"]["items"][0]


async def test_ai_summary_markdown_wrapped_json(client, monkeypatch):
    collection_id = _create_collection(client, "包裹测试")
    await _seed_item("b1", tags=("法式",))
    await _add_members(collection_id, ["b1"])

    async def fake_chat(prompt: str) -> str:
        return "```json\n" + _ai_payload([{"tag": "法式", "name": "法式风"}]) + "\n```"

    monkeypatch.setattr(collection_ai_service, "_request_ai", fake_chat)
    data = client.post(f"/api/collections/{collection_id}/ai-summary").json()
    assert data["groups"][0]["name"] == "法式风"


async def test_ai_summary_parse_failure_falls_back(client, monkeypatch):
    collection_id = _create_collection(client, "解析失败")
    await _seed_item("c1", tags=("法式",), rating=5)
    await _add_members(collection_id, ["c1"])

    async def fake_chat(prompt: str) -> str:
        return "AI 今天不想输出 JSON"

    monkeypatch.setattr(collection_ai_service, "_request_ai", fake_chat)
    resp = client.post(f"/api/collections/{collection_id}/ai-summary")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # 分组名回退规则 tag；其余字段规则兜底
    assert data["groups"][0]["name"] == "法式"
    assert data["summary"]
    assert len(data["name_suggestions"]) == 3
    assert 5 <= len(data["theme_tags"]) <= 8


async def test_ai_summary_service_failure_503(client, monkeypatch):
    collection_id = _create_collection(client, "失败测试")
    await _seed_item("d1", tags=("法式",))
    await _add_members(collection_id, ["d1"])

    async def fake_chat(prompt: str) -> str:
        raise RuntimeError("ollama down")

    monkeypatch.setattr(collection_ai_service, "_request_ai", fake_chat)
    resp = client.post(f"/api/collections/{collection_id}/ai-summary")
    assert resp.status_code == 503
    assert "AI 服务暂不可用" in resp.json()["detail"]


async def test_ai_summary_empty_collection_400(client):
    empty = _create_collection(client, "空合集")
    resp = client.post(f"/api/collections/{empty}/ai-summary")
    assert resp.status_code == 400
    assert "合集为空" in resp.json()["detail"]


async def test_ai_summary_samples_at_most_80_items(client, monkeypatch):
    collection_id = _create_collection(client, "采样上限")
    ids = []
    for index in range(85):
        insp_id = f"sample-{index:02d}"
        await _seed_item(insp_id, tags=("法式",), author=f"作者{index}")
        ids.append(insp_id)
    await _add_members(collection_id, ids)

    recorded: dict = {}

    async def fake_chat(prompt: str) -> str:
        recorded["prompt"] = prompt
        return _ai_payload([{"tag": "法式", "name": "法式精选"}])

    monkeypatch.setattr(collection_ai_service, "_request_ai", fake_chat)
    data = client.post(f"/api/collections/{collection_id}/ai-summary").json()
    prompt = recorded["prompt"]
    assert "作者79" in prompt
    assert "作者84" not in prompt  # 只采样前 80 条
    # 规则分组 count 为全量成员数、item_ids 最多 12 个
    assert data["groups"][0]["count"] == 80  # 分组统计基于采样后的 80 条
    assert len(data["groups"][0]["item_ids"]) == 12


async def test_ai_summary_no_tags_falls_back_to_color_or_rating(client, monkeypatch):
    color_collection = _create_collection(client, "无标签有颜色")
    await _seed_item("e1", colors=["#ff0000"], rating=4)
    await _seed_item("e2", colors=["#ff0000"], rating=0)
    await _add_members(color_collection, ["e1", "e2"])

    rating_collection = _create_collection(client, "无标签无颜色")
    await _seed_item("f1", rating=5)
    await _seed_item("f2", rating=0)
    await _add_members(rating_collection, ["f1", "f2"])

    async def fake_chat(prompt: str) -> str:
        return "{}"

    monkeypatch.setattr(collection_ai_service, "_request_ai", fake_chat)

    color_data = client.post(
        f"/api/collections/{color_collection}/ai-summary"
    ).json()
    assert len(color_data["groups"]) == 1
    assert color_data["groups"][0]["tag"] == "#ff0000"
    assert color_data["groups"][0]["count"] == 2

    rating_data = client.post(
        f"/api/collections/{rating_collection}/ai-summary"
    ).json()
    assert len(rating_data["groups"]) == 1
    assert rating_data["groups"][0]["tag"] == "高评分"


def test_build_prompt_keeps_json_instructions_when_truncated():
    """超长素材行时只截素材区：末尾「只输出 JSON」的格式要求必须完整保留。

    此前按总长度从尾部截断，正好把格式要求切掉，模型返回散文 → 全量退化为规则兜底。
    """
    from types import SimpleNamespace

    collection = SimpleNamespace(name="长名字" * 40)
    items = [
        SimpleNamespace(
            id=f"id-{index}",
            caption="超长标题" * 40,
            file_path=f"images/{index}.jpg",
            source_author="作者" * 20,
            tags=[],
            dominant_colors=None,
            rating=3,
            source_type="manual_upload",
        )
        for index in range(80)
    ]
    groups = [
        {"tag": f"tag{index}", "count": 10, "item_ids": [f"id-{index}"]}
        for index in range(5)
    ]

    prompt = collection_ai_service._build_prompt(collection, items, groups)

    assert len(prompt) <= collection_ai_service.MAX_PROMPT_CHARS
    assert "只输出 JSON" in prompt
    # 结尾必须是 JSON 模板，而不是被截断的半句话
    assert prompt.rstrip().endswith("]}")
