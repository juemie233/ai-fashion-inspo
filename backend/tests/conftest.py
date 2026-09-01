"""pytest 全局配置：临时数据库/存储 + TestClient fixture。

关键点：settings 是模块级单例（import app.config 时从环境变量/.env 加载），
因此必须在导入任何 app.* 模块之前设置测试环境变量（临时库 + 关闭 API Key）。
"""

import asyncio
import atexit
import os
import shutil
import tempfile

# ── 测试环境变量（必须在 import app.* 之前设置）──
_TMP = tempfile.mkdtemp(prefix="pytest_fashion_")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP}/fashion_inspo.db"
os.environ["STORAGE_ROOT"] = f"{_TMP}/storage"
# 注意：images_dir 等派生字段是 Settings 类的默认值（类定义时用默认 storage_root 计算），
# 仅覆盖 STORAGE_ROOT 不会让它们跟着变，必须逐项覆盖，否则测试文件会写入真实 backend/storage/！
for _sub in (
    "images",
    "thumbnails",
    "videos",
    "trash",
    "person_photos",
    "person_thumbnails",
):
    os.environ[f"{_sub.upper()}_DIR"] = f"{_TMP}/storage/{_sub}"
# lancedb_dir 同为类字段默认值（storage_root/"lancedb"），必须显式覆盖，
# 否则测试会读写/清空真实 backend/storage/lancedb（历史事故：向量「几乎全部缺失」）
os.environ["LANCEDB_DIR"] = f"{_TMP}/storage/lancedb"
os.environ.pop("API_KEY", None)  # 认证测试自行设置，默认开发模式跳过

atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# 数据库表清空顺序（按外键依赖：先子表后主表）
# 注意：ai_extracted_tags / ai_quality_review 是 ai_analysis_log 的结构化快照子表，
# 缺失会导致 AI 分析用例写入的快照跨用例残留，污染 model-stats 等聚合口径
_ALL_TABLES = [
    "collection_items",
    "collections",
    "ai_extracted_tags",
    "ai_quality_review",
    "inspiration_bloggers",
    "inspiration_models",
    "inspiration_tags",
    "inspiration_face_detections",
    "ai_analysis_log",
    "tag_aliases",
    "scraper_seen_urls",
    "scraper_schedules",
    "scraper_tasks",
    "scraper_hashtags",
    "task_queue",
    "pending_vector_backfills",
    "service_heartbeats",
    "audit_logs",
    "model_photos",
    "model_photo_sets",
    "model_face_embeddings",
    "blogger_enrichment_skips",
    "person_groups",
    "inspirations",
    "bloggers",
    "models",
    "tag_history",
    "tags",
]


@pytest.fixture(scope="session")
def client():
    """会话级 TestClient：以 context manager 触发 lifespan（建表 + 预设标签导入）。"""
    import app.main as main_module

    # 测试环境禁用后台「定时采集调度循环」：该循环每 30s 触发一次
    # run_due_schedules，会在用例的 await 窗口内并发抢跑（例如「立即执行」
    # 推进 next_run_at 的用例，可能被调度循环抢先多创建一个采集任务），
    # 造成平台相关的偶发失败。测试需要调度行为时显式调用对应函数即可。
    async def _noop_schedule_loop() -> None:
        while True:
            await asyncio.sleep(3600)

    main_module._scraper_schedule_loop = _noop_schedule_loop

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session", autouse=True)
def _mock_clip_image_encoding():
    """全局 mock CLIP 图像向量编码，避免测试真实加载 600MB 模型（约 8s/会话）。

    背景：测试环境装有 sentence-transformers/torch 且 clip-ViT-B-32 权重已
    下载，首个触发图像向量的用例会真实加载模型（import torch + 读权重 +
    CUDA 初始化约 6~8 秒），进程内只加载一次但全部计入该用例。测试关注的是
    向量链路（登记/落库/回填/搜索）而非 CLIP 编码本身，故 patch
    ``embedding._encode_image_sync``——它是所有图像向量路径（单条重建、
    回填、以图搜图）经 ``asyncio.to_thread`` 调用的唯一同步入口，patch 后
    返回正确维度（lancedb_image_dim）的确定性假向量，跳过模型加载。

    需要真实 CLIP 的用例（如 test_clip_load_retries 直接测 _load_clip_model，
    不经过本函数）不受影响；确需真实编码的用例可在自身 monkeypatch 中还原。
    """
    from app.config import settings
    from app.services.vector import embedding as emb_module

    original = emb_module._encode_image_sync

    def _fake_encode(file_path=None, image_bytes=None):  # noqa: ANN001
        # 与 LanceDB 图像表 schema 同维度，batch/upsert 维度校验通过
        return [0.2] * settings.lancedb_image_dim

    emb_module._encode_image_sync = _fake_encode
    yield
    emb_module._encode_image_sync = original


@pytest.fixture(autouse=True)
def clean_state(client):
    """每个测试前清空数据库表与存储目录，保证用例相互隔离。

    同步 sqlite3 连接访问同一数据库文件（TestClient 请求结束后无持锁连接）。
    """
    from app.config import settings

    db_path = settings.storage_root.parent / "fashion_inspo.db"
    conn = sqlite3_connect(str(db_path))
    try:
        for table in _ALL_TABLES:
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()

    for dir_path in [
        settings.images_dir,
        settings.thumbnails_dir,
        settings.videos_dir,
        settings.person_photos_dir,
        settings.person_thumbnails_dir,
        settings.storage_root / "faces",  # 博主人脸缩略图缓存
        settings.storage_root / "quality_classifier",  # 负样本初筛器训练产物
        settings.storage_root / "_crop_backup",  # 裁剪原图备份（按时间戳分目录）
        settings.storage_root / "_crop_dups",  # 裁剪重复对比预览（按批次分目录）
        settings.lancedb_dir,  # 向量库（LanceDB 落盘目录，含 .text-formula-version 标记）
    ]:
        if dir_path.exists():
            # 递归清理子目录文件：素材按「年月」子目录落盘（images/2026-08/xxx.jpg），
            # 仅清空顶层 iterdir() 会残留子目录文件，导致跨用例状态污染
            for f in dir_path.rglob("*"):
                if f.is_file():
                    f.unlink()

    # 向量库连接缓存重置：LanceDB 连接/表对象持有目录与版本基线，目录被清空后
    # 旧连接指向已删除的数据，必须丢弃缓存让下一次操作懒加载重新连接建空表，
    # 否则本用例与后续用例共享同一连接的陈旧视图（向量写入/读取相互污染）
    from app.services.vector import store as vector_store

    vector_store.reset_connection()
    yield


def sqlite3_connect(path: str):
    """延迟导入 sqlite3，避免在环境变量设置前引入其他依赖。"""
    import sqlite3

    return sqlite3.connect(path)


# 生成图片的序列号：默认每次生成不同颜色，避免同一测试内多次上传内容相同被内容去重拦截
_image_seq = [0]


@pytest.fixture
def make_image():
    """生成一张测试图片的 (bytes, content_type)。

    color 显式传入时内容确定（用于内容去重测试）；不传时每次生成不同颜色，
    保证同一测试内多次上传互不重复。
    """

    def _make(color: tuple[int, int, int] | None = None, size=(64, 64), fmt: str = "JPEG"):
        from io import BytesIO

        from PIL import Image

        if color is None:
            _image_seq[0] += 1
            color = (
                (_image_seq[0] * 50) % 256,
                (_image_seq[0] * 80) % 256,
                (_image_seq[0] * 30) % 256,
            )
        buf = BytesIO()
        Image.new("RGB", size, color).save(buf, format=fmt)
        buf.seek(0)
        ctype = "image/png" if fmt == "PNG" else "image/jpeg"
        return buf.getvalue(), ctype

    return _make


@pytest.fixture
def upload(client, make_image):
    """上传一张素材，返回响应对象。overrides 可传 source_type/source_author 等表单字段。"""

    def _upload(color: tuple[int, int, int] | None = None, **overrides):
        data, ctype = make_image(color=color)
        files = {"file": ("test.jpg", data, ctype)}
        return client.post("/api/inspirations", files=files, data=overrides)

    return _upload


@pytest.fixture
def create_blogger(client):
    """创建一位穿搭博主，返回响应 JSON。"""

    def _create(name: str = "测试博主", **overrides):
        body = {"name": name, "platform": "xiaohongshu"}
        body.update(overrides)
        r = client.post("/api/bloggers", json=body)
        assert r.status_code == 201, r.text
        return r.json()

    return _create


@pytest.fixture
def create_model(client):
    """创建一位职业模特，返回响应 JSON。"""

    def _create(name: str = "测试模特", **overrides):
        body = {"name": name}
        body.update(overrides)
        r = client.post("/api/models", json=body)
        assert r.status_code == 201, r.text
        return r.json()

    return _create
