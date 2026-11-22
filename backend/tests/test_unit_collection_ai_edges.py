"""合集 AI 整理预览（`app/services/collection_ai_service.py`）边界补测。

既有 `tests/test_collection_ai_summary.py` 覆盖接口主路径（正常 JSON / 包裹 JSON /
解析失败 / 调用失败 / 空合集 / 80 条采样 / 无标签兜底 / 提示词截断）；这里直接调
服务与纯函数，补齐：主色解析降级、分组兜底边界、提示词预算耗尽、AI JSON 解析
各种脏输出、字段清洗与兜底拼装、真实 `_request_ai` 的请求体/异常，以及
`generate_summary` 的 404 / 空响应 503 / 不写库等分支。LLM 一律 mock 在 httpx 边界。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.config import settings
from app.database import async_session
from app.models.collection import Collection, CollectionItem
from app.models.inspiration import Inspiration
from app.services import collection_ai_service as ai


# ── 构造助手 ──


def _item(
    item_id: str = "i1",
    *,
    tags: tuple[str, ...] = (),
    caption: str | None = None,
    file_path: str | None = "images/i1.jpg",
    colors=None,  # noqa: ANN001
    rating=None,  # noqa: ANN001
    author: str | None = None,
    source_type: str | None = "manual_upload",
    thumbnail_path: str | None = "thumbnails/i1.jpg",
) -> SimpleNamespace:
    links = [
        SimpleNamespace(tag=SimpleNamespace(name=name)) for name in tags
    ]
    return SimpleNamespace(
        id=item_id,
        tags=links,
        caption=caption,
        file_path=file_path,
        thumbnail_path=thumbnail_path,
        source_author=author,
        dominant_colors=colors,
        rating=rating,
        source_type=source_type,
    )


def _collection(name: str = "法式通勤") -> SimpleNamespace:
    return SimpleNamespace(name=name)


# ══════════════════════════════════════════════════════════════
#  素材字段解析：_colors_of / _tags_of / _title_of / _brief
# ══════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, []),
        ([], []),
        (["#fff", " #000 "], ["#fff", "#000"]),
        ('["#abc", "#def"]', ["#abc", "#def"]),
        ("not-json", []),  # JSON 解析失败 → 无颜色
        ('{"a": 1}', []),  # JSON 不是数组 → 无颜色
        ("123", []),
        (["#fff", "", None, 5], ["#fff"]),  # 空串/None/数字都不是颜色
        (["#fff", None, " #000 "], ["#fff", "#000"]),  # 非字符串跳过、str 去空白
    ],
)
def test_colors_of_variants(raw, expected):  # noqa: ANN001
    item = _item(colors=raw)
    assert ai._colors_of(item) == expected


def test_colors_of_handles_missing_attribute():
    assert ai._colors_of(SimpleNamespace()) == []


def test_tags_of_variants():
    no_attr = SimpleNamespace()
    assert ai._tags_of(no_attr) == []
    assert ai._tags_of(_item(tags=())) == []
    broken = SimpleNamespace(
        tags=[
            SimpleNamespace(tag=None),
            SimpleNamespace(tag=SimpleNamespace(name="")),
            SimpleNamespace(tag=SimpleNamespace(name="法式")),
        ]
    )
    assert ai._tags_of(broken) == ["法式"]


def test_title_of_prefers_caption_then_stem():
    assert ai._title_of(_item(caption="  雪纺连衣裙  ")) == "雪纺连衣裙"
    assert ai._title_of(_item(caption="", file_path="images/2026/abc.file.jpg")) == "abc.file"
    assert ai._title_of(_item(caption=None, file_path=None)) == ""


def test_brief_fields():
    item = _item(author="作者A", thumbnail_path="thumbnails/x.jpg")
    assert ai._brief(item) == {
        "id": "i1",
        "thumbnail_path": "thumbnails/x.jpg",
        "source_author": "作者A",
    }


# ══════════════════════════════════════════════════════════════
#  规则分组：词频 top5、重叠、每组 12 条上限、无标签兜底
# ══════════════════════════════════════════════════════════════


def test_rule_groups_tag_frequency_top5_and_member_cap():
    items = []
    # a 出现 13 次 → 组内 item_ids 截到 12；其余按频次递减
    for index in range(13):
        items.append(_item(f"a{index}", tags=("a",)))
    for index in range(6):
        items.append(_item(f"b{index}", tags=("b",)))
    for index in range(5):
        items.append(_item(f"c{index}", tags=("c",)))
    for index in range(4):
        items.append(_item(f"d{index}", tags=("d",)))
    for index in range(3):
        items.append(_item(f"e{index}", tags=("e",)))
    for index in range(2):
        items.append(_item(f"f{index}", tags=("f",)))
    items.append(_item("g0", tags=("g",)))

    groups = ai._rule_groups(items)
    assert [group["tag"] for group in groups] == ["a", "b", "c", "d", "e"]
    group_a = groups[0]
    assert group_a["count"] == 13  # count 是全量成员数
    assert len(group_a["item_ids"]) == ai.AI_MAX_GROUP_ITEMS == 12
    assert len(group_a["items"]) == 12
    assert group_a["name"] == "a"  # AI 起名失败前的规则回退


def test_rule_groups_overlapping_tags_and_duplicate_count():
    items = [_item("x", tags=("法式", "通勤")), _item("y", tags=("法式",))]
    groups = ai._rule_groups(items)
    by_tag = {group["tag"]: group for group in groups}
    assert by_tag["法式"]["count"] == 2
    assert by_tag["通勤"]["count"] == 1
    # 允许重叠：同一条素材可出现在多个分组
    assert "x" in by_tag["法式"]["item_ids"]
    assert "x" in by_tag["通勤"]["item_ids"]


def test_rule_groups_fallback_by_dominant_color():
    items = [
        _item("c1", colors=["#ff0000"]),
        _item("c2", colors='["#ff0000", "#000000"]'),
        _item("c3", colors=["#000000"]),
    ]
    (group,) = ai._rule_groups(items)
    assert group["tag"] == "#ff0000"
    assert group["name"] == "#ff0000"
    assert group["count"] == 3
    assert [item["id"] for item in group["items"]] == ["c1", "c2", "c3"]


def test_rule_groups_fallback_by_rating():
    rated = [_item("r1", rating=5), _item("r2", rating=0)]
    assert ai._rule_groups(rated)[0]["tag"] == "高评分"

    unrated = [_item("u1", rating=0), _item("u2", rating=None)]
    assert ai._rule_groups(unrated)[0]["tag"] == "未评分"


def test_rule_groups_empty_items():
    (group,) = ai._rule_groups([])
    assert group == {
        "tag": "未评分",
        "name": "未评分",
        "count": 0,
        "item_ids": [],
        "items": [],
    }


# ══════════════════════════════════════════════════════════════
#  提示词：单条素材行 / 完整提示词 / 预算耗尽
# ══════════════════════════════════════════════════════════════


def test_item_prompt_line_truncation_and_defaults():
    item = _item(
        tags=("标签" * 60,),
        caption="标题" * 60,
        colors=["#ffffff" * 10],
        author="作者" * 40,
        rating=None,
        source_type=None,
    )
    line = ai._item_prompt_line(3, item)
    assert line.startswith("3. 标题:")
    assert "｜作者:" in line
    assert "｜评分:0｜来源:-" in line
    # 各字段都按上限截断
    assert len(line) < 300
    assert "-" in line

    bare = ai._item_prompt_line(1, _item(tags=(), colors=None, author=None))
    assert "标签:-" in bare and "主色:-" in bare


def test_build_prompt_contains_groups_and_json_tail():
    items = [_item("i1", tags=("法式",), caption="第一条"), _item("i2")]
    groups = [
        {"tag": "法式", "count": 1, "item_ids": ["i1"]},
        {"tag": "通勤", "count": 0, "item_ids": []},
    ]
    prompt = ai._build_prompt(_collection("春季合集"), items, groups)

    assert "合集名称：春季合集" in prompt
    assert "素材数量：2 条" in prompt
    assert "1. 标题:第一条" in prompt
    assert "- 法式（1 条；示例 id：i1）" in prompt
    assert "只输出 JSON" in prompt
    assert prompt.rstrip().endswith("]}")


def test_build_prompt_budget_exhausted_keeps_tail(monkeypatch):
    """预算为负时素材区留空，但末尾「只输出 JSON」格式要求必须保留。"""
    monkeypatch.setattr(ai, "MAX_PROMPT_CHARS", 50)
    items = [_item("i1", caption="不该出现的素材标题")]
    groups = [{"tag": "法式", "count": 1, "item_ids": ["i1"]}]

    prompt = ai._build_prompt(_collection("预算测试"), items, groups)

    assert "不该出现的素材标题" not in prompt
    assert "只输出 JSON" in prompt
    assert prompt.rstrip().endswith("]}")


# ══════════════════════════════════════════════════════════════
#  AI 输出解析与字段清洗
# ══════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", {}),
        ("   ", {}),
        ('{"summary": "x"}', {"summary": "x"}),
        ('```json\n{"summary": "y"}\n```', {"summary": "y"}),
        ('```\n{"summary": "z"}\n```', {"summary": "z"}),
        ('前缀 {"summary": "prose"} 后缀', {"summary": "prose"}),
        ('这里 {不是 JSON} 结尾', {}),
        ("[1, 2]", {}),  # JSON 不是对象 → {}
        ("完全不是 JSON", {}),
        ('{"a": {"b": 1}}', {"a": {"b": 1}}),
    ],
)
def test_parse_ai_json_variants(raw, expected):  # noqa: ANN001
    assert ai._parse_ai_json(raw) == expected


def test_clean_str_list_variants():
    assert ai._clean_str_list(None) == []
    assert ai._clean_str_list("abc") == []
    assert ai._clean_str_list({"a": 1}) == []
    assert ai._clean_str_list([None, "", "  ", "法式", "法式", 123, "y" * 60]) == [
        "法式",
        "123",
        "y" * 40,
    ]


def test_fallback_summary():
    groups = [{"tag": "法式"}, {"tag": "通勤"}, {"tag": ""}]
    text = ai._fallback_summary(_collection("合集A"), [1, 2, 3], groups)
    assert text == "合集《合集A》共 3 条素材，主要主题：法式、通勤。"
    assert "未分组" in ai._fallback_summary(_collection("空主题"), [], [])


# ══════════════════════════════════════════════════════════════
#  结果合并：AI 字段缺失/非法时的兜底
# ══════════════════════════════════════════════════════════════


def test_apply_ai_result_empty_uses_rule_fallback():
    items = [_item("i1", tags=("法式",)), _item("i2", tags=("法式",))]
    groups = ai._rule_groups(items)

    result = ai._apply_ai_result({}, _collection("兜底合集"), items, groups)

    assert result["summary"].startswith("合集《兜底合集》共 2 条素材")
    assert result["name_suggestions"] == [
        "兜底合集精选",
        "兜底合集收藏",
        "我的穿搭灵感合集",
    ]
    assert result["reason"] == (
        "按素材标签频次自动分组，并由 AI 生成主题名（仅预览，未写入合集）。"
    )
    assert 5 <= len(result["theme_tags"]) <= 8
    assert result["theme_tags"][0] == "法式"
    assert result["groups"][0]["name"] == "法式"


def test_apply_ai_result_trims_and_validates_ai_fields():
    items = [_item("i1", tags=("法式",))]
    groups = [
        {"tag": "法式", "count": 1, "item_ids": ["i1"], "items": [], "name": "法式"},
        {"tag": "通勤", "count": 0, "item_ids": [], "items": [], "name": "通勤"},
    ]
    parsed = {
        "summary": "摘" * 700,
        "name_suggestions": ["唯一建议"],
        "reason": "理由" * 200,
        "theme_tags": ["法式", "x" * 30, 1, None, "通勤", "a", "b", "c", "d", "e"],
        "groups": [
            {"tag": "法式", "name": "优雅法式" * 20},
            {"tag": "", "name": "空 tag 忽略"},
            {"tag": "通勤"},  # 缺 name → 回退 tag
            "不是字典",  # 非 dict 条目跳过
        ],
    }

    result = ai._apply_ai_result(parsed, _collection("裁剪合集"), items, groups)

    assert len(result["summary"]) == 600
    assert len(result["reason"]) == 300
    # 名称建议不足 3 个 → 用兜底名补齐且保持去重
    assert len(result["name_suggestions"]) == 3
    assert result["name_suggestions"][0] == "唯一建议"
    # 超 20 字的主题标签被丢弃；总数截到 8
    assert all(len(tag) <= 20 for tag in result["theme_tags"])
    assert len(result["theme_tags"]) == ai.AI_MAX_THEME_TAGS_OUTPUT
    # 分组名：AI 名截到 40 字；缺 name 回退 tag
    group_map = {group["tag"]: group for group in groups}
    assert group_map["法式"]["name"] == ("优雅法式" * 20)[:40]
    assert group_map["通勤"]["name"] == "通勤"


# ══════════════════════════════════════════════════════════════
#  _request_ai：httpx 边界（请求体 / 超时 / 状态码 / 内容清洗）
# ══════════════════════════════════════════════════════════════


class _FakeResponse:
    def __init__(self, payload: dict | None = None, status_code: int = 200):
        self._payload = payload if payload is not None else {}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "http error",
                request=httpx.Request("POST", "http://fake/api/chat"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self._payload


class _FakeAsyncClient:
    calls: list[tuple[str, dict]] = []
    init_kwargs: dict = {}
    payload: dict = {"message": {"content": "   AI 内容  "}}
    status_code = 200
    error: Exception | None = None

    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        type(self).init_kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, url, json=None, **kwargs):  # noqa: ANN001
        type(self).calls.append((url, json))
        if type(self).error is not None:
            raise type(self).error
        return _FakeResponse(type(self).payload, type(self).status_code)


@pytest.fixture
def fake_http(monkeypatch):
    monkeypatch.setattr(ai.httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.init_kwargs = {}
    _FakeAsyncClient.payload = {"message": {"content": "   AI 内容  "}}
    _FakeAsyncClient.status_code = 200
    _FakeAsyncClient.error = None
    return _FakeAsyncClient


async def test_request_ai_payload_and_content_strip(fake_http):
    content = await ai._request_ai("提示词内容")

    assert content == "AI 内容"
    url, payload = fake_http.calls[0]
    assert url == f"{settings.ollama_base_url}/api/chat"
    assert fake_http.init_kwargs["timeout"] == ai.AI_TIMEOUT_SECONDS == 120.0
    assert payload["model"] == settings.ollama_vision_model
    assert payload["stream"] is False
    assert payload["think"] is False
    assert payload["options"] == {"temperature": 0.3, "num_predict": 1024}
    assert payload["messages"] == [{"role": "user", "content": "提示词内容"}]


async def test_request_ai_missing_message_key_returns_empty(fake_http):
    fake_http.payload = {}
    assert await ai._request_ai("x") == ""


async def test_request_ai_http_error_propagates(fake_http):
    fake_http.status_code = 500
    with pytest.raises(httpx.HTTPStatusError):
        await ai._request_ai("x")


# ══════════════════════════════════════════════════════════════
#  generate_summary：404 / 空响应 503 / 成功且不写库
# ══════════════════════════════════════════════════════════════


def _create_collection(client, name: str) -> int:
    resp = client.post("/api/collections", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _seed_items(collection_id: int, specs: list[dict]) -> None:
    async with async_session() as db:
        for position, spec in enumerate(specs):
            db.add(
                Inspiration(
                    id=spec["id"],
                    source_type="manual_upload",
                    file_path=f"images/{spec['id']}.jpg",
                    media_type="image",
                    quality_status="approved",
                    caption=spec.get("caption"),
                    dominant_colors=(
                        json.dumps(spec["colors"])
                        if isinstance(spec.get("colors"), list)
                        else spec.get("colors")
                    ),
                    rating=spec.get("rating", 0),
                )
            )
            db.add(
                CollectionItem(
                    collection_id=collection_id,
                    inspiration_id=spec["id"],
                    position=position,
                )
            )
        await db.commit()


async def test_generate_summary_unknown_collection_404():
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await ai.generate_summary(db, 987654)
    assert exc.value.status_code == 404
    assert exc.value.detail == "合集未找到"


async def test_generate_summary_empty_collection_400(client):
    collection_id = _create_collection(client, "空合集直调")
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await ai.generate_summary(db, collection_id)
    assert exc.value.status_code == 400
    assert "合集为空" in exc.value.detail


@pytest.mark.parametrize("raw", ["", None])
async def test_generate_summary_empty_ai_response_503(client, monkeypatch, raw):
    collection_id = _create_collection(client, "空响应合集")
    await _seed_items(collection_id, [{"id": "empty-ai-1", "colors": ["#fff"]}])

    async def _empty(_prompt: str):
        return raw

    monkeypatch.setattr(ai, "_request_ai", _empty)
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await ai.generate_summary(db, collection_id)
    assert exc.value.status_code == 503
    assert exc.value.detail == "AI 服务暂不可用，请稍后重试"


async def test_generate_summary_http_failure_maps_to_503(client, fake_http):
    collection_id = _create_collection(client, "HTTP失败合集")
    await _seed_items(collection_id, [{"id": "http-fail-1", "colors": ["#fff"]}])
    fake_http.error = httpx.ConnectError("refused")

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await ai.generate_summary(db, collection_id)
    assert exc.value.status_code == 503
    assert exc.value.detail == "AI 服务暂不可用，请稍后重试"


async def test_generate_summary_success_via_http_and_does_not_write(
    client, fake_http
):
    """真实 `_request_ai` 路径（假 httpx）：返回预览字段，且不修改合集/成员。"""
    collection_id = _create_collection(client, "成功预览合集")
    await _seed_items(
        collection_id,
        [
            {"id": "ok-1", "caption": "法式长裙", "colors": '["#ffffff"]'},
            {"id": "ok-2", "caption": "通勤衬衫"},
        ],
    )
    fake_http.payload = {
        "message": {
            "content": json.dumps(
                {
                    "summary": "法式与通勤混搭。",
                    "name_suggestions": ["法式通勤精选", "优雅日常", "灵感簿"],
                    "reason": "按场景收集。",
                    "theme_tags": ["法式", "通勤", "长裙", "优雅", "日常"],
                    "groups": [{"tag": "#ffffff", "name": "白色主题"}],
                },
                ensure_ascii=False,
            )
        }
    }

    async with async_session() as db:
        before = await db.get(Collection, collection_id)
        before_name = before.name
        before_description = before.description
        before_updated_at = before.updated_at
        before_items = await db.scalar(
            select(func.count(CollectionItem.inspiration_id)).where(
                CollectionItem.collection_id == collection_id
            )
        )

        result = await ai.generate_summary(db, collection_id)
        # 重复调用幂等：预览只读，再调一次也不产生任何写入
        repeated = await ai.generate_summary(db, collection_id)

        after = await db.get(Collection, collection_id)
        after_items = await db.scalar(
            select(func.count(CollectionItem.inspiration_id)).where(
                CollectionItem.collection_id == collection_id
            )
        )

    assert result["summary"] == "法式与通勤混搭。"
    assert result["name_suggestions"] == ["法式通勤精选", "优雅日常", "灵感簿"]
    assert result["reason"] == "按场景收集。"
    # AI 只给 5 个主题标签（下限）→ 规则分组 tag 补到第 6 个（仍 ≤ 8）
    assert result["theme_tags"] == [
        "法式",
        "通勤",
        "长裙",
        "优雅",
        "日常",
        "#ffffff",
    ]
    assert result["model"] == settings.ollama_vision_model
    assert result["generated_at"].endswith("Z")
    assert result["groups"][0]["name"] == "白色主题"
    assert repeated["summary"] == result["summary"]
    assert repeated["groups"] == result["groups"]
    # 不写库：合集名称/描述/更新时间与成员数都不变
    assert (after.name, after.description, after.updated_at) == (
        before_name,
        before_description,
        before_updated_at,
    )
    assert after_items == before_items == 2
    # 提示词里带合集名
    assert "成功预览合集" in fake_http.calls[0][1]["messages"][0]["content"]
