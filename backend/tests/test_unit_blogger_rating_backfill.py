"""博主素材评分补全任务测试（目标排序 / >5 门槛 / 只补未评分 / 执行器 / 取消）。

覆盖需求（2026-11-18「人物页补全评分」）：
- 覆盖所有有未评分素材的博主；素材数 > 5 的优先（按素材数升序），其余随后，
  同数按 id 升序；
- 只更新 ``rating`` 为 0/空且未删除的素材，**已评分绝不覆盖**；
- 显式传 ``blogger_ids`` 时按给定范围补；
- 无目标 400、参数越界 422；
- 执行器按博主分批 commit、进度/明细写入 result、可取消（取消保留已补数据）。
"""

import pytest
from sqlalchemy import select, update

from app.database import async_session
from app.models.inspiration import Inspiration
from app.models.person import Blogger, InspirationBlogger
from app.models.task import TaskQueue
from app.services.task_runner import TASK_HANDLERS
from app.services.task_runners import blogger_rating_backfill as runner
from app.utils.time import utcnow

API = "/api/bloggers/rating-backfill"


async def _add_blogger(name: str) -> int:
    """插入一位博主，返回 id。"""
    async with async_session() as db:
        blogger = Blogger(name=name, platform="xiaohongshu")
        db.add(blogger)
        await db.commit()
        await db.refresh(blogger)
        return blogger.id


async def _add_material(
    insp_id: str,
    blogger_id: int,
    *,
    rating: int = 0,
    deleted: bool = False,
) -> None:
    """插入一条素材并关联到博主（deleted=True 时进垃圾桶，不进有效素材统计）。"""
    async with async_session() as db:
        db.add(
            Inspiration(
                id=insp_id,
                source_type="xiaohongshu",
                file_path=f"images/{insp_id}.jpg",
                media_type="image",
                quality_status="approved",
                rating=rating,
                deleted_at=utcnow() if deleted else None,
            )
        )
        db.add(InspirationBlogger(inspiration_id=insp_id, blogger_id=blogger_id))
        await db.commit()


async def _seed_blogger(
    name: str,
    rating_counts: dict[int, int],
    *,
    deleted_count: int = 0,
) -> int:
    """建博主并造素材：``rating_counts`` = {评分: 条数}（0 = 未评分）。"""
    blogger_id = await _add_blogger(name)
    seq = 0
    for rating, count in rating_counts.items():
        for _ in range(count):
            seq += 1
            await _add_material(f"insp-{blogger_id}-{seq}", blogger_id, rating=rating)
    for _ in range(deleted_count):
        seq += 1
        await _add_material(
            f"insp-{blogger_id}-{seq}", blogger_id, rating=0, deleted=True
        )
    return blogger_id


async def _fetch_ratings(blogger_id: int) -> dict[str, int]:
    """取某博主名下素材的 {素材 id: rating}（含垃圾桶素材，便于断言未被改）。"""
    async with async_session() as db:
        rows = (
            await db.execute(
                select(Inspiration.id, Inspiration.rating)
                .join(
                    InspirationBlogger,
                    InspirationBlogger.inspiration_id == Inspiration.id,
                )
                .where(InspirationBlogger.blogger_id == blogger_id)
            )
        ).all()
    return {row[0]: row[1] for row in rows}


async def _get_task(task_id: int) -> TaskQueue:
    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
        assert task is not None
        return task


# ── 接口：建任务 / 400 / 参数校验 ──


async def test_create_task_targets_order_and_threshold(client):
    """目标：覆盖所有有未评分素材的博主；>5 的优先，其余随后，均按素材数升序。"""
    blogger_a = await _seed_blogger("博主A", {0: 2, 3: 4})  # 6 条，2 未评分
    blogger_b = await _seed_blogger("博主B", {0: 7})  # 7 条，7 未评分
    small = await _seed_blogger("少素材", {0: 5})  # 5 条：排在大博主之后
    await _seed_blogger("全已评分", {4: 8})  # 8 条但 0 未评分，不参与

    resp = client.post(API, json={"rating": 5})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["total"] == 14
    assert "位博主的 14 条未评分素材" in body["message"]

    task = await _get_task(body["task_id"])
    payload = task.result or {}
    assert task.type == "blogger_rating_backfill"
    assert task.status == "pending"
    assert task.total == 14
    assert payload["rating"] == 5
    assert payload["total"] == 14
    assert payload["blogger_ids"] == [blogger_a, blogger_b, small]
    assert [t["blogger_id"] for t in payload["blogger_targets"]] == [
        blogger_a,
        blogger_b,
        small,
    ]
    assert [t["unrated_materials"] for t in payload["blogger_targets"]] == [2, 7, 5]


async def test_targets_sorted_by_count_then_blogger_id(client):
    """素材数相同按博主 id 升序；数量少的排前面。"""
    first = await _seed_blogger("同数一", {0: 6})
    second = await _seed_blogger("同数二", {0: 6})
    bigger = await _seed_blogger("更多", {0: 9})

    async with async_session() as db:
        targets = await runner.list_rating_backfill_targets(db)

    assert [t.blogger_id for t in targets] == [first, second, bigger]
    assert [t.total_materials for t in targets] == [6, 6, 9]


async def test_explicit_blogger_ids_scope(client):
    """显式 blogger_ids 时只补指定范围。"""
    small = await _seed_blogger("小博主", {0: 2})
    await _seed_blogger("大博主", {0: 6})

    resp = client.post(API, json={"rating": 4, "blogger_ids": [small]})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["total"] == 2
    assert "1 位博主的 2 条" in body["message"]

    task = await _get_task(body["task_id"])
    assert (task.result or {})["blogger_ids"] == [small]


async def test_no_target_returns_400(client):
    """没有任何可补素材 → 400（前端据此提示，不创建空任务）。"""
    await _seed_blogger("全部已评分", {5: 8})
    resp = client.post(API, json={"rating": 3})
    assert resp.status_code == 400
    assert "没有可补评分" in resp.json()["detail"]


async def test_explicit_missing_blogger_returns_400(client):
    """显式指定的博主没有可补素材（不存在/无素材）→ 400。"""
    resp = client.post(API, json={"rating": 3, "blogger_ids": [999999]})
    assert resp.status_code == 400


@pytest.mark.parametrize(
    "payload",
    [
        {},  # 缺 rating
        {"rating": 0},  # 低于下限
        {"rating": 6},  # 高于上限
        {"rating": 3.5},  # 非整数
        {"rating": 3, "blogger_ids": ["x"]},  # 元素类型错误
        {"rating": 3, "blogger_ids": 1},  # 整体类型错误
    ],
)
def test_request_validation_422(client, payload):
    """rating 必填且 1~5；blogger_ids 必须是整数数组。"""
    resp = client.post(API, json=payload)
    assert resp.status_code == 422, resp.text


async def test_cancel_endpoint_accepts_new_type(client):
    """新任务类型在运行中可取消（tasks.py 白名单同步新增）。"""
    await _seed_blogger("可取消", {0: 6})
    tid = client.post(API, json={"rating": 3}).json()["task_id"]
    async with async_session() as db:
        task = await db.get(TaskQueue, tid)
        assert task is not None
        task.status = "running"
        await db.commit()

    resp = client.post(f"/api/tasks/{tid}/cancel")
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] is False
    assert client.get(f"/api/tasks/{tid}").json()["status"] == "cancelled"


def test_task_handler_registered(client):
    """新任务类型必须登记 TASK_HANDLERS，否则 worker 认领后报「未知任务类型」。"""
    assert TASK_HANDLERS["blogger_rating_backfill"] is runner.execute_rating_backfill


# ── 执行器：只补未评分、进度/明细、取消 ──


async def test_execute_updates_only_unrated_and_undeleted(client):
    """只把 rating=0 的未删除素材写成统一星级；已评分与垃圾桶素材原样保留。"""
    blogger_a = await _seed_blogger("A", {0: 2, 5: 4})  # 6 条：2 未评分 + 4 已评 5
    blogger_b = await _seed_blogger("B", {0: 6}, deleted_count=1)  # 6 有效 + 1 垃圾桶

    async with async_session() as db:
        task, total = await runner.create_rating_backfill_task(db, 4)
        assert task is not None
        assert total == 8  # 垃圾桶素材不计入
        task.status = "running"
        await db.commit()
        await runner.execute_rating_backfill(db, task)
        task_id = task.id

    ratings = {**await _fetch_ratings(blogger_a), **await _fetch_ratings(blogger_b)}
    assert sorted(ratings.values()).count(4) == 8  # 8 条未评分 → 4
    assert sorted(ratings.values()).count(5) == 4  # 已评分 5 不被覆盖
    assert sorted(ratings.values()).count(0) == 1  # 垃圾桶素材保持 0

    done = await _get_task(task_id)
    payload = done.result or {}
    assert done.done == 8
    assert done.progress == 100
    assert payload["updated_materials"] == 8
    assert payload["updated_bloggers"] == 2
    assert payload["skipped"] == 0
    assert payload["failed"] == 0
    assert payload["cancelled"] is False
    assert [d["status"] for d in payload["bloggers"]] == ["updated", "updated"]


async def test_execute_skips_blogger_rated_after_task_created(client):
    """任务创建后用户手动评完某博主 → 执行时如实记 skipped，不误报更新。"""
    await _seed_blogger("A", {0: 6})
    blogger_b = await _seed_blogger("B", {0: 6})

    async with async_session() as db:
        task, total = await runner.create_rating_backfill_task(db, 5)
        assert task is not None
        assert total == 12
        task_id = task.id

    # 模拟用户并发给 B 手动评分
    async with async_session() as db:
        await db.execute(
            update(Inspiration)
            .where(
                Inspiration.id.in_(
                    select(InspirationBlogger.inspiration_id).where(
                        InspirationBlogger.blogger_id == blogger_b
                    )
                ),
                Inspiration.rating == 0,
            )
            .values(rating=2)
        )
        await db.commit()
        task = await db.get(TaskQueue, task_id)
        assert task is not None
        task.status = "running"
        await db.commit()
        await runner.execute_rating_backfill(db, task)

    done = await _get_task(task_id)
    payload = done.result or {}
    assert payload["updated_bloggers"] == 1
    assert payload["skipped"] == 1
    assert payload["updated_materials"] == 6
    assert done.done == 6
    assert done.progress == 100


async def test_execute_cancelled_stops_and_keeps_done(client, monkeypatch):
    """取消检查发生在博主之间：已补的保留，后面的不再补，任务终态 cancelled。"""
    blogger_a = await _seed_blogger("先处理", {0: 6})
    blogger_b = await _seed_blogger("后处理", {0: 6})

    async with async_session() as db:
        task, _total = await runner.create_rating_backfill_task(db, 2)
        assert task is not None
        task_id = task.id
        calls = {"n": 0}

        async def _cancel_check(_db, _task):
            calls["n"] += 1
            return calls["n"] >= 2  # 第 1 位博主前放行，第 2 位前取消

        monkeypatch.setattr(runner, "is_cancelled", _cancel_check)
        await runner.execute_rating_backfill(db, task)

    ratings_a = await _fetch_ratings(blogger_a)
    ratings_b = await _fetch_ratings(blogger_b)
    assert set(ratings_a.values()) == {2}  # 第一位已补
    assert set(ratings_b.values()) == {0}  # 第二位未处理

    done = await _get_task(task_id)
    payload = done.result or {}
    assert done.status == "cancelled"
    assert payload["cancelled"] is True
    assert payload["updated_bloggers"] == 1
    assert payload["updated_materials"] == 6
    assert done.done == 6
