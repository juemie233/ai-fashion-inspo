"""穿搭博主人脸特征库测试：注册（平均池化）/ 重新注册 / 素材检测匹配 / 手动关联。

人脸识别子服务通过 monkeypatch 替换 face_client.embed 模拟（不依赖真实子服务），
embedding 用固定 512 维单位向量保证匹配确定性。
"""

import numpy as np
import pytest


def _unit_embedding(seed: int = 1) -> list[float]:
    """生成 512 维单位向量（种子固定，测试确定性）。"""
    rng = np.random.default_rng(seed)
    emb = rng.standard_normal(512).astype(np.float32)
    emb = emb / np.linalg.norm(emb)
    return emb.tolist()


def _patch_embed(monkeypatch, embedding: list[float], det_score: float = 0.9):
    """把 face_client.embed 替换为固定返回单张人脸的假实现。"""

    async def fake_embed(image_bytes: bytes, filename: str = "image.jpg") -> dict:
        return {
            "face_count": 1,
            "faces": [{"bbox": [0, 0, 10, 10], "det_score": det_score, "embedding": embedding}],
        }

    monkeypatch.setattr("app.services.blogger_face.face_client.embed", fake_embed)


def _patch_no_face(monkeypatch):
    async def fake_embed(image_bytes: bytes, filename: str = "image.jpg") -> dict:
        return {"face_count": 0, "faces": []}

    monkeypatch.setattr("app.services.blogger_face.face_client.embed", fake_embed)


# ═══════════════════════════════════════════════════════════════
#  博主人脸注册
# ═══════════════════════════════════════════════════════════════


def test_register_blogger_face_and_status(client, create_blogger, monkeypatch):
    """注册：多张照片平均池化入库；状态查询；重复注册覆盖（重新注册）。"""
    blogger = create_blogger(name="脸博甲")
    emb_a = _unit_embedding(1)
    _patch_embed(monkeypatch, emb_a)

    r = client.post(
        f"/api/bloggers/{blogger['id']}/face",
        files=[
            ("files", ("a.jpg", b"photo-a", "image/jpeg")),
            ("files", ("b.jpg", b"photo-b", "image/jpeg")),
        ],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["registered"] is True
    assert body["photos_used"] == 2

    # 状态查询
    s = client.get(f"/api/bloggers/{blogger['id']}/face").json()
    assert s["registered"] is True
    assert s["updated_at"]

    # 重新注册（不同特征）→ 覆盖
    _patch_embed(monkeypatch, _unit_embedding(2))
    r2 = client.post(
        f"/api/bloggers/{blogger['id']}/face",
        files=[("files", ("c.jpg", b"photo-c", "image/jpeg"))],
    )
    assert r2.status_code == 200
    assert r2.json()["photos_used"] == 1


def test_register_blogger_face_no_face_rejected(client, create_blogger, monkeypatch):
    """所有照片都检测不到人脸 → 400。"""
    blogger = create_blogger(name="无脸博")
    _patch_no_face(monkeypatch)
    r = client.post(
        f"/api/bloggers/{blogger['id']}/face",
        files=[("files", ("a.jpg", b"photo", "image/jpeg"))],
    )
    assert r.status_code == 400


def test_register_blogger_face_missing_blogger(client, monkeypatch):
    """博主不存在 → 404。"""
    _patch_embed(monkeypatch, _unit_embedding(1))
    r = client.post(
        "/api/bloggers/999999/face",
        files=[("files", ("a.jpg", b"photo", "image/jpeg"))],
    )
    assert r.status_code == 404


def test_register_blogger_face_too_many_photos(client, create_blogger, monkeypatch):
    """超过 5 张 → 422。"""
    blogger = create_blogger(name="多图博")
    files = [("files", (f"p{i}.jpg", b"x", "image/jpeg")) for i in range(6)]
    r = client.post(f"/api/bloggers/{blogger['id']}/face", files=files)
    assert r.status_code == 422


# ═══════════════════════════════════════════════════════════════
#  素材人脸检测与匹配
# ═══════════════════════════════════════════════════════════════


def test_detect_inspiration_faces_match(client, create_blogger, upload, monkeypatch):
    """检测命中：素材人脸与已注册博主特征一致 → 自动关联并带置信度。"""
    blogger = create_blogger(name="命中博")
    emb = _unit_embedding(7)
    _patch_embed(monkeypatch, emb)
    client.post(
        f"/api/bloggers/{blogger['id']}/face",
        files=[("files", ("m.jpg", b"blogger-photo", "image/jpeg"))],
    )

    insp_id = upload().json()["id"]
    r = client.post(f"/api/inspirations/{insp_id}/face-detect")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["face_count"] == 1
    det = body["detections"][0]
    assert det["matched_blogger_id"] == blogger["id"]
    assert det["confidence"] > 0.99  # 完全相同向量 → 相似度 ≈ 1

    # 列表接口带博主名
    lst = client.get(f"/api/inspirations/{insp_id}/face-detections").json()
    assert lst["detections"][0]["matched_blogger_name"] == "命中博"


def test_detect_inspiration_faces_no_match(client, create_blogger, upload, monkeypatch):
    """未命中：素材人脸与特征库差异大 → matched_blogger_id 为空（疑似未知人脸）。"""
    blogger = create_blogger(name="库博")
    _patch_embed(monkeypatch, _unit_embedding(1))
    client.post(
        f"/api/bloggers/{blogger['id']}/face",
        files=[("files", ("m.jpg", b"blogger-photo", "image/jpeg"))],
    )

    _patch_embed(monkeypatch, _unit_embedding(2))  # 素材人脸与库特征不同
    insp_id = upload().json()["id"]
    r = client.post(f"/api/inspirations/{insp_id}/face-detect")
    det = r.json()["detections"][0]
    assert det["matched_blogger_id"] is None
    assert det["confidence"] is None


def test_detect_manual_link_and_unlink(client, create_blogger, upload, monkeypatch):
    """手动关联/解除：低于阈值的人脸可手动指定博主，也可解除。"""
    blogger = create_blogger(name="手选博")
    _patch_embed(monkeypatch, _unit_embedding(5))
    client.post(
        f"/api/bloggers/{blogger['id']}/face",
        files=[("files", ("m.jpg", b"blogger-photo", "image/jpeg"))],
    )

    insp_id = upload().json()["id"]
    _patch_embed(monkeypatch, _unit_embedding(6))  # 未命中
    r = client.post(f"/api/inspirations/{insp_id}/face-detect")
    det_id = r.json()["detections"][0]["id"]

    # 手动关联
    r2 = client.put(
        f"/api/inspirations/{insp_id}/face-detections/{det_id}",
        json={"blogger_id": blogger["id"]},
    )
    assert r2.status_code == 200
    assert r2.json()["matched_blogger_id"] == blogger["id"]

    # 解除
    r3 = client.put(
        f"/api/inspirations/{insp_id}/face-detections/{det_id}",
        json={"blogger_id": None},
    )
    assert r3.json()["matched_blogger_id"] is None

    # 删除单条检测
    r4 = client.delete(f"/api/inspirations/{insp_id}/face-detections/{det_id}")
    assert r4.status_code == 200
    lst = client.get(f"/api/inspirations/{insp_id}/face-detections").json()
    assert lst["face_count"] == 0


def test_face_detect_missing_inspiration(client):
    """素材不存在 → 404。"""
    r = client.post("/api/inspirations/no-such-id/face-detect")
    assert r.status_code == 404
