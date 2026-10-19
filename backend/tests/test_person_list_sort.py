"""人物列表「素材数」排序：表头点击 = sort=count + order=asc/desc（2026-10 新增）。

后端此前只支持「素材数倒序」；为了让素材数列可点击排序，路由加了 order 参数，
平铺与「人物组折叠」两条路径都必须按同一口径排序（同数按 id 兜底，保证分页稳定）。
"""

import asyncio

from sqlalchemy import text

from app.database import async_session


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
