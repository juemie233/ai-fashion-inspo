"""素材管理统计服务（admin_stats_service）的直连单测。

为什么直接调服务、不走 API：本项目 TestClient 的请求跑在 anyio portal 线程，
pytest-cov 追踪不到那里执行的 async 函数体；而本模块的聚合口径（垃圾桶排除、
「失败按最新一条日志」、按目录汇总存储占用、重复文件浪费字节、CSV 注入清洗、
missing/orphan 判定）都值得按返回值精确断言，所以直接 ``await`` 服务函数。

覆盖：scan_storage_files / _compute_storage_sizes / collect_stats（基础计数、
分析三态、分布、存储尺寸）/ query_inspiration_trend / largest_files /
find_duplicates / _get_missing_image_vector_ids / vector_stats /
person_frequency / export_inspirations_csv / integrity_check 的 Python 路径。
"""

import csv
import io
import re
import uuid
from datetime import datetime, timedelta

import pytest

from app.config import settings
from app.database import async_session
from app.models.inspiration import AIAnalysisLog, Inspiration
from app.models.person import Blogger, InspirationBlogger, InspirationModel, Model
from app.models.scraper import ScraperSeenURL
from app.models.tag import InspirationTag, Tag
from app.services import admin_stats_service as stats
from app.services.vector.embedding import TEXT_EMBEDDING_FORMULA_VERSION
from app.utils.time import utcnow


# ═══════════════════════════════════════════════════════════════
#  造数助手
# ═══════════════════════════════════════════════════════════════


def _insp(**overrides) -> Inspiration:
    """构造一条素材记录（默认图片、手动上传、已通过）。"""
    data = {
        "id": str(uuid.uuid4()),
        "source_type": "manual_upload",
        "file_path": f"images/{uuid.uuid4().hex}.jpg",
        "media_type": "image",
        "quality_status": "approved",
    }
    data.update(overrides)
    return Inspiration(**data)


async def _add(*objects):
    """提交若干 ORM 对象；返回单个对象或列表。"""
    async with async_session() as db:
        db.add_all(list(objects))
        await db.commit()
    return objects[0] if len(objects) == 1 else list(objects)


def _write(rel_path: str, content: bytes) -> None:
    """在测试存储根下写文件（自动建父目录）。"""
    path = settings.storage_root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


# ═══════════════════════════════════════════════════════════════
#  存储扫描与尺寸汇总
# ═══════════════════════════════════════════════════════════════


async def test_scan_storage_files_excludes_non_media_dirs(client):
    """只扫 images/thumbnails/videos；默认不含 trash，include_trash=True 才含。"""
    _write("images/a.jpg", b"x" * 100)
    _write("images/2026-01/b.jpg", b"x" * 20)
    _write("thumbnails/a_t.jpg", b"x" * 30)
    _write("videos/v.mp4", b"x" * 50)
    _write("trash/t.jpg", b"x" * 7)
    _write("lancedb/ignore.bin", b"x" * 999)

    files = stats.scan_storage_files()
    assert files == {
        "images/a.jpg": 100,
        "images/2026-01/b.jpg": 20,
        "thumbnails/a_t.jpg": 30,
        "videos/v.mp4": 50,
    }

    with_trash = stats.scan_storage_files(include_trash=True)
    assert with_trash["trash/t.jpg"] == 7
    assert "lancedb/ignore.bin" not in with_trash

    sizes = stats._compute_storage_sizes(files)
    assert sizes == {
        "total_size_bytes": 200,
        "thumbnail_size_bytes": 30,  # 路径里含 thumbnails 的才计入
        "images_size_bytes": 120,  # 以 images 开头的才计入（子目录也算）
    }


async def test_scan_storage_files_missing_root_returns_empty(client, monkeypatch, tmp_path):
    """存储根不存在（全新部署）时返回空字典，而不是抛异常。"""
    monkeypatch.setattr(settings, "storage_root", tmp_path / "not-exists")

    assert stats.scan_storage_files() == {}


async def test_scan_storage_files_skips_missing_media_subdirs(client, monkeypatch, tmp_path):
    """某个媒体子目录还没建（如从未传过视频）时跳过，不报错。"""
    root = tmp_path / "storage"
    (root / "images").mkdir(parents=True)
    (root / "images" / "a.jpg").write_bytes(b"x" * 3)
    monkeypatch.setattr(settings, "storage_root", root)

    assert stats.scan_storage_files() == {"images/a.jpg": 3}


# ═══════════════════════════════════════════════════════════════
#  collect_stats：基础计数 / 分析三态 / 分布 / 存储尺寸
# ═══════════════════════════════════════════════════════════════


async def test_collect_stats_full_aggregation(client):
    tag_white = Tag(name="白色", category="color")
    tag_jk = Tag(name="JK", category="style")
    await _add(tag_white, tag_jk)

    a = _insp(
        source_type="manual_upload", created_at=datetime(2026, 1, 15, 8, 0, 0)
    )
    b = _insp(source_type="douyin", created_at=datetime(2026, 2, 10, 8, 0, 0))
    c = _insp(
        source_type="generated",
        media_type="video",
        created_at=datetime(2026, 2, 20, 8, 0, 0),
        is_favorite=True,
    )
    # 垃圾桶素材：计数/分布/分析状态全部排除
    d = _insp(
        source_type="manual_upload",
        created_at=datetime(2026, 3, 1, 8, 0, 0),
        deleted_at=utcnow(),
        trash_reason="不喜欢",
        trash_source="manual",
    )
    await _add(a, b, c, d)
    await _add(
        InspirationTag(inspiration_id=a.id, tag_id=tag_white.id),
        InspirationTag(inspiration_id=a.id, tag_id=tag_jk.id),
    )
    await _add(
        ScraperSeenURL(source_url="https://cdn.example.com/1.jpg"),
        ScraperSeenURL(source_url="https://cdn.example.com/2.jpg"),
    )
    # a：先失败后成功 → 已分析且不算失败（error 取最新一条的语义）
    await _add(
        AIAnalysisLog(inspiration_id=a.id, model_name="m1", error="旧失败"),
        AIAnalysisLog(inspiration_id=a.id, model_name="m1", processing_time_ms=120),
        # b：唯一一条即失败 → 已分析且计入分析失败
        AIAnalysisLog(inspiration_id=b.id, model_name="m1", error="timeout"),
        # c：只有质量审核日志，不算标签分析 → 仍为未分析
        AIAnalysisLog(
            inspiration_id=c.id, model_name="m1", log_type="quality_check", error="x"
        ),
        # d：已删除素材的日志不参与任何计数
        AIAnalysisLog(inspiration_id=d.id, model_name="m1"),
    )
    _write("images/main.jpg", b"x" * 100)
    _write("thumbnails/main_t.jpg", b"x" * 30)
    _write("videos/clip.mp4", b"x" * 50)

    async with async_session() as db:
        result = await stats.collect_stats(db)

    assert result["total_count"] == 3
    assert result["untagged_count"] == 2  # 只有 a 打了标签
    assert result["favorite_count"] == 1
    assert result["total_tags"] == 2
    assert result["tombstone_count"] == 2
    assert result["analysis_failed_count"] == 1

    status = {row["status"]: row for row in result["by_analysis_status"]}
    assert status["done"] == {"status": "done", "count": 1, "label": "已分析"}
    assert status["error"] == {"status": "error", "count": 1, "label": "分析失败"}
    assert status["pending"] == {"status": "pending", "count": 1, "label": "未分析"}

    by_source = {row["source_type"]: row["count"] for row in result["by_source_type"]}
    assert by_source == {"manual_upload": 1, "douyin": 1, "generated": 1}
    assert result["generated_count"] == 1  # 生成图单独计数，不混进采集进度
    by_media = {row["media_type"]: row["count"] for row in result["by_media_type"]}
    assert by_media == {"image": 2, "video": 1}
    assert result["by_month"] == [
        {"month": "2026-02", "count": 2},
        {"month": "2026-01", "count": 1},
    ]

    assert result["total_size_bytes"] == 180
    assert result["thumbnail_size_bytes"] == 30
    assert result["images_size_bytes"] == 100


async def test_collect_stats_empty_library(client):
    """空库：所有计数为 0、分布为空列表，不出现 None/异常。"""
    async with async_session() as db:
        result = await stats.collect_stats(db)

    assert result["total_count"] == 0
    assert result["untagged_count"] == 0
    assert result["favorite_count"] == 0
    assert result["total_tags"] == 0
    assert result["tombstone_count"] == 0
    assert result["analysis_failed_count"] == 0
    assert [row["count"] for row in result["by_analysis_status"]] == [0, 0, 0]
    assert result["by_source_type"] == []
    assert result["by_media_type"] == []
    assert result["by_month"] == []
    assert result["generated_count"] == 0
    assert result["total_size_bytes"] == 0


# ═══════════════════════════════════════════════════════════════
#  新增趋势
# ═══════════════════════════════════════════════════════════════


async def test_query_inspiration_trend_utc_window(client):
    """按天聚合：窗口外/垃圾桶排除，结果按日期升序。"""
    now = utcnow()
    await _add(
        _insp(created_at=now - timedelta(hours=2)),
        _insp(created_at=now - timedelta(days=2)),
        _insp(created_at=now - timedelta(days=40)),
        _insp(
            created_at=now - timedelta(hours=1),
            deleted_at=now,
            trash_reason="其他",
            trash_source="manual",
        ),
    )

    async with async_session() as db:
        data = await stats.query_inspiration_trend(db, 30)
        day_only = await stats.query_inspiration_trend(db, 1)

    assert data["days"] == 30
    assert len(data["trend"]) == 2
    assert sum(row["count"] for row in data["trend"]) == 2
    days = [row["day"] for row in data["trend"]]
    assert days == sorted(days)
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) for day in days)
    # days=1：只剩 2 小时前那条（2 天前与 40 天前都被截止时间排除）
    assert day_only["days"] == 1
    assert sum(row["count"] for row in day_only["trend"]) == 1


async def test_query_inspiration_trend_empty(client):
    async with async_session() as db:
        data = await stats.query_inspiration_trend(db, 7)

    assert data == {"days": 7, "trend": []}


# ═══════════════════════════════════════════════════════════════
#  最大文件
# ═══════════════════════════════════════════════════════════════


async def test_largest_files_sorted_and_missing_marked(client):
    """按磁盘大小降序；文件不存在时 size=0、exists=False（不抛异常）。"""
    _write("images/big.jpg", b"x" * 300)
    _write("images/small.jpg", b"x" * 100)
    big = _insp(
        file_path="images/big.jpg", source_type="douyin", created_at=datetime(2026, 1, 1)
    )
    small = _insp(file_path="images/small.jpg", created_at=datetime(2026, 1, 2))
    missing = _insp(file_path="images/missing.jpg", created_at=datetime(2026, 1, 3))
    await _add(big, small, missing)

    async with async_session() as db:
        files = await stats.largest_files(db)
        limited = await stats.largest_files(db, limit=1)

    assert [f["file_path"] for f in files] == [
        "images/big.jpg",
        "images/small.jpg",
        "images/missing.jpg",
    ]
    assert [f["size_bytes"] for f in files] == [300, 100, 0]
    assert [f["exists"] for f in files] == [True, True, False]
    assert files[0]["id"] == big.id
    assert files[0]["source_type"] == "douyin"
    assert files[0]["created_at"] == "2026-01-01T00:00:00Z"
    assert len(limited) == 1 and limited[0]["file_path"] == "images/big.jpg"


async def test_largest_files_empty(client):
    async with async_session() as db:
        assert await stats.largest_files(db) == []


# ═══════════════════════════════════════════════════════════════
#  重复文件
# ═══════════════════════════════════════════════════════════════


async def test_find_duplicates_groups_and_wasted_bytes(client):
    """内容相同的两份归一组并算浪费字节；不同内容/文件缺失不进组。"""
    _write("images/dup1.jpg", b"D" * 50)
    _write("images/dup2.jpg", b"D" * 50)
    _write("images/unique.jpg", b"U" * 60)
    dup1 = _insp(file_path="images/dup1.jpg")
    dup2 = _insp(file_path="images/dup2.jpg")
    unique = _insp(file_path="images/unique.jpg")
    gone = _insp(file_path="images/gone.jpg")
    await _add(dup1, dup2, unique, gone)

    async with async_session() as db:
        result = await stats.find_duplicates(db)

    assert result["duplicate_count"] == 1
    assert result["wasted_bytes"] == 50
    assert len(result["duplicate_groups"]) == 1
    group = result["duplicate_groups"][0]
    assert {item["id"] for item in group["files"]} == {dup1.id, dup2.id}
    assert group["files"][0]["size_bytes"] == 50
    assert unique.id not in {item["id"] for item in group["files"]}


async def test_find_duplicates_empty(client):
    async with async_session() as db:
        result = await stats.find_duplicates(db)

    assert result == {"duplicate_groups": [], "duplicate_count": 0, "wasted_bytes": 0}


# ═══════════════════════════════════════════════════════════════
#  缺失图像向量
# ═══════════════════════════════════════════════════════════════


async def test_get_missing_image_vector_ids_filters_video_and_deleted(client, monkeypatch):
    """只算未删除的图片素材；已有向量的排除。"""
    from app.services.vector import store as vector_store

    img1 = _insp()
    img2 = _insp()
    video = _insp(media_type="video")
    deleted = _insp(
        deleted_at=utcnow(), trash_reason="其他", trash_source="manual"
    )
    await _add(img1, img2, video, deleted)

    async def _list(kind: str) -> set[str]:
        assert kind == "image"
        return {img1.id}

    monkeypatch.setattr(vector_store, "list_vector_ids", _list)

    async with async_session() as db:
        missing = await stats._get_missing_image_vector_ids(db)

    assert missing == [img2.id]


async def test_get_missing_image_vector_ids_empty_library_skips_lancedb(
    client, monkeypatch
):
    """没有图片素材时直接返回空列表，不查向量库（省一次 IO）。"""
    from app.services.vector import store as vector_store

    calls: list[str] = []

    async def _list(kind: str) -> set[str]:
        calls.append(kind)
        return set()

    monkeypatch.setattr(vector_store, "list_vector_ids", _list)

    async with async_session() as db:
        assert await stats._get_missing_image_vector_ids(db) == []

    assert calls == []


# ═══════════════════════════════════════════════════════════════
#  向量统计
# ═══════════════════════════════════════════════════════════════


def _patch_vector_store(monkeypatch, *, text: int, image: int, stored: int | None, available: bool):
    """统一打桩 vector_store 的四个只读函数。"""
    from app.services.vector import store as vector_store

    async def _count(kind: str) -> int:
        return text if kind == "text" else image

    async def _list(_kind: str) -> set[str]:
        return set()

    monkeypatch.setattr(vector_store, "count_vectors", _count)
    monkeypatch.setattr(vector_store, "list_vector_ids", _list)
    monkeypatch.setattr(vector_store, "get_stored_text_formula_version", lambda: stored)
    monkeypatch.setattr(vector_store, "is_lancedb_available", lambda: available)


async def test_vector_stats_matching_version_not_stale(client, monkeypatch):
    img = _insp()
    await _add(img, _insp(media_type="video"))
    _patch_vector_store(
        monkeypatch,
        text=5,
        image=1,
        stored=TEXT_EMBEDDING_FORMULA_VERSION,
        available=True,
    )

    from app.services.vector import store as vector_store

    async def _existing(_kind: str) -> set[str]:
        return {img.id}

    monkeypatch.setattr(vector_store, "list_vector_ids", _existing)

    async with async_session() as db:
        result = await stats.vector_stats(db)

    assert result["total_inspirations"] == 1
    assert result["image_vectors"] == 1
    assert result["text_vectors"] == 5
    assert result["missing"] == 0
    assert result["lancedb_available"] is True
    assert result["text_vector_version"] == {
        "current": TEXT_EMBEDDING_FORMULA_VERSION,
        "stored": TEXT_EMBEDDING_FORMULA_VERSION,
        "stale": False,
    }


@pytest.mark.parametrize(
    "text, stored, stale",
    [
        (4, None, True),  # 从未记录版本 → 视为旧版本（v2 前的存量）
        (2, 1, True),  # 记录了旧版本
        (0, None, False),  # 没有文本向量 → 不算 stale
        (0, 1, False),
    ],
)
async def test_vector_stats_stale_matrix(client, monkeypatch, text, stored, stale):
    _patch_vector_store(monkeypatch, text=text, image=0, stored=stored, available=False)

    async with async_session() as db:
        result = await stats.vector_stats(db)

    assert result["text_vector_version"]["stale"] is stale
    assert result["lancedb_available"] is False


# ═══════════════════════════════════════════════════════════════
#  人物频次
# ═══════════════════════════════════════════════════════════════


async def test_person_frequency_merges_and_excludes_trash(client):
    blogger = Blogger(name="博主A", platform="douyin")
    model = Model(name="模特C", platform="xiaohongshu")
    await _add(blogger, model)

    i1 = _insp()
    i2 = _insp()
    trashed = _insp(
        deleted_at=utcnow(), trash_reason="不喜欢", trash_source="auto"
    )
    await _add(i1, i2, trashed)
    await _add(
        InspirationBlogger(inspiration_id=i1.id, blogger_id=blogger.id),
        InspirationBlogger(inspiration_id=i2.id, blogger_id=blogger.id),
        # 垃圾桶素材的关联不计数
        InspirationBlogger(inspiration_id=trashed.id, blogger_id=blogger.id),
        InspirationModel(inspiration_id=i2.id, model_id=model.id),
    )

    async with async_session() as db:
        rows = await stats.person_frequency(db)
        limited = await stats.person_frequency(db, limit=1)

    assert [row["name"] for row in rows] == ["博主A", "模特C"]
    assert [row["count"] for row in rows] == [2, 1]
    assert rows[0]["platform"] == "douyin"
    assert rows[1]["platform"] == "xiaohongshu"
    # union 两支各带类型判别列：博主行仍是 blogger
    assert rows[0]["person_type"] == "blogger"
    assert rows[1]["person_type"] == "model"
    assert len(limited) == 1 and limited[0]["name"] == "博主A"


async def test_person_frequency_empty(client):
    async with async_session() as db:
        assert await stats.person_frequency(db) == []


async def test_person_frequency_model_row_keeps_person_type(client):
    """union 后仍按来源带出 person_type：模特行是 model、博主行是 blogger。"""
    blogger = Blogger(name="博主B", platform="douyin")
    model = Model(name="模特C", platform="other")
    await _add(blogger, model)
    insp = _insp()
    await _add(insp)
    await _add(
        InspirationModel(inspiration_id=insp.id, model_id=model.id),
        InspirationBlogger(inspiration_id=insp.id, blogger_id=blogger.id),
    )

    async with async_session() as db:
        rows = await stats.person_frequency(db)

    by_name = {item["name"]: item for item in rows}
    assert by_name["模特C"]["person_type"] == "model"
    assert by_name["博主B"]["person_type"] == "blogger"


# ═══════════════════════════════════════════════════════════════
#  CSV 导出
# ═══════════════════════════════════════════════════════════════

_CSV_HEADER = [
    "id",
    "source_type",
    "source_author",
    "source_url",
    "media_type",
    "is_favorite",
    "quality_status",
    "quality_reason",
    "is_ai_generated",
    "dominant_colors",
    "tags",
    "bloggers",
    "models",
    "created_at",
    "updated_at",
]


async def test_export_inspirations_csv_headers_relations_and_injection(client):
    """表头列契约、标签/博主/模特拼接、时间带 Z、公式注入前缀加单引号。"""
    tag_white = Tag(name="白色", category="color")
    await _add(tag_white)
    blogger = Blogger(name="博主A", platform="douyin")
    model = Model(name="模特C", platform="other")
    await _add(blogger, model)

    rich = _insp(
        id="csv-rich",
        source_type="manual_upload",
        source_author="正常作者",
        source_url="https://example.com/1.jpg",
        is_favorite=True,
        quality_status="rejected",
        quality_reason="不是穿搭",
        is_ai_generated=True,
        dominant_colors='["#FFFFFF"]',
        created_at=datetime(2026, 1, 2, 3, 4, 5),
        updated_at=datetime(2026, 1, 2, 4, 0, 0),
    )
    inject = _insp(
        id="csv-inject",
        source_type="douyin",
        source_author="=cmd|calc",
        source_url="@evil",
        quality_reason="-minus",
    )
    tag_inject = Tag(name="=SUM(A1)", category="free")
    deleted = _insp(
        id="csv-deleted",
        deleted_at=utcnow(),
        trash_reason="其他",
        trash_source="manual",
    )
    await _add(rich, inject, deleted, tag_inject)
    await _add(
        InspirationTag(inspiration_id=rich.id, tag_id=tag_white.id),
        InspirationBlogger(inspiration_id=rich.id, blogger_id=blogger.id),
        InspirationModel(inspiration_id=rich.id, model_id=model.id),
        InspirationTag(inspiration_id=inject.id, tag_id=tag_inject.id),
    )

    async with async_session() as db:
        filename, text = await stats.export_inspirations_csv(db)

    assert re.fullmatch(r"inspirations_\d{8}_\d{6}\.csv", filename)
    assert not text.startswith("\ufeff")  # BOM 由路由层加，服务层只出文本

    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == _CSV_HEADER
    by_id = {row[0]: row for row in rows[1:]}
    assert set(by_id) == {"csv-rich", "csv-inject"}  # 垃圾桶素材不导出

    rich_row = by_id["csv-rich"]
    assert rich_row[1] == "manual_upload"
    assert rich_row[2] == "正常作者"
    assert rich_row[3] == "https://example.com/1.jpg"
    assert rich_row[5] == "1" and rich_row[8] == "1"
    assert rich_row[6] == "rejected" and rich_row[7] == "不是穿搭"
    assert set(rich_row[10].split("|")) == {"白色"}
    assert rich_row[11] == "博主A"
    assert rich_row[12] == "模特C"
    assert rich_row[13] == "2026-01-02T03:04:05Z"
    assert rich_row[14] == "2026-01-02T04:00:00Z"

    inject_row = by_id["csv-inject"]
    assert inject_row[2] == "'=cmd|calc"  # = 前缀被转义，Excel 不会当公式
    assert inject_row[3] == "'@evil"
    assert inject_row[7] == "'-minus"
    assert inject_row[10] == "'=SUM(A1)"


async def test_export_inspirations_csv_empty_library_header_only(client):
    """空库：只有表头且无 BOM（路由加 BOM，服务层不能重复加）。"""
    async with async_session() as db:
        _filename, text = await stats.export_inspirations_csv(db)

    rows = list(csv.reader(io.StringIO(text)))
    assert rows == [_CSV_HEADER]
    assert "\ufeff" not in text


# ═══════════════════════════════════════════════════════════════
#  完整性检查（Python 扫描路径）
# ═══════════════════════════════════════════════════════════════


async def test_integrity_check_python_missing_orphan_and_invariants(client, monkeypatch):
    """未配置 Rust sidecar 时走 Python：missing/orphan 判定与垃圾桶不变量接线。"""
    from app.services import storage_audit_service

    async def _unavailable(_paths):
        return None

    monkeypatch.setattr(storage_audit_service, "run_storage_audit", _unavailable)

    _write("images/present.jpg", b"x" * 10)
    _write("thumbnails/present_t.jpg", b"x" * 5)
    _write("images/orphan.jpg", b"x" * 7)
    _write("thumbnails/orphan2.jpg", b"x" * 3)
    _write("trash/trashed.jpg", b"x" * 4)  # trash 不参与素材扫描
    _write("lancedb/vector.bin", b"x" * 4)

    present = _insp(
        file_path="images/present.jpg", thumbnail_path="thumbnails/present_t.jpg"
    )
    missing = _insp(file_path="images/missing.jpg")
    # 已删除素材的文件缺失不算 missing（其文件应已移入 trash/）
    deleted = _insp(
        file_path="images/deleted_ghost.jpg",
        deleted_at=utcnow(),
        trash_reason="不喜欢",
        trash_source="manual",
    )
    # 人为破坏三字段不变量：deleted_at 非空但缺 reason/source → R1/R2
    broken_trash = _insp(
        file_path="images/broken_ghost.jpg", deleted_at=utcnow()
    )
    await _add(present, missing, deleted, broken_trash)

    async with async_session() as db:
        result = await stats.integrity_check(db)

    assert result["scanner"] == "python"
    assert result["missing_count"] == 1
    assert result["missing_files"] == [
        {"file_path": "images/missing.jpg", "inspiration_ids": [missing.id]}
    ]
    assert {item["file_path"]: item["size_bytes"] for item in result["orphan_files"]} == {
        "images/orphan.jpg": 7,
        "thumbnails/orphan2.jpg": 3,
    }
    assert result["orphan_count"] == 2
    assert result["orphan_total_size_bytes"] == 10
    rules = {(v["rule"], v["id"]) for v in result["trash_invariants"]}
    assert ("R1", broken_trash.id) in rules
    assert ("R2", broken_trash.id) in rules


async def test_integrity_check_python_healthy_library(client, monkeypatch):
    """文件都在、没有孤儿文件：计数全 0，垃圾桶不变量为空。"""
    from app.services import storage_audit_service

    async def _unavailable(_paths):
        return None

    monkeypatch.setattr(storage_audit_service, "run_storage_audit", _unavailable)

    _write("images/ok.jpg", b"x" * 6)
    _write("thumbnails/ok_t.jpg", b"x" * 2)
    insp = _insp(file_path="images/ok.jpg", thumbnail_path="thumbnails/ok_t.jpg")
    await _add(insp)

    async with async_session() as db:
        result = await stats.integrity_check(db)

    assert result["scanner"] == "python"
    assert result["missing_files"] == []
    assert result["missing_count"] == 0
    assert result["orphan_files"] == []
    assert result["orphan_count"] == 0
    assert result["orphan_total_size_bytes"] == 0
    assert result["trash_invariants"] == []


# ═══════════════════════════════════════════════════════════════
#  2026-11-27 性能优化：largest_files 候选 stat / 扫描缓存
# ═══════════════════════════════════════════════════════════════


async def test_largest_files_uses_file_size_candidates(client, monkeypatch):
    """有 file_size 时只对候选池 stat（limit*10），不全库逐条 stat；按实际大小排序。"""
    total = 200
    expected_sizes: dict[str, int] = {}
    objects = []
    for index in range(total):
        rel = f"images/cand-{index:03d}.jpg"
        size = (index + 1) * 10
        expected_sizes[rel] = size
        objects.append(
            _insp(
                file_path=rel,
                file_size=size,
                created_at=datetime(2026, 1, 1, 0, 0, index % 60),
            )
        )
    await _add(*objects)

    calls = {"n": 0}

    def _fake_size_and_exists(storage_root, relative_path):
        calls["n"] += 1
        return expected_sizes.get(relative_path or "", 0), True

    monkeypatch.setattr(stats, "_file_size_and_exists", _fake_size_and_exists)

    async with async_session() as db:
        files = await stats.largest_files(db, limit=5)

    assert [f["size_bytes"] for f in files] == [2000, 1990, 1980, 1970, 1960]
    assert set(files[0]) == {
        "id",
        "file_path",
        "source_type",
        "created_at",
        "size_bytes",
        "exists",
    }
    # 候选池 = limit*10 = 50，远小于 200 条；只对候选做 stat
    assert calls["n"] == 50


async def test_largest_files_null_file_size_fallback(client, monkeypatch):
    """file_size 为 NULL 的旧数据逐条 stat 兜底，参与按实际大小的排名。"""
    await _add(
        _insp(file_path="images/known.jpg", file_size=100),
        _insp(file_path="images/legacy-big.jpg"),
        _insp(file_path="images/legacy-small.jpg"),
        _insp(file_path="images/legacy-missing.jpg"),
    )
    actual = {
        "images/known.jpg": 100,
        "images/legacy-big.jpg": 500,
        "images/legacy-small.jpg": 50,
    }

    def _fake_size_and_exists(storage_root, relative_path):
        if relative_path in actual:
            return actual[relative_path], True
        return 0, False

    monkeypatch.setattr(stats, "_file_size_and_exists", _fake_size_and_exists)

    async with async_session() as db:
        files = await stats.largest_files(db, limit=10)

    assert [f["file_path"] for f in files] == [
        "images/legacy-big.jpg",
        "images/known.jpg",
        "images/legacy-small.jpg",
        "images/legacy-missing.jpg",
    ]
    assert [f["size_bytes"] for f in files] == [500, 100, 50, 0]
    assert [f["exists"] for f in files] == [True, True, True, False]


async def test_collect_stats_storage_scan_cache_hit_and_root_key(
    client, monkeypatch, tmp_path
):
    """collect_stats 复用 storage 扫描结果；cache key 含 storage root，换根必重扫。"""
    stats.clear_admin_scan_caches()
    calls = {"n": 0}

    def _fake_scan(include_trash: bool = False) -> dict[str, int]:
        calls["n"] += 1
        return {"images/a.jpg": 10, "thumbnails/t.jpg": 2}

    monkeypatch.setattr(stats, "scan_storage_files", _fake_scan)

    async with async_session() as db:
        first = await stats.collect_stats(db)
        second = await stats.collect_stats(db)

    assert calls["n"] == 1  # 第二次命中缓存，没有重新扫描 storage
    assert first["total_size_bytes"] == second["total_size_bytes"] == 12
    assert first["thumbnail_size_bytes"] == 2
    assert first["images_size_bytes"] == 10

    # cache key 含 storage root：换根后必须重新扫描
    monkeypatch.setattr(stats.settings, "storage_root", tmp_path / "other-storage")
    async with async_session() as db:
        third = await stats.collect_stats(db)
    assert calls["n"] == 2
    assert third["total_size_bytes"] == 12


async def test_collect_stats_storage_scan_cache_ttl_expiry(client, monkeypatch):
    """缓存超过 TTL 后失效，重新扫描 storage。"""
    stats.clear_admin_scan_caches()
    calls = {"n": 0}

    def _fake_scan(include_trash: bool = False) -> dict[str, int]:
        calls["n"] += 1
        return {"images/a.jpg": 10}

    monkeypatch.setattr(stats, "scan_storage_files", _fake_scan)
    monkeypatch.setattr(stats, "_STORAGE_SCAN_TTL_SECONDS", 0.0)

    async with async_session() as db:
        await stats.collect_stats(db)
        await stats.collect_stats(db)

    assert calls["n"] == 2


async def test_integrity_check_cache_hit_and_force_bypass(client, monkeypatch):
    """integrity_check 默认命中缓存；force=True 必须重新扫描并刷新缓存。"""
    from app.services import storage_audit_service

    stats.clear_admin_scan_caches()
    calls = {"n": 0}

    async def _fake_audit(paths):
        calls["n"] += 1
        return {"missing": [], "orphans": []}

    monkeypatch.setattr(storage_audit_service, "run_storage_audit", _fake_audit)

    async with async_session() as db:
        first = await stats.integrity_check(db)
        second = await stats.integrity_check(db)
    assert calls["n"] == 1
    assert first == second
    assert first["scanner"] == "rust"

    async with async_session() as db:
        forced = await stats.integrity_check(db, force=True)
    assert calls["n"] == 2  # force 绕缓存，重新扫描
    assert forced["scanner"] == "rust"

    # force 已刷新缓存：再次普通调用仍命中，不再扫描
    async with async_session() as db:
        again = await stats.integrity_check(db)
    assert calls["n"] == 2
    assert again["scanner"] == "rust"
