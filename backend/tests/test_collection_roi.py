"""采集 ROI 漏斗测试：关键词 / 博主维度聚合、素材归属与「样本为 0」的诚实口径。

覆盖两个通道的取数差异：CDP 任务用 task 级 found/added + 素材的 scraper_task_id 归属；
f2 素材没有该关联，只能按 source_author 聚合（两种口径都不摊派）。
"""

import sqlite3

import pytest

from app.config import settings
from app.services.scraper.roi import get_collection_roi


@pytest.fixture(autouse=True)
def no_scraper_subprocess(monkeypatch):
    """与 test_scraper*.py 同约定：测试不拉起真实采集子进程。"""
    from app.services.scraper import process

    monkeypatch.setattr(process, "_launch_scraper_process", lambda task_id: None)


def _db_path():
    return settings.storage_root.parent / "fashion_inspo.db"


def _create_task(client, platform="douyin", keywords=("JK制服",), **extra) -> int:
    body = {"platform": platform, "keywords": list(keywords), "max_count": 5, **extra}
    r = client.post("/api/scraper/tasks", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _set_counters(task_id: int, found: int, added: int) -> None:
    """预置任务的发现/入库计数。

    这两个数字由采集子进程在跑完时写入（插件任务另有 complete 接口），
    ROI 只读取它们，因此测试按同样的落库方式预置，不改业务代码路径。
    """
    conn = sqlite3.connect(str(_db_path()))
    try:
        conn.execute(
            "UPDATE scraper_tasks SET items_found=?, items_added=? WHERE id=?",
            (found, added, task_id),
        )
        conn.commit()
    finally:
        conn.close()


def _backdate(table: str, row_id, days: int) -> None:
    """把创建时间往前挪 N 天，用于验证统计窗口的过滤。"""
    conn = sqlite3.connect(str(_db_path()))
    try:
        conn.execute(
            f"UPDATE {table} SET created_at = datetime('now', ?) WHERE id = ?",
            (f"-{days} days", row_id),
        )
        conn.commit()
    finally:
        conn.close()


def _set_quality(client, ids: list[str], quality: str) -> None:
    r = client.post(
        "/api/inspirations/batch-update", json={"ids": ids, "quality_status": quality}
    )
    assert r.status_code == 200, r.text


def _roi(client, **params) -> dict:
    r = client.get("/api/scraper/collection-roi", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _keyword_row(data: dict, keyword: str) -> dict:
    return next(row for row in data["by_keyword"] if row["keyword"] == keyword)


def _author_row(data: dict, name: str) -> dict:
    return next(row for row in data["by_author"] if row["name"] == name)


class TestKeywordDimension:
    def test_funnel_counts_and_quality_attribution(self, client, upload):
        """关键词漏斗：任务级 发现/入库 与按 scraper_task_id 归属的合格率。"""
        jk = _create_task(client, keywords=["JK制服"])
        _set_counters(jk, found=10, added=4)
        approved = [upload(scraper_task_id=str(jk)).json()["id"] for _ in range(2)]
        rejected = [upload(scraper_task_id=str(jk)).json()["id"]]
        _set_quality(client, approved, "approved")
        _set_quality(client, rejected, "rejected")

        yujie = _create_task(client, keywords=["御姐"])
        _set_counters(yujie, found=5, added=5)
        ok = [upload(scraper_task_id=str(yujie)).json()["id"]]
        _set_quality(client, ok, "approved")

        data = _roi(client)

        jk_row = _keyword_row(data, "JK制服")
        assert jk_row["tasks"] == 1
        assert jk_row["found"] == 10 and jk_row["added"] == 4
        assert jk_row["add_rate"] == 40.0
        assert (jk_row["attributed"], jk_row["approved"], jk_row["rejected"]) == (3, 2, 1)
        assert jk_row["pending"] == 0
        assert jk_row["approved_rate"] == 66.7  # 2 / (2 + 1)

        assert _keyword_row(data, "御姐")["add_rate"] == 100.0
        assert _keyword_row(data, "御姐")["approved_rate"] == 100.0

        # 按入库量降序：御姐 5 > JK制服 4
        assert [r["keyword"] for r in data["by_keyword"]] == ["御姐", "JK制服"]
        assert data["coverage"]["tasks_scanned"] == 2
        assert data["coverage"]["keyword_attributed"] == 4

    def test_multi_keyword_tasks_excluded(self, client):
        """多关键词任务的量无法按词拆分 → 整体排除并给出说明（不摊派）。"""
        _create_task(client, keywords=["甜美风", "白色系穿搭"])
        _set_counters(_create_task(client, keywords=["黑丝"]), found=3, added=1)

        data = _roi(client)
        assert [r["keyword"] for r in data["by_keyword"]] == ["黑丝"]
        assert data["coverage"]["multi_keyword_tasks"] == 1
        assert any("多关键词" in n for n in data["coverage"]["notes"])

    def test_missing_quality_sample_reports_null_rate(self, client):
        """没有可归属素材时合格率为 null（而不是 0%），并说明原因。"""
        task_id = _create_task(client, keywords=["高跟鞋"])
        _set_counters(task_id, found=8, added=2)

        row = _keyword_row(_roi(client), "高跟鞋")
        assert row["add_rate"] == 25.0
        assert row["attributed"] == 0
        assert row["approved_rate"] is None
        assert any("暂无样本" in n for n in _roi(client)["coverage"]["notes"])

    def test_trashed_materials_leave_the_sample(self, client, upload):
        """进垃圾桶的素材不再计入合格率样本（否则会把它当作合格/不合格）。"""
        task_id = _create_task(client, keywords=["小白裙"])
        _set_counters(task_id, found=4, added=2)
        kept = upload(scraper_task_id=str(task_id)).json()["id"]
        trashed = upload(scraper_task_id=str(task_id)).json()["id"]
        _set_quality(client, [kept, trashed], "approved")
        r = client.post(f"/api/inspirations/{trashed}/trash", json={"reason": "重复"})
        assert r.status_code == 200, r.text

        row = _keyword_row(_roi(client), "小白裙")
        assert row["attributed"] == 1 and row["approved"] == 1
        assert row["approved_rate"] == 100.0


class TestAuthorDimension:
    def test_f2_materials_grouped_by_source_author(self, client, upload):
        """f2 通道：按来源作者聚合入库与质量（没有任务级 found/added）。"""
        for _ in range(2):
            mid = upload(source_type="douyin", source_author="穿搭博主A").json()["id"]
            _set_quality(client, [mid], "approved")
        bad = upload(source_type="douyin", source_author="穿搭博主A").json()["id"]
        _set_quality(client, [bad], "rejected")
        upload(source_type="douyin", source_author="博主B")

        data = _roi(client)
        row_a = _author_row(data, "穿搭博主A")
        assert row_a["channel"] == "f2" and row_a["platform"] == "douyin"
        assert row_a["imported"] == 3
        assert (row_a["approved"], row_a["rejected"], row_a["pending"]) == (2, 1, 0)
        assert row_a["approved_rate"] == 66.7
        # f2 没有任务级口径：相关字段为 null，前端显示「—」而不是 0
        assert (row_a["tasks"], row_a["found"], row_a["added"], row_a["add_rate"]) == (
            None,
            None,
            None,
            None,
        )

        row_b = _author_row(data, "博主B")
        assert row_b["imported"] == 1 and row_b["pending"] == 1
        assert row_b["approved_rate"] is None
        assert data["coverage"]["f2_materials"] == 4

    def test_cdp_blogger_task_uses_task_counters(self, client, upload, create_blogger):
        """CDP 按博主采集：任务级发现/入库 + 素材归属的合格率。"""
        blogger = create_blogger(
            "抖音博主C",
            platform="douyin",
            profile_url="https://www.douyin.com/user/MS4x_c",
        )
        task_id = _create_task(
            client,
            keywords=[],
            collect_mode="user",
            blogger_id=blogger["id"],
        )
        _set_counters(task_id, found=6, added=3)
        mid = upload(scraper_task_id=str(task_id)).json()["id"]
        _set_quality(client, [mid], "approved")

        row = _author_row(_roi(client), "抖音博主C")
        assert row["channel"] == "cdp"
        assert row["tasks"] == 1
        assert (row["found"], row["added"], row["add_rate"]) == (6, 3, 50.0)
        assert row["imported"] == 3  # 任务口径的入库数
        assert row["approved"] == 1 and row["approved_rate"] == 100.0

    def test_missing_blogger_name_falls_back_to_id(self, client, create_blogger):
        """博主建任务后被删除：任务仍在、素材归属也还在，名字回退为 "# id"。"""
        blogger = create_blogger(
            "待删博主", platform="douyin", profile_url="https://www.douyin.com/user/MS4x_d"
        )
        task_id = _create_task(
            client, keywords=[], collect_mode="user", blogger_id=blogger["id"]
        )
        _set_counters(task_id, found=2, added=2)
        assert client.delete(f"/api/bloggers/{blogger['id']}").status_code == 204

        row = _author_row(_roi(client), f"# {blogger['id']}")
        assert row["channel"] == "cdp" and row["added"] == 2


class TestWindowAndShape:
    def test_days_window_filters_tasks_and_materials(self, client, upload):
        """统计窗口同时作用于任务级与素材级口径。"""
        fresh = _create_task(client, keywords=["甜妹"])
        _set_counters(fresh, found=2, added=2)

        old = _create_task(client, keywords=["学院风"])
        _set_counters(old, found=9, added=9)
        _backdate("scraper_tasks", old, 200)

        old_material = upload(source_type="douyin", source_author="旧博主").json()["id"]
        _backdate("inspirations", old_material, 200)
        upload(source_type="douyin", source_author="新博主")

        recent = _roi(client, days=30)
        assert [r["keyword"] for r in recent["by_keyword"]] == ["甜妹"]
        assert {r["name"] for r in recent["by_author"]} == {"新博主"}

        long_window = _roi(client, days=365)
        assert {r["keyword"] for r in long_window["by_keyword"]} == {"甜妹", "学院风"}
        assert {r["name"] for r in long_window["by_author"]} == {"新博主", "旧博主"}

    def test_limit_applies_per_dimension(self, client):
        """limit 逐维度生效，且行按入库量降序取前 N。"""
        for i, kw in enumerate(["A词", "B词", "C词"]):
            task_id = _create_task(client, keywords=[kw])
            _set_counters(task_id, found=10, added=i + 1)

        data = _roi(client, limit=2)
        assert [r["keyword"] for r in data["by_keyword"]] == ["C词", "B词"]

    def test_invalid_params_rejected(self, client):
        """越界参数由请求模型直接拒绝，不进入聚合逻辑。"""
        assert client.get("/api/scraper/collection-roi?days=0").status_code == 422
        assert client.get("/api/scraper/collection-roi?days=99999").status_code == 422
        assert client.get("/api/scraper/collection-roi?limit=0").status_code == 422

    async def test_service_clamps_absurd_values(self, client):
        """服务层兜底：直接调用时把离谱的 days/limit 收敛到安全区间。"""
        from app.database import async_session

        async with async_session() as db:
            data = await get_collection_roi(db, days=0, limit=100000)
        assert data["days"] == 1
        assert len(data["by_keyword"]) <= 200
