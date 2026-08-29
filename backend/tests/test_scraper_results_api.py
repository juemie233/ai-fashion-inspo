"""采集结果 API 集成测试：结果分页 / 计数口径 / 批量移入垃圾桶。

补齐 test_scraper.py（TestExtensionTaskFlow 仅覆盖单素材全流程）之外的
结果查询边界：404、分页、跨任务隔离、任务计数同步、删除原因与审计。
"""

import pytest


@pytest.fixture(autouse=True)
def no_scraper_subprocess(monkeypatch):
    """与 test_scraper.py 同约定：测试不拉起真实采集子进程。"""
    from app.services.scraper import process

    monkeypatch.setattr(process, "_launch_scraper_process", lambda task_id: None)


def _create_task(client, platform="douyin") -> int:
    r = client.post(
        "/api/scraper/tasks",
        json={"platform": platform, "keywords": ["穿搭"], "max_count": 5},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestTaskResults:
    def test_results_unknown_task_404(self, client):
        r = client.get("/api/scraper/tasks/99999/results")
        assert r.status_code == 404

    def test_results_pagination(self, client, upload):
        """分页：total 恒为全量，page/size 切片正确。"""
        task_id = _create_task(client)
        for _ in range(3):
            r = upload(scraper_task_id=str(task_id))
            assert r.status_code == 201, r.text

        page1 = client.get(f"/api/scraper/tasks/{task_id}/results?page=1&size=2").json()
        page2 = client.get(f"/api/scraper/tasks/{task_id}/results?page=2&size=2").json()
        assert page1["total"] == 3 and len(page1["items"]) == 2
        assert page2["total"] == 3 and len(page2["items"]) == 1
        # 两页不重叠
        ids1 = {i["id"] for i in page1["items"]}
        ids2 = {i["id"] for i in page2["items"]}
        assert not ids1 & ids2

    def test_results_isolated_per_task(self, client, upload):
        """任务结果互不串扰：A 任务看不到 B 任务的素材。"""
        task_a = _create_task(client)
        task_b = _create_task(client)
        upload(scraper_task_id=str(task_a))
        upload(scraper_task_id=str(task_b))
        upload(scraper_task_id=str(task_b))

        assert client.get(f"/api/scraper/tasks/{task_a}/results").json()["total"] == 1
        assert client.get(f"/api/scraper/tasks/{task_b}/results").json()["total"] == 2

    def test_results_excludes_manual_uploads(self, client, upload):
        """无任务归属的手动上传素材不出现在任何任务结果里。"""
        task_id = _create_task(client)
        upload(scraper_task_id=str(task_id))
        upload()  # 手动上传，无任务关联
        res = client.get(f"/api/scraper/tasks/{task_id}/results").json()
        assert res["total"] == 1


class TestBatchDeleteResults:
    def test_empty_ids_400(self, client):
        task_id = _create_task(client)
        r = client.post(f"/api/scraper/tasks/{task_id}/results/batch-delete", json={"ids": []})
        assert r.status_code == 400

    def test_batch_delete_updates_task_counter(self, client, upload):
        """批量删除后任务 items_added 同步为剩余未删除数。"""
        task_id = _create_task(client)
        ids = [upload(scraper_task_id=str(task_id)).json()["id"] for _ in range(2)]

        body = client.post(
            f"/api/scraper/tasks/{task_id}/results/batch-delete",
            json={"ids": ids},
        ).json()
        assert body["trashed_count"] == 2
        assert body["skipped"] == 0
        assert body["remaining"] == 0

        # 任务计数同步（任务详情口径 = 未删除素材数）
        tasks = client.get("/api/scraper/tasks").json()["items"]
        task = next(t for t in tasks if t["id"] == task_id)
        assert task["items_added"] == 0

    def test_batch_delete_only_touches_own_task(self, client, upload):
        """跨任务素材不误删：请求里混入其他任务的 ID 时被忽略。"""
        task_a = _create_task(client)
        task_b = _create_task(client)
        id_a = upload(scraper_task_id=str(task_a)).json()["id"]
        id_b = upload(scraper_task_id=str(task_b)).json()["id"]

        body = client.post(
            f"/api/scraper/tasks/{task_a}/results/batch-delete",
            json={"ids": [id_a, id_b]},  # id_b 不属于任务 A
        ).json()
        assert body["trashed_count"] == 1
        # 任务 B 的素材仍在结果中
        assert client.get(f"/api/scraper/tasks/{task_b}/results").json()["total"] == 1

    def test_batch_delete_reason_explicit_and_inferred(self, client, upload):
        """显式原因直接采用；未传原因时按素材状态推断为「不喜欢」。"""
        task_id = _create_task(client)
        id1 = upload(scraper_task_id=str(task_id)).json()["id"]
        id2 = upload(scraper_task_id=str(task_id)).json()["id"]

        client.post(
            f"/api/scraper/tasks/{task_id}/results/batch-delete",
            json={"ids": [id1], "reason": "不喜欢"},
        )
        client.post(
            f"/api/scraper/tasks/{task_id}/results/batch-delete",
            json={"ids": [id2]},  # 未传 reason
        )

        trash = client.get("/api/inspirations/trash").json()["items"]
        reasons = {i["id"]: i["trash_reason"] for i in trash}
        assert reasons[id1] == "不喜欢"
        assert reasons[id2] == "不喜欢"
