"""pytest 全局配置：临时数据库/存储 + TestClient fixture。

关键点：settings 是模块级单例（import app.config 时从环境变量/.env 加载），
因此必须在导入任何 app.* 模块之前设置测试环境变量（临时库 + 关闭 API Key）。
"""

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
os.environ.pop("API_KEY", None)  # 认证测试自行设置，默认开发模式跳过

atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# 数据库表清空顺序（按外键依赖：先子表后主表）
# 注意：ai_extracted_tags / ai_quality_review 是 ai_analysis_log 的结构化快照子表，
# 缺失会导致 AI 分析用例写入的快照跨用例残留，污染 model-stats 等聚合口径
_ALL_TABLES = [
    "ai_extracted_tags",
    "ai_quality_review",
    "inspiration_persons",
    "inspiration_tags",
    "ai_analysis_log",
    "tag_aliases",
    "scraper_seen_urls",
    "scraper_schedules",
    "scraper_tasks",
    "task_queue",
    "service_heartbeats",
    "audit_logs",
    "inspirations",
    "person_photos",
    "person_photo_sets",
    "persons",
    "tags",
]


@pytest.fixture(scope="session")
def client():
    """会话级 TestClient：以 context manager 触发 lifespan（建表 + 预设标签导入）。"""
    from app.main import app

    with TestClient(app) as c:
        yield c


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
        settings.storage_root / "quality_classifier",  # 负样本初筛器训练产物
    ]:
        if dir_path.exists():
            # 递归清理子目录文件：素材按「年月」子目录落盘（images/2026-08/xxx.jpg），
            # 仅清空顶层 iterdir() 会残留子目录文件，导致跨用例状态污染
            for f in dir_path.rglob("*"):
                if f.is_file():
                    f.unlink()
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
def create_person(client):
    """创建一个人物，返回响应 JSON。"""

    def _create(name: str = "测试博主", **overrides):
        body = {"name": name, "person_type": "blogger", "platform": "xiaohongshu"}
        body.update(overrides)
        r = client.post("/api/persons", json=body)
        assert r.status_code == 201, r.text
        return r.json()

    return _create
