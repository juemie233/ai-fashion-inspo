"""L2 系统测试首个 smoke：真 uvicorn + 临时 DB/存储，全链路走 HTTP。

覆盖 Phase 1（见 `docs/overview/测试体系与系统测试方案-2026-11-14.md` §6）：

1. 健康检查可用；
2. 上传素材 → 文件落到**临时**存储、列表可见；
3. 关键词搜索能检索到；
4. 移入垃圾桶 → 默认列表消失、垃圾桶可见；
5. 恢复 → 默认列表重新出现、文件回到媒体目录；
6. 直连**临时 SQLite** 校验状态（证明 API 进程真的用了隔离库）。
"""

import io
import sqlite3
from pathlib import Path

import pytest
from PIL import Image

pytestmark = pytest.mark.system


def _png(color: tuple[int, int, int]) -> bytes:
    """生成一张真实可解码的 PNG（不同颜色 = 不同内容哈希，避免内容去重 409）。"""
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), color).save(buffer, format="PNG")
    return buffer.getvalue()

_PNG_1PX = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0fIDATx\x9cc\xf8\xcf\xc0"
    b"\x00\x00\x01\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
)

_SOURCE_AUTHOR = "system-test-作者"


def _ids(payload: dict) -> set[str]:
    return {item["id"] for item in payload.get("items", [])}


def test_health(api):
    response = api.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["schema_version"]


def test_upload_list_search_trash_restore(api, system_root: Path):
    # 上传：文件真实落盘（临时 storage）
    upload = api.post(
        "/api/inspirations",
        files={"file": ("system-smoke.png", _png((200, 150, 120)), "image/png")},
        data={"source_type": "manual_upload", "source_author": _SOURCE_AUTHOR},
    )
    assert upload.status_code == 201, upload.text
    inspiration = upload.json()
    inspiration_id = inspiration["id"]
    file_path = system_root / "storage" / inspiration["file_path"]
    assert file_path.exists(), f"上传文件未落到临时存储：{file_path}"

    # 列表可见
    listing = api.get("/api/inspirations", params={"size": 100})
    assert listing.status_code == 200
    assert inspiration_id in _ids(listing.json())

    # 关键词搜索（作者名）
    search = api.get("/api/search", params={"keyword": _SOURCE_AUTHOR})
    assert search.status_code == 200, search.text
    assert inspiration_id in _ids(search.json())

    # 移入垃圾桶：默认列表消失、垃圾桶列表可见、文件移到 trash/
    trashed = api.post(
        f"/api/inspirations/{inspiration_id}/trash", json={"reason": "其他"}
    )
    assert trashed.status_code == 200, trashed.text
    assert trashed.json()["deleted_at"] is not None

    listing = api.get("/api/inspirations", params={"size": 100})
    assert inspiration_id not in _ids(listing.json())
    trash_list = api.get("/api/inspirations/trash", params={"size": 100})
    assert trash_list.status_code == 200, trash_list.text
    assert inspiration_id in _ids(trash_list.json())

    # 恢复：默认列表重新出现、文件回媒体目录
    restored = api.post(f"/api/inspirations/{inspiration_id}/restore")
    assert restored.status_code == 200, restored.text
    assert restored.json()["deleted_at"] is None
    listing = api.get("/api/inspirations", params={"size": 100})
    assert inspiration_id in _ids(listing.json())
    # 恢复后以「接口返回的当前 file_path」为准（trash/restore 可能规范化文件名）
    detail = api.get(f"/api/inspirations/{inspiration_id}")
    assert detail.status_code == 200, detail.text
    restored_path = system_root / "storage" / detail.json()["file_path"]
    assert restored_path.exists(), f"恢复后文件不在媒体目录：{restored_path}"


def test_database_is_isolated(api, system_root: Path):
    """直连临时 SQLite：API 进程确实写的是隔离库，而不是真实库。"""
    upload = api.post(
        "/api/inspirations",
        files={"file": ("system-db.png", _png((10, 120, 200)), "image/png")},
        data={"source_type": "manual_upload"},
    )
    assert upload.status_code == 201, upload.text
    inspiration_id = upload.json()["id"]

    db_path = system_root / "db.sqlite"
    assert db_path.exists(), f"临时数据库不存在：{db_path}"

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, source_type FROM inspirations WHERE id = ?", (inspiration_id,)
        ).fetchone()
    assert row is not None, "API 写入的行不在临时库里（可能连到了真实库）"
    assert row[1] == "manual_upload"
