"""采集模块集成测试：插件任务记录、任务分页/筛选/统计、定时计划、Cookie 管理。

统一 monkeypatch 采集子进程启动，避免测试中拉起真实 Playwright。
"""

import pytest


@pytest.fixture(autouse=True)
def no_scraper_subprocess(monkeypatch):
    """禁用采集子进程启动（测试不运行真实爬虫）。"""
    from app.services import scraper_service

    monkeypatch.setattr(scraper_service, "_launch_scraper_process", lambda task_id: None)


def _create_task(client, platform="douyin", keywords=("穿搭",)):
    """创建采集任务（douyin 不触发 CDP 预检，xiaohongshu 需 cdp_port=None）。"""
    r = client.post(
        "/api/scraper/tasks",
        json={"platform": platform, "keywords": list(keywords), "max_count": 5},
    )
    assert r.status_code == 201, r.text
    return r.json()


class TestExtensionTaskFlow:
    """浏览器插件采集会话：任务记录创建 → 素材关联 → 汇总完成 → 结果查看/删除。"""

    def test_full_flow(self, client, upload):
        r = client.post(
            "/api/scraper/extension-tasks",
            json={"platform": "xiaohongshu", "source_url": "https://www.xiaohongshu.com/explore/abc"},
        )
        assert r.status_code == 201
        task_id = r.json()["id"]

        # 素材上传关联任务
        up = upload(scraper_task_id=str(task_id), source_type="browser_extension")
        assert up.status_code == 201, up.text

        # 汇总完成
        r = client.post(
            f"/api/scraper/extension-tasks/{task_id}/complete",
            json={"items_found": 3, "items_added": 1},
        )
        assert r.status_code == 200

        tasks = client.get("/api/scraper/tasks").json()
        t = next(x for x in tasks["items"] if x["id"] == task_id)
        assert t["status"] == "completed"
        assert t["platform"] == "browser_extension"
        assert t["items_found"] == 3
        assert t["items_added"] == 1
        assert t["diagnostics"]  # 有漏斗数据，前端可展示

        # 结果预览能查到该素材
        res = client.get(f"/api/scraper/tasks/{task_id}/results").json()
        assert res["total"] == 1
        assert len(res["items"]) == 1

        # 批量删除结果 → 软删除进入垃圾桶（而非物理删除），任务结果计数同步
        del_res = client.post(
            f"/api/scraper/tasks/{task_id}/results/batch-delete",
            json={"ids": [res["items"][0]["id"]]},
        )
        assert del_res.status_code == 200
        body = del_res.json()
        assert body["trashed_count"] == 1
        assert body["skipped"] == 0
        assert client.get(f"/api/scraper/tasks/{task_id}/results").json()["total"] == 0

        # 素材进入全局垃圾桶，可恢复
        trash = client.get("/api/inspirations/trash").json()
        assert trash["total"] == 1
        assert trash["items"][0]["id"] == res["items"][0]["id"]

        # 从垃圾桶恢复后重新出现在任务结果中
        r = client.post(f"/api/inspirations/{res['items'][0]['id']}/restore")
        assert r.status_code == 200
        assert client.get(f"/api/scraper/tasks/{task_id}/results").json()["total"] == 1

        # 已在垃圾桶中的素材重复删除：计入 skipped，不报错
        client.post(f"/api/inspirations/{res['items'][0]['id']}/trash")
        del_res2 = client.post(
            f"/api/scraper/tasks/{task_id}/results/batch-delete",
            json={"ids": [res["items"][0]["id"]]},
        )
        assert del_res2.json()["trashed_count"] == 0
        assert del_res2.json()["skipped"] == 1

    def test_upload_with_unknown_task_id(self, client, upload):
        up = upload(scraper_task_id="99999")
        assert up.status_code == 400
        assert "采集任务不存在" in up.json()["detail"]

    def test_complete_unknown_task(self, client):
        r = client.post("/api/scraper/extension-tasks/99999/complete", json={"items_found": 1, "items_added": 1})
        assert r.status_code == 404

    def test_complete_non_extension_task(self, client):
        task = _create_task(client)
        r = client.post(f"/api/scraper/extension-tasks/{task['id']}/complete", json={"items_found": 1, "items_added": 1})
        assert r.status_code == 400


class TestTaskListPagination:
    """任务列表分页、筛选与后端聚合统计。"""

    def test_pagination_and_stats(self, client):
        for i in range(3):
            _create_task(client, keywords=(f"关键词{i}",))
        _create_task(client, platform="xiaohongshu")

        page1 = client.get("/api/scraper/tasks", params={"size": 2}).json()
        assert page1["total"] == 4
        assert len(page1["items"]) == 2
        assert page1["stats"]["pending"] == 4  # 统计覆盖全部筛选结果而非当前页

        page2 = client.get("/api/scraper/tasks", params={"size": 2, "page": 2}).json()
        assert len(page2["items"]) == 2

        dy = client.get("/api/scraper/tasks", params={"platform": "douyin"}).json()
        assert dy["total"] == 3
        xhs = client.get("/api/scraper/tasks", params={"platform": "xiaohongshu"}).json()
        assert xhs["total"] == 1

    def test_sort_modes(self, client):
        _create_task(client, keywords=("a",))
        _create_task(client, keywords=("b",))
        newest = client.get("/api/scraper/tasks", params={"sort": "newest"}).json()
        oldest = client.get("/api/scraper/tasks", params={"sort": "oldest"}).json()
        assert [t["id"] for t in newest["items"]] == list(reversed([t["id"] for t in oldest["items"]]))


class TestSchedules:
    """定时采集计划：CRUD、启停、立即执行。"""

    def _create(self, client, **overrides):
        body = {"platform": "douyin", "keywords": ["穿搭"], "max_count": 10, "interval_minutes": 60}
        body.update(overrides)
        r = client.post("/api/scraper/schedules", json=body)
        assert r.status_code == 201, r.text
        return r.json()

    def test_crud_and_toggle(self, client):
        s = self._create(client)
        assert s["enabled"] is True
        assert s["next_run_at"] is not None
        assert s["keywords"] == ["穿搭"]
        sid = s["id"]

        # 停用：next_run_at 清空
        r = client.patch(f"/api/scraper/schedules/{sid}", json={"enabled": False})
        assert r.json()["next_run_at"] is None

        # 重新启用并改间隔：next_run_at 重新计算
        r = client.patch(f"/api/scraper/schedules/{sid}", json={"enabled": True, "interval_minutes": 720})
        s2 = r.json()
        assert s2["next_run_at"] is not None
        assert s2["interval_minutes"] == 720

        # 列表包含该计划
        assert any(x["id"] == sid for x in client.get("/api/scraper/schedules").json())

        # 删除 + 再删 404
        assert client.delete(f"/api/scraper/schedules/{sid}").status_code == 200
        assert client.delete(f"/api/scraper/schedules/{sid}").status_code == 404

    def test_validation(self, client):
        assert client.post(
            "/api/scraper/schedules", json={"platform": "bilibili", "keywords": ["x"]}
        ).status_code == 400
        assert client.post(
            "/api/scraper/schedules", json={"platform": "douyin", "keywords": []}
        ).status_code == 400
        assert client.post(
            "/api/scraper/schedules", json={"platform": "douyin", "keywords": ["x"], "interval_minutes": 5}
        ).status_code == 422  # 间隔低于下限

    def test_update_editable_fields(self, client):
        """更新计划的关键词/数量/排序/间隔，且「综合」归一化为 None。"""
        s = self._create(client, platform="xiaohongshu", keywords=["穿搭"], sort_mode="latest")
        assert s["sort_mode"] == "latest"

        r = client.patch(
            f"/api/scraper/schedules/{s['id']}",
            json={"keywords": ["法式", "通勤"], "max_count": 50, "sort_mode": "popular", "interval_minutes": 720},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["keywords"] == ["法式", "通勤"]
        assert body["max_count"] == 50
        assert body["sort_mode"] == "popular"
        assert body["interval_minutes"] == 720

        r2 = client.patch(f"/api/scraper/schedules/{s['id']}", json={"sort_mode": "general"})
        assert r2.status_code == 200
        assert r2.json()["sort_mode"] is None

    def test_run_now_creates_task(self, client, monkeypatch):
        from app.services import scraper_service

        # 定时计划「立即执行」对小红书做 CDP 预检；测试环境无 Chrome，mock 为可用
        monkeypatch.setattr(scraper_service, "_check_cdp", lambda port, timeout=2.0: (True, "ok", True))

        s = self._create(client, platform="xiaohongshu", keywords=["法式"], sort_mode="latest")
        r = client.post(f"/api/scraper/schedules/{s['id']}/run")
        assert r.status_code == 200
        task_id = r.json()["task_id"]

        tasks = client.get("/api/scraper/tasks").json()
        t = next(x for x in tasks["items"] if x["id"] == task_id)
        assert t["platform"] == "xiaohongshu"
        import json as _json

        assert _json.loads(t["config"])["sort_mode"] == "latest"

    def test_run_now_xiaohongshu_requires_chrome(self, client, monkeypatch):
        """小红书计划「立即执行」前做 CDP 预检，Chrome 不可用时返回 400。"""
        from app.services import scraper_service

        monkeypatch.setattr(
            scraper_service, "_check_cdp", lambda port, timeout=2.0: (False, "端口无响应", False)
        )
        s = self._create(client, platform="xiaohongshu", keywords=["法式"])
        r = client.post(f"/api/scraper/schedules/{s['id']}/run")
        assert r.status_code == 400
        assert "Chrome" in r.json()["detail"]

    def test_advance_next_run_keeps_rhythm(self):
        """next_run_at 从原到期点推进，而非从当前时间重置（防节奏漂移）。"""
        from datetime import datetime, timedelta, timezone

        from app.services.scraper_service import _advance_next_run

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # 单周期内迟到：从到期点 + 一个间隔
        due = now - timedelta(minutes=30)
        assert _advance_next_run(60, due, now) == due + timedelta(minutes=60)
        # 停机超过一个周期：推进到未来的第一个执行点
        due2 = now - timedelta(hours=3)
        assert _advance_next_run(60, due2, now) == due2 + timedelta(minutes=60) * 4

    async def test_run_now_advances_clock_so_auto_loop_skips(self, client):
        """「立即执行」到期计划后推进 next_run_at，自动循环不再重复触发。"""
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import func, select

        from app.database import async_session
        from app.models.scraper import ScraperSchedule, ScraperTask
        from app.services import scraper_service

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        due = now - timedelta(minutes=10)

        async with async_session() as db:
            sched = ScraperSchedule(
                platform="douyin", keywords='["穿搭"]', max_count=10,
                enabled=True, interval_minutes=60, next_run_at=due, run_count=0,
            )
            db.add(sched)
            await db.commit()
            await db.refresh(sched)
            sid = sched.id

        # 立即执行（douyin 无 CDP 预检）
        async with async_session() as db:
            await scraper_service.run_schedule_now(db, sid)

        # 立即执行已把 next_run_at 推进到未来，自动循环不应再触发
        async with async_session() as db:
            assert await scraper_service.run_due_schedules(db) == 0

        async with async_session() as db:
            task_count = await db.scalar(select(func.count(ScraperTask.id)))
            assert task_count == 1  # 仅「立即执行」创建的那一个任务


class TestCookieManagement:
    """Cookie 导入、状态查询与删除。"""

    def test_import_status_delete(self, client):
        st = client.get("/api/scraper/cookie-status", params={"platform": "xiaohongshu"}).json()
        assert st["exists"] is False

        r = client.post(
            "/api/scraper/cookie-import",
            json={"platform": "xiaohongshu", "cookies": [{"name": "web_session", "value": "abc"}]},
        )
        assert r.status_code == 200
        assert r.json()["imported"] == 1

        st = client.get("/api/scraper/cookie-status", params={"platform": "xiaohongshu"}).json()
        assert st["exists"] is True
        assert st["valid"] is True

        assert client.delete("/api/scraper/cookie/xiaohongshu").status_code == 200
        st = client.get("/api/scraper/cookie-status", params={"platform": "xiaohongshu"}).json()
        assert st["exists"] is False
        assert client.delete("/api/scraper/cookie/xiaohongshu").status_code == 404

    def test_import_rejects_unknown_platform(self, client):
        r = client.post(
            "/api/scraper/cookie-import",
            json={"platform": "weibo", "cookies": [{"name": "a", "value": "b"}]},
        )
        assert r.status_code == 400


class TestStats:
    """采集统计看板聚合接口。"""

    def test_stats_aggregation(self, client):
        for i in range(2):
            _create_task(client, keywords=(f"关键词{i}",))

        s = client.get("/api/scraper/stats", params={"days": 30}).json()
        assert s["total_tasks"] == 2
        assert s["success_rate"] == 0  # 均为 pending
        platforms = {p["platform"] for p in s["by_platform"]}
        assert "douyin" in platforms
        assert sum(d["tasks"] for d in s["by_day"]) == 2
