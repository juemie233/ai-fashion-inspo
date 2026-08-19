"""博主人脸缩略图测试：bbox 裁剪纯函数 + 端到端链路（检测匹配 → 列表返回人脸小图）。

链路：素材人脸检测入库 bbox → 匹配博主 → 博主列表接口补齐 face_thumb_path →
缓存文件为 96x96 有效 JPEG；无匹配/删除博主等边界各自验证。
"""

import io

import numpy as np
import pytest
from PIL import Image

from app.config import settings
from app.services.face_thumbnail import (
    FACE_THUMB_SIZE,
    _crop_face,
    face_thumb_rel_path,
)


def _unit_embedding(seed: int = 1) -> list[float]:
    """生成 512 维单位向量（种子固定，测试确定性）。"""
    rng = np.random.default_rng(seed)
    emb = rng.standard_normal(512).astype(np.float32)
    emb = emb / np.linalg.norm(emb)
    return emb.tolist()


def _patch_embed(monkeypatch, embedding: list[float]):
    """把 face_client.embed 替换为固定返回单张人脸的假实现。"""

    async def fake_embed(image_bytes: bytes, filename: str = "image.jpg") -> dict:
        return {
            "face_count": 1,
            "faces": [{"bbox": [10, 10, 50, 50], "det_score": 0.9, "embedding": embedding}],
        }

    monkeypatch.setattr("app.services.blogger_face.face_client.embed", fake_embed)


# ═══════════════════════════════════════════════════════════════
#  bbox 裁剪纯函数
# ═══════════════════════════════════════════════════════════════


def test_crop_face_returns_square_jpeg(make_image):
    """正常 bbox：输出 96x96 正方形 JPEG，可被 PIL 打开。"""
    data, _ctype = make_image(color=(200, 100, 50))  # 64x64 纯色图
    out = _crop_face(data, [10, 10, 50, 50])
    with Image.open(io.BytesIO(out)) as img:
        assert img.size == (FACE_THUMB_SIZE, FACE_THUMB_SIZE)
        assert img.format == "JPEG"


def test_crop_face_clamps_out_of_bounds(make_image):
    """bbox 越界（贴边/出界）：外扩后 clamp 到图内，不产生黑边，仍正常输出。"""
    data, _ctype = make_image(color=(10, 200, 100))
    out = _crop_face(data, [-5, -5, 80, 80])  # 完全超出 64x64 图
    with Image.open(io.BytesIO(out)) as img:
        assert img.size == (FACE_THUMB_SIZE, FACE_THUMB_SIZE)


def test_crop_face_invalid_bbox_raises(make_image):
    """无效 bbox（退化为零面积）：抛 ValueError，由调用方降级处理。"""
    data, _ctype = make_image(color=(50, 50, 200))
    with pytest.raises(ValueError):
        _crop_face(data, [10, 10, 10, 10])


# ═══════════════════════════════════════════════════════════════
#  端到端：检测匹配 → 列表返回人脸小图
# ═══════════════════════════════════════════════════════════════


def test_face_thumbnail_end_to_end(client, create_blogger, upload, monkeypatch):
    """素材检测命中博主后：列表接口返回 face_thumb_path，缓存为有效小图且复用。"""
    blogger = create_blogger(name="脸图博")
    emb = _unit_embedding(7)
    _patch_embed(monkeypatch, emb)
    client.post(
        f"/api/bloggers/{blogger['id']}/face",
        files=[("files", ("m.jpg", b"blogger-photo", "image/jpeg"))],
    )

    insp_id = upload().json()["id"]
    r = client.post(f"/api/inspirations/{insp_id}/face-detect")
    assert r.status_code == 200, r.text
    assert r.json()["detections"][0]["matched_blogger_id"] == blogger["id"]

    # 列表接口返回人脸缩略图相对路径
    lst = client.get("/api/bloggers").json()
    item = next(i for i in lst["items"] if i["id"] == blogger["id"])
    assert item["face_thumb_path"] == f"faces/face_{blogger['id']}.jpg"

    # 缓存文件存在且为 96x96 有效 JPEG
    cached = settings.storage_root / face_thumb_rel_path(blogger["id"])
    assert cached.is_file()
    with Image.open(cached) as img:
        assert img.size == (FACE_THUMB_SIZE, FACE_THUMB_SIZE)
        assert img.format == "JPEG"

    # 再次请求列表：命中缓存（路径不变，不重复裁剪）
    lst2 = client.get("/api/bloggers").json()
    item2 = next(i for i in lst2["items"] if i["id"] == blogger["id"])
    assert item2["face_thumb_path"] == item["face_thumb_path"]

    # 详情接口同样带人脸缩略图
    detail = client.get(f"/api/bloggers/{blogger['id']}").json()
    assert detail["face_thumb_path"] == item["face_thumb_path"]


def test_face_thumbnail_absent_without_detection(client, create_blogger):
    """博主没有匹配到任何素材人脸：face_thumb_path 为 null，不生成缓存文件。"""
    blogger = create_blogger(name="无人脸博")
    lst = client.get("/api/bloggers").json()
    item = next(i for i in lst["items"] if i["id"] == blogger["id"])
    assert item["face_thumb_path"] is None
    assert not (settings.storage_root / face_thumb_rel_path(blogger["id"])).exists()


def test_face_thumbnail_unmatched_detection(client, create_blogger, upload, monkeypatch):
    """素材检测未命中博主（疑似未知人脸）：同样不生成缩略图。"""
    blogger = create_blogger(name="未中博")
    _patch_embed(monkeypatch, _unit_embedding(1))
    client.post(
        f"/api/bloggers/{blogger['id']}/face",
        files=[("files", ("m.jpg", b"blogger-photo", "image/jpeg"))],
    )
    _patch_embed(monkeypatch, _unit_embedding(2))  # 素材人脸与库特征不同
    insp_id = upload().json()["id"]
    r = client.post(f"/api/inspirations/{insp_id}/face-detect")
    assert r.json()["detections"][0]["matched_blogger_id"] is None

    lst = client.get("/api/bloggers").json()
    item = next(i for i in lst["items"] if i["id"] == blogger["id"])
    assert item["face_thumb_path"] is None


def test_delete_blogger_cleans_thumbnail_cache(client, create_blogger, make_image):
    """删除博主：人脸缩略图缓存文件同步清理，不残留孤儿文件。"""
    blogger = create_blogger(name="待删博")
    cached = settings.storage_root / face_thumb_rel_path(blogger["id"])
    cached.parent.mkdir(parents=True, exist_ok=True)
    data, _ctype = make_image(color=(30, 160, 90))
    cached.write_bytes(data)
    assert cached.is_file()

    r = client.delete(f"/api/bloggers/{blogger['id']}")
    assert r.status_code == 204
    assert not cached.exists()
