"""人物列表「素材数」排序：表头点击 = sort=count + order=asc/desc（2026-10 新增）。

后端此前只支持「素材数倒序」；为了让素材数列可点击排序，路由加了 order 参数，
平铺与「人物组折叠」两条路径都必须按同一口径排序（同数按 id 兜底，保证分页稳定）。

2026-11-19 追加：两级排序（sort2/order2）与「评分排序隐藏样本不足」用例，见文末。
"""

import asyncio

from sqlalchemy import text

from app.database import async_session
from app.models.inspiration import Inspiration
from app.models.person import InspirationBlogger, InspirationModel


async def _attach(inspiration_ids: list[str], blogger_id: int) -> None:
    """把素材挂到博主名下（直接写关联表，绕过上传链路的关系组装）。"""
    async with async_session() as db:
        for insp_id in inspiration_ids:
            await db.execute(
                text(
                    "INSERT INTO inspiration_bloggers (inspiration_id, blogger_id, confidence) "
                    "VALUES (:i, :b, 1.0)"
                ),
                {"i": insp_id, "b": blogger_id},
            )
        await db.commit()


def test_count_sort_order_toggles(client, upload, create_blogger):
    """order=desc 素材多的在前；order=asc 素材少的在前（0 素材的人物也能排上来）。"""
    busy = create_blogger("素材多")
    idle = create_blogger("素材少")

    ids = []
    for _ in range(2):
        r = upload()
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    asyncio.run(_attach(ids, busy["id"]))

    def ids_of(params: dict) -> list[int]:
        body = client.get("/api/bloggers", params=params).json()
        return [x["id"] for x in body["items"]]

    desc_ids = ids_of({"sort": "count", "order": "desc", "grouped": False})
    asc_ids = ids_of({"sort": "count", "order": "asc", "grouped": False})

    assert desc_ids.index(busy["id"]) < desc_ids.index(idle["id"]), desc_ids
    assert asc_ids.index(idle["id"]) < asc_ids.index(busy["id"]), asc_ids


def test_count_sort_order_rejects_unknown(client):
    """order 只接受 asc / desc（非法值由 FastAPI 直接 422）。"""
    r = client.get("/api/bloggers", params={"sort": "count", "order": "sideways"})
    assert r.status_code == 422


def test_list_returns_last_inspiration_at(client, upload, create_blogger):
    """列表响应必须带 last_inspiration_at（「最近素材」列的数据源）。

    回归：服务层 _to_dict 早就算出了该字段，但 ``response_model=BloggerListOut`` 会
    **过滤掉未在 schema 里声明的字段**——漏声明时接口静默少字段，前端列永远显示 "-"。
    """
    blogger = create_blogger("有最近素材的博主")
    insp_id = upload().json()["id"]
    asyncio.run(_attach([insp_id], blogger["id"]))

    body = client.get(
        "/api/bloggers", params={"search": "有最近素材的博主", "grouped": False}
    ).json()
    item = body["items"][0]
    assert item["id"] == blogger["id"]
    assert item["inspiration_count"] == 1
    assert item.get("last_inspiration_at"), item

    # 没有素材的博主：字段存在但为 None（前端显示 "-"，而不是 undefined）
    lonely = create_blogger("没有素材的博主")
    body = client.get(
        "/api/bloggers", params={"search": "没有素材的博主", "grouped": False}
    ).json()
    assert body["items"][0]["id"] == lonely["id"]
    assert body["items"][0]["last_inspiration_at"] is None


# ── 两级排序 + 评分排序样本过滤（2026-11-19）──


async def _add_blogger_images(blogger_id: int, ratings: list[int]) -> None:
    """给博主造图片素材，``ratings`` 是每张的评分（0 = 未评分）。"""
    async with async_session() as db:
        for index, rating in enumerate(ratings):
            iid = f"insp-b{blogger_id}-{index}"
            db.add(
                Inspiration(
                    id=iid,
                    source_type="xiaohongshu",
                    file_path=f"images/{iid}.jpg",
                    media_type="image",
                    quality_status="approved",
                    rating=rating,
                )
            )
            db.add(InspirationBlogger(inspiration_id=iid, blogger_id=blogger_id))
        await db.commit()


async def _add_model_images(model_id: int, ratings: list[int]) -> None:
    """给模特造图片素材。"""
    async with async_session() as db:
        for index, rating in enumerate(ratings):
            iid = f"insp-m{model_id}-{index}"
            db.add(
                Inspiration(
                    id=iid,
                    source_type="xiaohongshu",
                    file_path=f"images/{iid}.jpg",
                    media_type="image",
                    quality_status="approved",
                    rating=rating,
                )
            )
            db.add(InspirationModel(inspiration_id=iid, model_id=model_id))
        await db.commit()


def _ids(payload: dict) -> list[int]:
    return [item["id"] for item in payload["items"]]


def _link_group(client, blogger_a: int, blogger_b: int) -> int:
    """把两位博主绑成一个人物组（方案 B），返回组 id。"""
    resp = client.post(
        "/api/bloggers/groups/link",
        json={"blogger_id": blogger_a, "target_blogger_id": blogger_b},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["group_id"]


async def test_rating_sort_keeps_pagination_for_insufficient_samples(client, create_blogger):
    """回归：评分排序曾隐藏样本不足导致 total 不足一页、没有下一页。"""
    for index in range(21):
        blogger_id = create_blogger(f"分页样本{index}")["id"]
        await _add_blogger_images(blogger_id, [5, 5, 5, 5])  # 已评4 → 样本不足

    first = client.get(
        "/api/bloggers",
        params={"sort": "rating", "page": 1, "size": 20, "grouped": "false"},
    ).json()
    second = client.get(
        "/api/bloggers",
        params={"sort": "rating", "page": 2, "size": 20, "grouped": "false"},
    ).json()
    assert first["total"] == 21
    assert len(first["items"]) == 20
    assert len(second["items"]) == 1
    assert second["total"] == 21


# ── 平铺路径：主 count + 次 rating ──


async def test_primary_count_secondary_rating(client, create_blogger):
    """主排序 count（desc/asc）+ 次排序 rating；样本不足保留但排在评分级别最后。"""
    a = create_blogger("A")["id"]
    await _add_blogger_images(a, [5, 5, 5, 5, 5])  # count5 / 已评5 / 均分5
    b = create_blogger("B")["id"]
    await _add_blogger_images(b, [3, 3, 3, 3, 3])  # count5 / 已评5 / 均分3
    c = create_blogger("C")["id"]
    await _add_blogger_images(c, [5, 5, 5, 5])  # 已评4 → 评分排序时隐藏
    d = create_blogger("D")["id"]
    await _add_blogger_images(d, [1, 1, 1, 1, 1, 1, 1])  # count7 / 均分1

    desc = client.get(
        "/api/bloggers",
        params={
            "sort": "count",
            "order": "desc",
            "sort2": "rating",
            "grouped": "false",
        },
    ).json()
    assert desc["total"] == 4  # C 样本不足也保留，分页可继续
    # count desc：D(7) 先；A/B/C 同为 5 → 次排序 rating desc：A(5) → B(3) → C(样本不足)
    assert _ids(desc) == [d, a, b, c]

    asc = client.get(
        "/api/bloggers",
        params={
            "sort": "count",
            "order": "asc",
            "sort2": "rating",
            "grouped": "false",
        },
    ).json()
    assert _ids(asc) == [c, a, b, d]


# ── 平铺路径：主 rating + 次 count ──


async def test_primary_rating_secondary_count(client, create_blogger):
    """主排序 rating + 次排序 count（order2 生效），样本不足排最后。"""
    e = create_blogger("E")["id"]
    await _add_blogger_images(e, [5, 5, 5])  # 已评3 → 隐藏
    f = create_blogger("F")["id"]
    await _add_blogger_images(f, [5, 5, 5, 5, 5])  # count5 / 均分5
    g = create_blogger("G")["id"]
    # 6 张图里 5 张已评（均分与 F 相同、已评数也相同），次排序 count 才能生效
    await _add_blogger_images(g, [5, 5, 5, 5, 5, 0])
    h = create_blogger("H")["id"]
    await _add_blogger_images(h, [3, 3, 3, 3, 3])  # count5 / 均分3

    desc = client.get(
        "/api/bloggers",
        params={
            "sort": "rating",
            "sort2": "count",
            "order2": "desc",
            "grouped": "false",
        },
    ).json()
    assert desc["total"] == 4
    # 样本足够者先：G/F(5)、H(3)；同均分按次键 count desc → G(6) 先于 F(5)；
    # E 样本不足（已评3）排最后
    assert _ids(desc) == [g, f, h, e]

    asc = client.get(
        "/api/bloggers",
        params={
            "sort": "rating",
            "sort2": "count",
            "order2": "asc",
            "grouped": "false",
        },
    ).json()
    # 主排序 rating 的方向由 order 控制（默认 desc）；order2 只影响次排序 count
    assert _ids(asc) == [f, g, h, e]


# ── 评分排序的样本过滤 / 恢复全量 ──


async def test_rating_sort_keeps_insufficient_samples_at_end(client, create_blogger):
    """评分排序保留样本不足者但排在最后，total 不缩水，分页可继续。"""
    low_sample = create_blogger("样本不足")["id"]
    await _add_blogger_images(low_sample, [5, 5, 5, 5])  # 已评4，均分5
    good_low = create_blogger("样本够低分")["id"]
    await _add_blogger_images(good_low, [1] * 5)  # 已评5，均分1
    good_high = create_blogger("样本够高分")["id"]
    await _add_blogger_images(good_high, [4] * 6)  # 已评6，均分4

    desc = client.get(
        "/api/bloggers", params={"sort": "rating", "grouped": "false"}
    ).json()
    assert desc["total"] == 3
    assert _ids(desc) == [good_high, good_low, low_sample]

    asc = client.get(
        "/api/bloggers", params={"sort": "rating_asc", "grouped": "false"}
    ).json()
    assert _ids(asc) == [good_low, good_high, low_sample]

    # 次排序是 rating 时也保留，并在该排序级别内把样本不足排最后
    by_name = client.get(
        "/api/bloggers",
        params={"sort": "name", "sort2": "rating", "grouped": "false"},
    ).json()
    assert by_name["total"] == 3
    assert set(_ids(by_name)) == {low_sample, good_low, good_high}

    # 清空评分排序同样全量（作为回退基准）
    all_rows = client.get(
        "/api/bloggers", params={"sort": "newest", "grouped": "false"}
    ).json()
    assert all_rows["total"] == 3
    assert low_sample in _ids(all_rows)


# ── 折叠路径：组合并后过滤/排序 ──


async def test_grouped_rating_keeps_insufficient_samples(client, create_blogger):
    """人物组按组合并后的 rated_count 排序；样本不足保留并排最后。"""
    # 组1：两个账号分别已评 3 张与 2 张 → 组合并后已评 5（达到门槛）
    g1a = create_blogger("组1账号A", platform="xiaohongshu")["id"]
    g1b = create_blogger("组1账号B", platform="douyin")["id"]
    _link_group(client, g1a, g1b)
    await _add_blogger_images(g1a, [5, 5, 5])
    await _add_blogger_images(g1b, [4, 4])
    # 组2：两个账号各已评 2 张 → 合并后 4，评分排序时隐藏
    g2a = create_blogger("组2账号A", platform="xiaohongshu")["id"]
    g2b = create_blogger("组2账号B", platform="douyin")["id"]
    _link_group(client, g2a, g2b)
    await _add_blogger_images(g2a, [5, 5])
    await _add_blogger_images(g2b, [5, 5])
    # 独立账号：已评 5 且均分 5
    solo = create_blogger("独立")["id"]
    await _add_blogger_images(solo, [5] * 5)

    rated = client.get("/api/bloggers", params={"sort": "rating"}).json()
    assert rated["total"] == 3  # 组2 也保留，分页不缩水
    ids = _ids(rated)
    assert solo in ids
    assert g1a in ids  # 组主账号 = 素材数最多者（A 有 3 张 > B 有 2 张）
    assert g2a in ids
    # 组1 的组均分 = (5*3 + 4*2)/5 = 4.6，独立账号 5.0；组2 已评4 样本不足排最后
    assert ids == [solo, g1a, g2a]

    # 不选评分排序 → 恢复全量（组1、组2、独立共 3 行）
    plain = client.get("/api/bloggers", params={"sort": "count"}).json()
    assert plain["total"] == 3

    # 折叠路径的两级排序也生效：主 count + 次 rating
    two_level = client.get(
        "/api/bloggers",
        params={"sort": "count", "order": "desc", "sort2": "rating"},
    ).json()
    assert _ids(two_level) == [solo, g1a, g2a]  # count desc：独立(5) > 组1(3) > 组2(2)
    two_level_asc = client.get(
        "/api/bloggers",
        params={"sort": "count", "order": "asc", "sort2": "rating"},
    ).json()
    assert _ids(two_level_asc) == [g2a, g1a, solo]


# ── 单排序兼容 / 重复键去重 / 参数校验 ──


async def test_single_sort_compatibility(client, create_blogger):
    """不传 sort2/order2 时保持原单排序行为（不触发样本过滤）。"""
    x = create_blogger("X")["id"]
    await _add_blogger_images(x, [0])  # 1 张未评分
    y = create_blogger("Y")["id"]
    await _add_blogger_images(y, [5, 5])
    z = create_blogger("Z")["id"]
    await _add_blogger_images(z, [5, 5, 5])

    asc = client.get(
        "/api/bloggers", params={"sort": "count", "order": "asc", "grouped": "false"}
    ).json()
    assert _ids(asc) == [x, y, z]
    desc = client.get(
        "/api/bloggers", params={"sort": "count", "grouped": "false"}
    ).json()
    assert _ids(desc) == [z, y, x]
    plain = client.get(
        "/api/bloggers", params={"sort": "newest", "grouped": "false"}
    ).json()
    assert plain["total"] == 3


async def test_duplicate_sort_key_is_deduped(client, create_blogger):
    """主/次同键（rating 与 rating_asc）时去重：按主排序方向，不产生方向冲突。"""
    high = create_blogger("高分")["id"]
    await _add_blogger_images(high, [5] * 5)
    low = create_blogger("低分")["id"]
    await _add_blogger_images(low, [1] * 5)

    data = client.get(
        "/api/bloggers",
        params={
            "sort": "rating",
            "sort2": "rating_asc",
            "grouped": "false",
        },
    ).json()
    assert _ids(data) == [high, low]  # 仍按主排序 rating 降序


def test_sort_params_validation(client):
    """sort2/order2 非法值 → 422。"""
    assert (
        client.get("/api/bloggers", params={"sort2": "bogus"}).status_code == 422
    )
    assert (
        client.get("/api/bloggers", params={"order2": "up"}).status_code == 422
    )
    assert (
        client.get("/api/models", params={"sort2": "bogus"}).status_code == 422
    )
    assert client.get("/api/models", params={"order2": "up"}).status_code == 422


# ── 模特列表：两级排序 + 样本过滤 ──


async def test_models_two_level_sort_and_filter(client, create_model):
    """模特列表同样支持 sort2/order2；评分排序保留样本不足并排最后。"""
    m1 = create_model("M1")["id"]
    await _add_model_images(m1, [5] * 5)  # 已评5 / 均分5
    m2 = create_model("M2")["id"]
    await _add_model_images(m2, [3] * 5)  # 已评5 / 均分3
    m3 = create_model("M3")["id"]
    await _add_model_images(m3, [5] * 4)  # 已评4 → 隐藏

    data = client.get(
        "/api/models",
        params={"sort": "count", "order": "desc", "sort2": "rating"},
    ).json()
    assert data["total"] == 3
    assert _ids(data) == [m1, m2, m3]

    # 不带评分排序 → m3 恢复显示
    plain = client.get("/api/models", params={"sort": "count"}).json()
    assert plain["total"] == 3
