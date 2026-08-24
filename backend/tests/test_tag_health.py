"""标签健康度扫描测试：检查项识别、健康评分、异步任务与明细接口。"""

from sqlalchemy import select

from app.database import async_session
from app.models.task import TaskQueue
from app.services.tag_health import scan_tag_health
from app.services.task_runners.tag_health import execute_tag_health_scan


def _create_tag(client, name: str, category: str = "free") -> dict:
    r = client.post("/api/tags", json={"name": name, "category": category})
    assert r.status_code == 201, r.text
    return r.json()


async def _run_health_scan(client, threshold: float = 0.75) -> int:
    """创建扫描任务并同步执行（模拟 worker 成功标记），返回 task_id。"""
    r = client.post(
        "/api/tags/health/scan", json={"duplicate_threshold": threshold}
    )
    assert r.status_code == 200, r.text
    task_id = r.json()["task_id"]
    async with async_session() as db:
        task = await db.get(TaskQueue, task_id)
        await execute_tag_health_scan(db, task)
        task.status = "success"  # 模拟 worker 的成功标记
        task.progress = 100
        await db.commit()
    return task_id


async def test_scan_identifies_issues(client, upload):
    """四类问题标签都能被识别，评分 < 100。"""
    orphan = _create_tag(client, "孤儿甲")
    low_freq = _create_tag(client, "低频甲")
    # 低频：恰好 1 次素材关联
    insp_id = upload().json()["id"]
    client.post(f"/api/inspirations/{insp_id}/tags", json={"names": ["低频甲"]})
    # 低质命名：超长名
    bad = _create_tag(client, "这是一条超过八个字的长标签名称")
    # 疑似重复：同类别相似名
    _create_tag(client, "法式", category="style")
    _create_tag(client, "法式风", category="style")

    async with async_session() as db:
        result = await scan_tag_health(db, duplicate_threshold=0.75)

    assert result["total"] == 5
    assert orphan["id"] in result["issues"]["orphan"]["tag_ids"]
    assert low_freq["id"] in result["issues"]["low_frequency"]["tag_ids"]
    assert bad["id"] in result["issues"]["low_quality_name"]["tag_ids"]
    assert result["issues"]["duplicate"]["count"] >= 1
    assert 0.0 <= result["score"] < 100.0


async def test_scan_task_and_detail_api(client):
    """提交扫描任务 → 执行 → 明细接口分页返回。"""
    orphan = _create_tag(client, "孤儿甲")
    await _run_health_scan(client)

    resp = client.get("/api/tags/health/orphan")
    assert resp.status_code == 200
    data = resp.json()
    assert data["issue_type"] == "orphan"
    assert data["total"] == 1
    assert data["items"][0]["id"] == orphan["id"]
    assert data["items"][0]["usage_count"] == 0


async def test_health_detail_pagination(client):
    """孤儿标签超过一页时按页返回。"""
    for i in range(5):
        _create_tag(client, f"孤儿{i}")
    await _run_health_scan(client)

    resp = client.get("/api/tags/health/orphan", params={"page": 1, "size": 2})
    data = resp.json()
    assert data["total"] == 5
    assert len(data["items"]) == 2

    resp3 = client.get("/api/tags/health/orphan", params={"page": 3, "size": 2})
    assert len(resp3.json()["items"]) == 1


async def test_health_duplicate_detail(client):
    """疑似重复明细返回标签对。"""
    _create_tag(client, "法式", category="style")
    _create_tag(client, "法式风", category="style")
    await _run_health_scan(client, threshold=0.75)

    resp = client.get("/api/tags/health/duplicate")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    pair = data["items"][0]
    assert {"法式", "法式风"} <= {pair["tag_a"]["name"], pair["tag_b"]["name"]}
    assert 0.0 <= pair["similarity"] <= 1.0


def test_health_detail_without_scan_404(client):
    """未提交过扫描任务时明细接口返回 404。"""
    resp = client.get("/api/tags/health/orphan")
    assert resp.status_code == 404


async def test_health_scan_empty_db(client):
    """空库扫描：total=0、score=100，各问题为空。"""
    async with async_session() as db:
        result = await scan_tag_health(db)
    assert result["total"] == 0
    assert result["score"] == 100.0
    assert result["issues"]["orphan"]["count"] == 0
    assert result["issues"]["duplicate"]["count"] == 0
