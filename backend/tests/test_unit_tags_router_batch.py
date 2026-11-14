"""标签管理路由（`routers/tags.py`）的批量治理 / 健康度 / 聚类接口测试。

这些接口是「长尾标签治理」的操作入口，平时点得少，但一出错就是**批量改坏数据**：
- `merge-batch` 一次合并几十上百个标签：单条失败不能中断其余，且不存在的 id
  不能算「合并成功」（否则数字不可信）；
- `rollback` 回滚操作要区分「记录不存在(404)」与「已被后续修改、不能回滚(409)」；
- 健康度/聚类扫描的阈值有业务区间，越界必须 422/400 而不是把无效任务塞进队列；
- 聚类应用允许「只传 group_id」复用最近一次扫描结果，但没扫描过要给出可操作提示。

本文件直接调用 handler 并 patch 服务层，覆盖这些分支（含 400/404/409 与成功路径）。
"""

import pytest
from fastapi import HTTPException

from app.database import async_session
from app.models.tag import Tag
from app.models.task import TaskQueue
from app.routers import tags as tags_router
from app.schemas.tag import ClusterApplyRequest, TagReorderRequest
from app.services import tag_dedupe_cache, tag_health, tag_history_service, tag_service


async def _tag(name: str, category: str = "风格") -> int:
    async with async_session() as db:
        tag = Tag(name=name, category=category)
        db.add(tag)
        await db.commit()
        return tag.id


async def _success_task(type_: str, result: dict) -> int:
    async with async_session() as db:
        task = TaskQueue(type=type_, status="success", total=0, done=0, result=result)
        db.add(task)
        await db.commit()
        return task.id


class TestMergeTagsBatch:
    async def test_validation_errors(self, client):
        async with async_session() as db:
            for payload in (
                {},  # 缺 source_ids
                {"source_ids": [], "target_id": 1},
                {"source_ids": "not-a-list", "target_id": 1},
                {"source_ids": [1]},  # 缺 target_id
                {"source_ids": [1, 2], "target_id": 2},  # 目标同时是源
                {"source_ids": list(range(501)), "target_id": 999},  # 超上限
            ):
                with pytest.raises(HTTPException) as exc_info:
                    await tags_router.merge_tags_batch(payload, db)
                assert exc_info.value.status_code == 400, payload
                await db.rollback()

    async def test_mixed_results_count_only_real_merges(self, client, monkeypatch):
        """单条失败不中断其余；不存在的 id 计入 failed 而不是 merged。"""
        source_a = await _tag("源A")
        source_b = await _tag("源B")
        source_c = await _tag("源C")
        target = await _tag("目标")

        async def _fake_merge(_db, source_id, target_id):
            if source_id == source_b:
                raise HTTPException(status_code=400, detail="不能合并到自己")
            if source_id == source_c:
                raise RuntimeError("模拟数据库错误")

        monkeypatch.setattr(tag_service, "merge_tags", _fake_merge)

        async with async_session() as db:
            result = await tags_router.merge_tags_batch(
                {
                    "source_ids": [source_a, source_b, source_c, 999999, "脏ID"],
                    "target_id": target,
                },
                db,
            )

        assert result["merged"] == 1  # 只有 source_a 真正合并
        failed = {str(item["id"]): item["reason"] for item in result["failed"]}
        assert failed[str(source_b)] == "不能合并到自己"
        assert "RuntimeError" in failed[str(source_c)]
        assert "标签不存在" in failed["999999"]
        assert failed["脏ID"] == "id 非法"


class TestFindDuplicates:
    async def test_cache_hit_returns_cached_and_truncates_to_50(self, client, monkeypatch):
        pairs = [{"a": i} for i in range(60)]
        monkeypatch.setattr(
            tag_dedupe_cache, "get_cached", lambda _key: {"pairs": pairs, "total": 60}
        )

        async with async_session() as db:
            result = await tags_router.find_duplicate_tags(threshold=0.8, db=db)

        assert result["cached"] is True
        assert result["total"] == 60
        assert len(result["duplicates"]) == 50  # 只回前 50 条给前端

    async def test_cache_miss_computes_and_stores(self, client, monkeypatch):
        stored: list = []
        monkeypatch.setattr(tag_dedupe_cache, "get_cached", lambda _key: None)
        monkeypatch.setattr(tag_dedupe_cache, "set_cached", lambda key, pairs, total: stored.append((key, total)))

        async def _fake_pairs(_db, threshold):
            return ([{"a": 1}, {"a": 2}], 2)

        monkeypatch.setattr(tag_service, "find_duplicate_tag_pairs", _fake_pairs)

        async with async_session() as db:
            result = await tags_router.find_duplicate_tags(threshold=0.85, db=db)

        assert result["cached"] is False
        assert len(result["duplicates"]) == 2
        assert stored and stored[0][1] == 2


class TestReorderAndRollback:
    async def test_reorder_rejects_empty(self, client):
        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await tags_router.reorder_tags(TagReorderRequest(items=[]), db)
        assert exc_info.value.status_code == 400

    async def test_reorder_missing_tag_404(self, client, monkeypatch):
        async def _fake(_db, order_map):
            return (1, [99])

        monkeypatch.setattr(tag_service, "reorder_tags", _fake)
        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await tags_router.reorder_tags(
                    TagReorderRequest(items=[{"id": 1, "sort_order": 0}]), db
                )
        assert exc_info.value.status_code == 404
        assert "无法排序" in exc_info.value.detail

    async def test_reorder_success(self, client, monkeypatch):
        async def _fake(_db, order_map):
            assert order_map == {1: 0, 2: 3}
            return (2, [])

        monkeypatch.setattr(tag_service, "reorder_tags", _fake)
        async with async_session() as db:
            result = await tags_router.reorder_tags(
                TagReorderRequest(
                    items=[{"id": 1, "sort_order": 0}, {"id": 2, "sort_order": 3}]
                ),
                db,
            )
        assert result == {"updated": 2}

    async def test_rollback_branches(self, client, monkeypatch):
        async with async_session() as db:
            async def _ok(_db, history_id):
                return {"rolled_back": history_id}

            monkeypatch.setattr(tag_history_service, "rollback_history", _ok)
            assert await tags_router.rollback_tag_history(5, db) == {"rolled_back": 5}

            async def _not_found(_db, _id):
                raise tag_history_service.TagHistoryNotFoundError

            monkeypatch.setattr(tag_history_service, "rollback_history", _not_found)
            with pytest.raises(HTTPException) as exc_info:
                await tags_router.rollback_tag_history(5, db)
            assert exc_info.value.status_code == 404

            async def _conflict(_db, _id):
                raise tag_history_service.TagHistoryRollbackError("已被后续修改")

            monkeypatch.setattr(tag_history_service, "rollback_history", _conflict)
            with pytest.raises(HTTPException) as exc_info:
                await tags_router.rollback_tag_history(5, db)
            assert exc_info.value.status_code == 409
            assert "已被后续修改" in exc_info.value.detail


class TestHealthScan:
    async def test_scan_rejects_out_of_range_threshold(self, client):
        async with async_session() as db:
            for threshold in (0.59, 0.96):
                with pytest.raises(HTTPException) as exc_info:
                    await tags_router.tag_health_scan(
                        {"duplicate_threshold": threshold}, db
                    )
                assert exc_info.value.status_code == 400

    async def test_scan_submits_task_with_threshold(self, client, monkeypatch):
        captured: dict = {}

        async def _fake(_db, duplicate_threshold):
            captured["threshold"] = duplicate_threshold
            return type("T", (), {"id": 7})()

        monkeypatch.setattr(
            "app.services.task_runner.create_tag_health_scan_task", _fake
        )
        async with async_session() as db:
            result = await tags_router.tag_health_scan({"duplicate_threshold": 0.8}, db)
        assert captured["threshold"] == 0.8
        assert result == {"message": "已提交健康度扫描任务 #7", "task_id": 7}

    async def test_issues_without_scan_404(self, client):
        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await tags_router.tag_health_issues("orphan", 1, 50, db)
        assert exc_info.value.status_code == 404

    async def test_issues_bad_type_400_and_success(self, client, monkeypatch):
        await _success_task("tag_health_scan", {"issues": {}})

        async def _bad(_db, _result, _type, _page, _size):
            raise ValueError("未知问题类型")

        monkeypatch.setattr(tag_health, "get_health_issue_detail", _bad)
        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await tags_router.tag_health_issues("unknown", 1, 50, db)
        assert exc_info.value.status_code == 400

        async def _ok(_db, _result, issue_type, page, size):
            return {"items": [], "issue_type": issue_type, "page": page, "size": size}

        monkeypatch.setattr(tag_health, "get_health_issue_detail", _ok)
        async with async_session() as db:
            result = await tags_router.tag_health_issues("orphan", 2, 20, db)
        assert result["page"] == 2


class TestClusters:
    async def test_scan_validation_and_submit(self, client, monkeypatch):
        async with async_session() as db:
            with pytest.raises(HTTPException):
                await tags_router.tag_clusters_scan({"threshold": 0.5}, db)
            with pytest.raises(HTTPException) as exc_info:
                await tags_router.tag_clusters_scan({"min_group_size": 1}, db)
            assert "至少为 2" in exc_info.value.detail

        captured: dict = {}

        async def _fake(_db, threshold, use_cooccurrence_boost, min_group_size):
            captured.update(
                threshold=threshold,
                boost=use_cooccurrence_boost,
                size=min_group_size,
            )
            return type("T", (), {"id": 11})()

        monkeypatch.setattr(
            "app.services.task_runner.create_tag_cluster_scan_task", _fake
        )
        async with async_session() as db:
            result = await tags_router.tag_clusters_scan(
                {"threshold": 0.7, "min_group_size": 3}, db
            )
        assert captured == {"threshold": 0.7, "boost": True, "size": 3}
        assert result["task_id"] == 11

    async def test_apply_with_group_id_requires_scan(self, client):
        request = ClusterApplyRequest(groups=[{"group_id": "g1"}])
        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await tags_router.tag_clusters_apply(request, db)
        assert exc_info.value.status_code == 400
        assert "请先完成聚类扫描" in exc_info.value.detail

    async def test_apply_unknown_group_400(self, client):
        await _success_task("tag_cluster_scan", {"groups": []})
        request = ClusterApplyRequest(groups=[{"group_id": "nope"}])
        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await tags_router.tag_clusters_apply(request, db)
        assert exc_info.value.status_code == 400
        assert "候选组 nope 不存在" in exc_info.value.detail

    async def test_apply_resolves_group_members(self, client, monkeypatch):
        await _success_task(
            "tag_cluster_scan",
            {
                "groups": [
                    {
                        "id": "g1",
                        "suggested_target": {"id": 1},
                        "members": [{"id": 1}, {"id": 2}, {"id": 3}],
                    }
                ]
            },
        )
        captured: list = []

        async def _fake(_db, groups, batch_id):
            captured.append((groups, batch_id))
            return {"applied": len(groups)}

        monkeypatch.setattr("app.services.tag_cluster.apply_tag_clusters", _fake)
        request = ClusterApplyRequest(batch_id="b1", groups=[{"group_id": "g1"}])
        async with async_session() as db:
            result = await tags_router.tag_clusters_apply(request, db)

        assert result == {"applied": 1}
        groups, batch_id = captured[0]
        assert batch_id == "b1"
        assert groups[0]["target_tag_id"] == 1
        assert groups[0]["source_tag_ids"] == [2, 3]  # 排除目标自身


class TestSmallHandlers:
    async def test_watchlist_keep_validation_and_success(self, client, monkeypatch):
        async with async_session() as db:
            with pytest.raises(HTTPException):
                await tags_router.tag_watchlist_keep({}, db)
            with pytest.raises(HTTPException):
                await tags_router.tag_watchlist_keep({"tag_ids": "x"}, db)

        async def _fake_keep(_db, ids):
            return len(ids)

        monkeypatch.setattr("app.services.tag_query.keep_watch_tags", _fake_keep)
        async with async_session() as db:
            result = await tags_router.tag_watchlist_keep({"tag_ids": [1, 2]}, db)
        assert result == {"kept": 2}

    async def test_move_tags(self, client, monkeypatch):
        async def _fake(_db, moves):
            return (len(moves), [{"tag_id": 9, "reason": "不存在"}])

        monkeypatch.setattr("app.services.tag_crud.move_tags", _fake)
        async with async_session() as db:
            result = await tags_router.tag_move({"moves": [{"tag_id": 1, "parent_id": None}]}, db)
        assert result == {"moved": 1, "errors": [{"tag_id": 9, "reason": "不存在"}]}

    async def test_tree_and_top(self, client, monkeypatch):
        async def _tree(_db, parent_id, page, size):
            return {"items": [], "parent_id": parent_id, "page": page, "size": size}

        async def _top(_db, limit):
            return [{"id": 1, "count": 9}][:limit]

        monkeypatch.setattr("app.services.tag_query.get_tag_tree_children", _tree)
        monkeypatch.setattr(tag_service, "get_top_tags", _top)
        async with async_session() as db:
            assert (await tags_router.tag_tree(None, 1, 200, db))["parent_id"] is None
            assert await tags_router.top_tags(5, db) == [{"id": 1, "count": 9}]

    async def test_tag_trend_404_and_success(self, client, monkeypatch):
        async def _empty(_db, _tag_id, _granularity):
            return None

        monkeypatch.setattr(tag_service, "get_tag_trend", _empty)
        async with async_session() as db:
            with pytest.raises(HTTPException) as exc_info:
                await tags_router.tag_trend(1, "month", db)
        assert exc_info.value.status_code == 404

        async def _ok(_db, tag_id, granularity):
            return {"tag_id": tag_id, "granularity": granularity, "points": []}

        monkeypatch.setattr(tag_service, "get_tag_trend", _ok)
        async with async_session() as db:
            assert (await tags_router.tag_trend(2, "week", db))["granularity"] == "week"
