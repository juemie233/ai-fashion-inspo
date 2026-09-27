"""内层关联的「漏预加载」守卫：必须当场点名报错，而不是在序列化深处抛 MissingGreenlet。

背景（2026-09-27「素材没有相似推荐」事故）：`inspiration_to_out` 会读关联的内层实体
（`t.tag` / `t.blogger` / `t.model`）。模型里外层集合是 `lazy="selectin"`（自动加载），
内层实体原先是默认 `lazy="select"`——漏了链式预加载时：
- 只在「候选真的带人物关联」这种数据下才炸（测试库空关系 → 永远绿）；
- 报错是 greenlet 深处那句 `MissingGreenlet: greenlet_spawn has not been called`，
  既看不出是哪个属性，也看不出是漏预加载；
- 前端把它 catch 成「暂无相似素材」，症状指向完全错误的方向。

现在三个内层关系都是 `lazy="raise_on_sql"`：漏预加载会立刻抛 InvalidRequestError 并
点名属性。本文件锁住这个可发现性保障，防止有人把它改回默认值。
"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import InvalidRequestError

from app.database import async_session
from app.models.inspiration import Inspiration


async def test_blogger_inner_entity_raises_when_not_eager_loaded(client, upload, create_blogger):
    """只查素材本体、不链式加载 bloggers.blogger 时，访问内层实体必须当场报错。"""
    insp_id = upload().json()["id"]
    blogger = create_blogger(name="守卫用例博主")
    r = client.post(
        f"/api/inspirations/{insp_id}/bloggers", json={"person_ids": [blogger["id"]]}
    )
    assert r.status_code == 200, r.text

    async with async_session() as db:
        # 故意不给 options：bloggers 集合会自动加载（lazy="selectin"），内层 blogger 不会
        insp = (
            await db.execute(select(Inspiration).where(Inspiration.id == insp_id))
        ).unique().scalar_one()
        assert insp.bloggers, "夹具没挂上博主关联，本用例失去意义"

        with pytest.raises(InvalidRequestError) as exc:
            _ = insp.bloggers[0].blogger

    message = str(exc.value)
    assert "blogger" in message.lower(), f"报错没点名属性（定位成本会很高）: {message}"


async def test_model_inner_entity_raises_when_not_eager_loaded(client, upload, create_model):
    """同理：models.model 漏预加载也必须当场报错。"""
    insp_id = upload().json()["id"]
    model = create_model(name="守卫用例模特")
    r = client.post(
        f"/api/inspirations/{insp_id}/models", json={"person_ids": [model["id"]]}
    )
    assert r.status_code == 200, r.text

    async with async_session() as db:
        insp = (
            await db.execute(select(Inspiration).where(Inspiration.id == insp_id))
        ).unique().scalar_one()
        assert insp.models, "夹具没挂上模特关联，本用例失去意义"

        with pytest.raises(InvalidRequestError) as exc:
            _ = insp.models[0].model

    assert "model" in str(exc.value).lower()


async def test_tag_inner_entity_raises_when_not_eager_loaded(client, upload):
    """同理：tags.tag 漏预加载也必须当场报错。"""
    insp_id = upload().json()["id"]
    r = client.post(
        f"/api/inspirations/{insp_id}/tags", json={"names": ["白色"], "category": "color"}
    )
    assert r.status_code == 200, r.text

    async with async_session() as db:
        insp = (
            await db.execute(select(Inspiration).where(Inspiration.id == insp_id))
        ).unique().scalar_one()
        assert insp.tags, "夹具没挂上标签关联，本用例失去意义"

        with pytest.raises(InvalidRequestError) as exc:
            _ = insp.tags[0].tag

    assert "tag" in str(exc.value).lower()
