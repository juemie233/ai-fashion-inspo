"""采集 ROI 漏斗补充测试：配置坏数据、维度口径边界、f2 通道与排序截断。

对应 ``app/services/scraper/roi.py`` 中既有 test_collection_roi.py 未覆盖的分支：
days/limit 收敛、config 非法 JSON、多关键词排除说明、无样本说明、CDP 博主名
回表与缺行兜底、非法 blogger_id / 非 user 模式忽略、f2 通道 pending 归并与
窗口外素材排除、排序与 limit 截断，以及纯函数（_parse_config/_rate/_tally_quality）。
"""

import json
from datetime import timedelta

from app.database import async_session
from app.models.inspiration import Inspiration
from app.models.person import Blogger
from app.models.scraper import ScraperTask
from app.services.scraper import roi
from app.utils.time import utcnow


async def _add_task(db, *, platform="douyin", config=None, found=0, added=0, created_at=None):
    task = ScraperTask(
        platform=platform,
        status="completed",
        config=json.dumps(config) if isinstance(config, dict) else config,
        items_found=found,
        items_added=added,
        created_at=created_at or utcnow(),
    )
    db.add(task)
    await db.commit()
    return task


async def _add_material(db, *, task_id=None, author=None, source_type="douyin", quality="pending", created_at=None):
    row = Inspiration(
        file_path=f"images/{author or 'x'}-{quality}.jpg",
        media_type="image",
        source_type=source_type,
        source_author=author,
        scraper_task_id=task_id,
        quality_status=quality,
        created_at=created_at or utcnow(),
    )
    db.add(row)
    await db.commit()
    return row


def test_parse_config_tolerates_bad_json():
    """任务 config 是坏数据时按空配置处理，不让整页 500。"""
    assert roi._parse_config(None) == {}
    assert roi._parse_config("") == {}
    assert roi._parse_config("not-json") == {}
    assert roi._parse_config("[1, 2]") == {}
    assert roi._parse_config('{"keywords": ["jk"]}') == {"keywords": ["jk"]}


def test_rate_and_quality_helpers():
    """比率分母为 0 返回 None；未知质量状态归入 pending。"""
    assert roi._rate(1, 3) == 33.3
    assert roi._rate(0, 0) is None
    assert roi._rate(5, 0) is None
    tally = roi._tally_quality({"approved": 1, "rejected": 2, "pending": 3, "weird": 4})
    assert tally == {"approved": 1, "rejected": 2, "pending": 7}
    quality = roi._quality_counts({"approved": 2, "rejected": 1})
    assert quality == {
        "approved": 2,
        "rejected": 1,
        "pending": 0,
        "approved_rate": 66.7,
    }
    assert roi._empty_bucket() == {"tasks": 0, "found": 0, "added": 0, "task_ids": []}


async def test_days_and_limit_are_clamped():
    """days 下限 1 / 上限 3650，limit 下限 1 且真的截断行数。"""
    async with async_session() as db:
        await _add_task(db, config={"keywords": ["JK制服"]}, found=10, added=5)
        await _add_task(db, config={"keywords": ["御姐"]}, found=10, added=2)

        clamped = await roi.get_collection_roi(db, days=0, limit=0)
        huge = await roi.get_collection_roi(db, days=999999, limit=999)

    assert clamped["days"] == 1
    assert len(clamped["by_keyword"]) == 1  # limit=0 → 收敛为 1
    assert clamped["by_keyword"][0]["keyword"] == "JK制服"  # 按入库量降序
    assert huge["days"] == 3650


async def test_bad_and_multi_keyword_configs_are_handled():
    """坏 config 不崩；多关键词任务整体排除并计入 notes。"""
    async with async_session() as db:
        await _add_task(db, config=None, found=3, added=1)
        await _add_task(db, config="broken-json", found=2, added=1)
        await _add_task(db, config={"keywords": ["甜美风", "白色系"]}, found=5, added=2)
        await _add_task(db, config={"keywords": ["黑丝"]}, found=4, added=2)

        data = await roi.get_collection_roi(db)

    assert [row["keyword"] for row in data["by_keyword"]] == ["黑丝"]
    assert data["coverage"]["multi_keyword_tasks"] == 1
    assert data["coverage"]["tasks_scanned"] == 4
    assert any("多关键词" in note for note in data["coverage"]["notes"])


async def test_keyword_without_attributed_sample_notes_null_rate():
    """有任务但素材没写 scraper_task_id：合格率 null 并说明历史素材口径。"""
    async with async_session() as db:
        task = await _add_task(db, config={"keywords": ["高跟鞋"]}, found=8, added=2)
        # 素材存在但未带任务归属（历史素材）
        await _add_material(db, quality="approved", author="")
        data = await roi.get_collection_roi(db)
        task_id = task.id

    row = data["by_keyword"][0]
    assert row["keyword"] == "高跟鞋"
    assert row["attributed"] == 0
    assert row["approved_rate"] is None
    assert data["coverage"]["keyword_attributed"] == 0
    assert any("暂无样本" in note for note in data["coverage"]["notes"])
    assert task_id  # 任务存在但不参与素材归属


async def test_cdp_author_uses_blogger_name_and_counts_quality():
    """按博主采集：名字回表取；素材质量按任务归属累计。"""
    async with async_session() as db:
        blogger = Blogger(name="抖音博主C", platform="douyin")
        db.add(blogger)
        await db.commit()
        blogger_id = blogger.id
        task = await _add_task(
            db,
            config={"keywords": [], "collect_mode": "user", "blogger_id": blogger_id},
            found=6,
            added=3,
        )
        await _add_material(db, task_id=task.id, quality="approved")
        await _add_material(db, task_id=task.id, quality="approved")
        await _add_material(db, task_id=task.id, quality="rejected")
        await _add_material(db, task_id=task.id, quality="pending")
        # 垃圾桶素材不算样本
        trashed = await _add_material(db, task_id=task.id, quality="approved")
        trashed.deleted_at = utcnow()
        await db.commit()

        data = await roi.get_collection_roi(db)

    row = data["by_author"][0]
    assert row["name"] == "抖音博主C"
    assert row["channel"] == "cdp"
    assert row["tasks"] == 1 and row["found"] == 6 and row["added"] == 3
    assert row["add_rate"] == 50.0
    assert (row["approved"], row["rejected"], row["pending"]) == (2, 1, 1)
    assert row["approved_rate"] == 66.7


async def test_cdp_author_name_fallback_when_blogger_missing():
    """博主行缺失（被删）时用 ``# id`` 占位，不丢统计。"""
    async with async_session() as db:
        await _add_task(
            db,
            config={"keywords": [], "collect_mode": "user", "blogger_id": 999},
            found=2,
            added=1,
        )
        data = await roi.get_collection_roi(db)

    assert data["by_author"][0]["name"] == "# 999"


async def test_invalid_blogger_id_and_non_user_mode_are_ignored():
    """blogger_id 非数字 / collect_mode 不是 user：不进博主维度。"""
    async with async_session() as db:
        await _add_task(
            db,
            config={"keywords": ["jk"], "collect_mode": "user", "blogger_id": "abc"},
            found=3,
            added=1,
        )
        await _add_task(
            db,
            config={"keywords": ["jk"], "collect_mode": "keyword", "blogger_id": 5},
            found=3,
            added=1,
        )
        data = await roi.get_collection_roi(db)

    assert data["by_author"] == []
    assert data["by_keyword"][0]["tasks"] == 2


async def test_f2_channel_pending_merges_unknown_quality():
    """f2 通道：无任务级口径、quality NULL/未知归 pending、窗口外素材排除。"""
    old = utcnow() - timedelta(days=200)
    async with async_session() as db:
        await _add_material(db, author="作者X", quality="approved")
        await _add_material(db, author="作者X", quality="rejected")
        await _add_material(db, author="作者X", quality=None)
        await _add_material(db, author="作者X", quality="unknown", created_at=old)
        data = await roi.get_collection_roi(db, days=180)

    row = data["by_author"][0]
    assert row["name"] == "作者X"
    assert row["channel"] == "f2" and row["platform"] == "douyin"
    assert (row["tasks"], row["found"], row["added"], row["add_rate"]) == (
        None,
        None,
        None,
        None,
    )
    assert row["imported"] == 3
    assert (row["approved"], row["rejected"], row["pending"]) == (1, 1, 1)
    assert row["approved_rate"] == 50.0
    assert data["coverage"]["f2_materials"] == 3
    assert not any("没有 f2 导入" in note for note in data["coverage"]["notes"])


async def test_f2_absence_note_and_author_sorting_with_limit():
    """无 f2 素材时给出说明；博主维度 CDP 按入库量降序、limit 截断。"""
    async with async_session() as db:
        await _add_task(
            db, config={"keywords": [], "collect_mode": "user", "blogger_id": 1}, added=1
        )
        await _add_task(
            db, config={"keywords": [], "collect_mode": "user", "blogger_id": 2}, added=5
        )
        data_full = await roi.get_collection_roi(db)
        data_limited = await roi.get_collection_roi(db, limit=1)

    assert [row["name"] for row in data_full["by_author"]] == ["# 2", "# 1"]
    assert len(data_limited["by_author"]) == 1
    assert data_limited["by_author"][0]["name"] == "# 2"
    assert any("没有 f2 导入" in note for note in data_full["coverage"]["notes"])
