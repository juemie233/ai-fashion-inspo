"""``inspirations`` 直写 INSERT 的「单一来源」守卫。

这组用例守的是两个**只在运行期才炸**的漂移：

1. **列清单 vs 占位符数量**：任务 #46 的事故形态是「14 列 13 值」，sqlite 报错后
   被重试包装器当成网络错误，整条抖音采集链路静默颗粒无收。现在三条 SQL 都由
   `scripts/_inspiration_insert.py` 生成，这里用 golden 字符串锁死它们，
   并**真的建表执行一次**（语法/列名错了直接失败）。
2. **模型新增必填列**：往 `Inspiration` 加一个「非空且无默认值」的列却忘了同步
   INSERT，会让采集与 f2 导入在写入时才失败。这里断言每条 spec 都覆盖了所有
   「非空且无默认」的列。

另外校验两条路径的列集合差异**恰为**已知的两列（`source_author` /
`source_platform_id`），防止某天有人悄悄让两边分叉。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from app.models.inspiration import Inspiration
from scripts._inspiration_insert import (
    F2_EXTRA_COLUMNS,
    F2_IMPORT,
    INSERT_F2_SQL,
    INSERT_INSPIRATION_IMAGE_SQL,
    INSERT_INSPIRATION_VIDEO_SQL,
    SCRAPER_IMAGE,
    SCRAPER_VIDEO,
)

# golden：任何列/字面量改动都必须同步改这里（改它就是一次需要人审的 schema 变更）
GOLDEN_IMAGE_SQL = (
    "INSERT INTO inspirations (id, source_type, source_url, file_path, "
    "thumbnail_path, media_type, dominant_colors, is_favorite, "
    "quality_status, rating, is_ai_generated, content_hash, caption, "
    "scraper_task_id, created_at, updated_at) "
    "VALUES (?, ?, ?, ?, NULL, ?, NULL, 0, 'pending', 0, 0, ?, ?, ?, ?, ?)"
)
GOLDEN_VIDEO_SQL = (
    "INSERT INTO inspirations (id, source_type, source_url, file_path, "
    "thumbnail_path, media_type, dominant_colors, is_favorite, "
    "quality_status, rating, is_ai_generated, content_hash, caption, "
    "scraper_task_id, created_at, updated_at) "
    "VALUES (?, ?, ?, ?, ?, ?, NULL, 0, 'pending', 0, 0, NULL, ?, ?, ?, ?)"
)
GOLDEN_F2_SQL = (
    "INSERT INTO inspirations (id, source_type, source_url, source_author, "
    "source_platform_id, file_path, thumbnail_path, media_type, dominant_colors, "
    "is_favorite, quality_status, rating, is_ai_generated, content_hash, caption, "
    "scraper_task_id, created_at, updated_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, 'pending', 0, 0, ?, ?, NULL, ?, ?)"
)


@pytest.mark.parametrize(
    ("actual", "golden", "label"),
    (
        (INSERT_INSPIRATION_IMAGE_SQL, GOLDEN_IMAGE_SQL, "采集·图片"),
        (INSERT_INSPIRATION_VIDEO_SQL, GOLDEN_VIDEO_SQL, "采集·视频"),
        (INSERT_F2_SQL, GOLDEN_F2_SQL, "f2 导入"),
    ),
)
def test_sql_matches_golden(actual: str, golden: str, label: str) -> None:
    """生成的 SQL 必须与 golden 完全一致（防止悄悄改列清单）。"""
    assert actual == golden, f"{label} 的 INSERT 与 golden 不一致"


@pytest.mark.parametrize(
    ("spec", "expected"),
    ((SCRAPER_IMAGE, 10), (SCRAPER_VIDEO, 10), (F2_IMPORT, 12)),
)
def test_placeholder_count_matches_param_count(spec, expected: int) -> None:
    """占位符数量 == 需要传参的列数（任务 #46 的形态：14 列 13 值）。"""
    assert spec.sql().count("?") == spec.param_count == expected


def test_two_paths_differ_only_by_documented_columns() -> None:
    """采集路径与 f2 路径的列集合差异必须恰为 source_author / source_platform_id。"""
    scraper_cols = set(SCRAPER_IMAGE.columns)
    f2_cols = set(F2_IMPORT.columns)
    assert f2_cols - scraper_cols == set(F2_EXTRA_COLUMNS)
    assert scraper_cols - f2_cols == set()
    # 采集的图片/视频两条只差「哪个列是字面量」，列集合必须完全相同
    assert SCRAPER_IMAGE.columns == SCRAPER_VIDEO.columns


@pytest.mark.parametrize("spec", (SCRAPER_IMAGE, SCRAPER_VIDEO, F2_IMPORT))
def test_required_model_columns_are_covered(spec) -> None:
    """模型里「非空且无默认值」的列必须都出现在 INSERT 里（否则写入时才炸）。"""
    required = {
        c.name
        for c in Inspiration.__table__.columns
        if not c.nullable and c.default is None and c.server_default is None
    }
    missing = required - set(spec.columns)
    assert not missing, f"INSERT 漏了必填列：{sorted(missing)}"


@pytest.mark.parametrize(
    ("spec", "values"),
    (
        (
            SCRAPER_IMAGE,
            {
                "id": "t-img",
                "source_type": "scraper",
                "source_url": "https://example.com/n/1",
                "file_path": "images/2026-10-28/a.jpg",
                "media_type": "image",
                "content_hash": "a" * 64,
                "caption": "正文",
                "scraper_task_id": 1,
                "created_at": "2026-10-28 10:00:00",
                "updated_at": "2026-10-28 10:00:00",
            },
        ),
        (
            SCRAPER_VIDEO,
            {
                "id": "t-vid",
                "source_type": "scraper",
                "source_url": "https://example.com/n/2",
                "file_path": "videos/2026-10-28/b.mp4",
                "thumbnail_path": "thumbnails/2026-10-28/b.jpg",
                "media_type": "video",
                "caption": "正文",
                "scraper_task_id": 1,
                "created_at": "2026-10-28 10:00:00",
                "updated_at": "2026-10-28 10:00:00",
            },
        ),
        (
            F2_IMPORT,
            {
                "id": "t-f2",
                "source_type": "douyin",
                "source_url": "https://www.douyin.com/note/7300000000000000001",
                "source_author": "某博主",
                "source_platform_id": "f2:7300000000000000001#image1",
                "file_path": "images/2026-10-28/c.webp",
                "thumbnail_path": "thumbnails/2026-10-28/c.webp",
                "media_type": "image",
                "content_hash": "b" * 64,
                "caption": "正文",
                "created_at": "2026-10-28 10:00:00",
                "updated_at": "2026-10-28 10:00:00",
            },
        ),
    ),
)
def test_sql_executes_against_model_schema(spec, values: dict) -> None:
    """真的建表并执行一次：列名写错、占位符数不对、字面量类型不合法都会在这里失败。"""
    engine = create_engine("sqlite://")
    Inspiration.__table__.create(engine)
    params = tuple(values[col] for col in spec.param_columns)

    # 用 SQLAlchemy 的同一个 engine 执行（保证与生产同样的 schema）
    with engine.begin() as conn:
        conn.exec_driver_sql(spec.sql(), params)
        count = conn.exec_driver_sql("SELECT count(*) FROM inspirations").scalar()
    assert count == 1
