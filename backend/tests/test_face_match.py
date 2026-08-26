"""人脸矩阵匹配测试：matrix_match_faces 纯函数 + match_all_faces 全库候选匹配。

- matrix_match_faces：纯 numpy 函数，直接单测（阈值过滤/互斥取最高分/空库）；
- match_all_faces：走真实 DB（async_session 工厂 + TestClient lifespan 建表），
  覆盖 博主/模特特征注册 → 素材检测 → 全库匹配写 pending → 幂等（只写变化行）。
"""

import numpy as np
import pytest
from sqlalchemy import select

from app.database import async_session
from app.models.face import InspirationFaceDetection
from app.services.face_match import match_all_faces, matrix_match_faces


def _unit(seed: int) -> np.ndarray:
    """生成 512 维单位向量（种子固定）。"""
    rng = np.random.default_rng(seed)
    emb = rng.standard_normal(512).astype(np.float32)
    return emb / np.linalg.norm(emb)


def _near(seed: int, base: np.ndarray, mix: float = 0.05) -> np.ndarray:
    """与 base 近似同向的单位向量（cos ≈ 1 - mix^2/2）。"""
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(512).astype(np.float32)
    v = (1 - mix) * base + mix * noise
    return v / np.linalg.norm(v)


# ═══════════════════════════════════════════════════════════════
#  matrix_match_faces 纯函数
# ═══════════════════════════════════════════════════════════════


def test_matrix_match_blogger_and_model_mutual_exclusion():
    """互斥取最高分：脸与博主同向（cos=1）时即使模特也相似仍命中博主。"""
    base = _unit(1)
    library = [
        {"person_type": "blogger", "person_id": 10, "embedding": base},
        {"person_type": "model", "person_id": 20, "embedding": _near(2, base)},
    ]
    faces = np.stack([base, _unit(9)], axis=0)
    results = matrix_match_faces(faces, library, threshold=0.5)
    assert results[0] == {"person_type": "blogger", "person_id": 10, "score": pytest.approx(1.0, abs=1e-5)}
    assert results[1] is None  # 正交向量低于阈值


def test_matrix_match_threshold_filter():
    """阈值过滤：低于阈值的脸返回 None。"""
    base = _unit(1)
    library = [{"person_type": "blogger", "person_id": 1, "embedding": base}]
    far = _unit(3)  # 与 base 独立随机 → cos 接近 0
    results = matrix_match_faces(np.stack([far], axis=0), library, threshold=0.5)
    assert results == [None]


def test_matrix_match_empty_library():
    """空特征库：全部返回 None（不崩溃）。"""
    faces = np.stack([_unit(1), _unit(2)], axis=0)
    assert matrix_match_faces(faces, [], threshold=0.5) == [None, None]


def test_matrix_match_empty_faces():
    """空人脸：返回空列表。"""
    library = [{"person_type": "blogger", "person_id": 1, "embedding": _unit(1)}]
    assert matrix_match_faces(np.zeros((0, 512), dtype=np.float32), library, 0.5) == []


# ═══════════════════════════════════════════════════════════════
#  match_all_faces 集成（真实 DB）
# ═══════════════════════════════════════════════════════════════


def _make_photo_bytes(color):
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (32, 32), color).save(buf, format="JPEG")
    buf.seek(0)
    return buf.getvalue(), "image/jpeg"


def _setup_blogger_face(client, create_blogger, monkeypatch, embedding):
    """创建博主并注册人脸（mock face_client.embed）。"""
    blogger = create_blogger(name="脸博主")
    async def fake_embed(image_bytes, filename="image.jpg"):
        return {
            "face_count": 1,
            "faces": [{"bbox": [0, 0, 10, 10], "det_score": 0.9, "embedding": embedding}],
        }
    monkeypatch.setattr("app.services.blogger_face.face_client.embed", fake_embed)
    r = client.post(
        f"/api/bloggers/{blogger['id']}/face",
        files=[("files", ("a.jpg", b"photo", "image/jpeg"))],
    )
    assert r.status_code == 200, r.text
    return blogger


def _setup_model_face(client, create_model, monkeypatch, embedding):
    """创建模特 + 照片组 + 照片并注册人脸（mock face_client.embed_batch）。"""
    model = create_model(name="脸模特")
    r = client.post(f"/api/models/{model['id']}/photo-sets", json={"name": "写真"})
    assert r.status_code == 201, r.text
    set_id = r.json()["id"]
    data, ctype = _make_photo_bytes((10, 20, 30))
    r = client.post(
        f"/api/models/{model['id']}/photo-sets/{set_id}/photos",
        files={"file": ("a.jpg", data, ctype)},
        data={"sort_order": "0"},
    )
    assert r.status_code == 201, r.text

    async def fake_embed_batch(images, filenames=None):
        return {
            "items": [
                {
                    "index": 0,
                    "face_count": 1,
                    "faces": [{"bbox": [0, 0, 10, 10], "det_score": 0.9, "embedding": embedding}],
                }
            ],
            "failed": 0,
        }

    monkeypatch.setattr("app.services.model_face.face_client.embed_batch", fake_embed_batch)
    r = client.post(f"/api/models/{model['id']}/face")
    assert r.status_code == 200, r.text
    return model


def _setup_inspiration_faces(client, monkeypatch, faces):
    """上传素材并检测（mock face_client.embed 返回 faces 列表）。"""
    data, ctype = _make_photo_bytes((200, 30, 40))
    r = client.post(
        "/api/inspirations",
        files={"file": ("insp.jpg", data, ctype)},
        data={"source_type": "manual_upload"},
    )
    assert r.status_code == 201, r.text
    inspiration_id = r.json()["id"]

    async def fake_embed(image_bytes, filename="image.jpg"):
        return {"face_count": len(faces), "faces": faces}

    monkeypatch.setattr("app.services.blogger_face.face_client.embed", fake_embed)
    r = client.post(f"/api/inspirations/{inspiration_id}/face-detect")
    assert r.status_code == 200, r.text
    return inspiration_id


async def test_match_all_faces_writes_pending(
    client, create_blogger, create_model, monkeypatch
):
    """全库匹配：博主/模特合并库取最高分，写入 pending；未命中保持空。"""
    base = _unit(1)
    blogger = _setup_blogger_face(client, create_blogger, monkeypatch, base.tolist())
    model = _setup_model_face(client, create_model, monkeypatch, _near(2, base).tolist())
    # 素材两张脸：脸0 与博主同向（cos=1 > 与模特 ~0.998），脸1 随机（未命中）
    insp_id = _setup_inspiration_faces(
        client,
        monkeypatch,
        [
            {"bbox": [0, 0, 10, 10], "det_score": 0.9, "embedding": base.tolist()},
            {"bbox": [10, 10, 20, 20], "det_score": 0.9, "embedding": _unit(9).tolist()},
        ],
    )

    async with async_session() as db:
        stats = await match_all_faces(db)
        assert stats["total_faces"] == 2
        assert stats["matched"] == 1
        assert stats["unmatched"] == 1
        assert stats["updated"] == 2
        assert stats["library_size"] == 2

        detections = (
            await db.execute(
                select(InspirationFaceDetection).where(
                    InspirationFaceDetection.inspiration_id == insp_id
                )
            )
        ).scalars().all()
        detections = sorted(detections, key=lambda d: d.face_index)
        # 脸0：命中博主（取最高分者），pending 候选
        assert detections[0].matched_blogger_id == blogger["id"]
        assert detections[0].matched_model_id is None
        assert detections[0].match_status == "pending"
        assert detections[0].confidence > 0.9
        # 脸1：未命中
        assert detections[1].matched_blogger_id is None
        assert detections[1].matched_model_id is None
        assert detections[1].match_status == "pending"

        # 幂等：再跑一次无变化
        stats2 = await match_all_faces(db)
        assert stats2["updated"] == 0


async def test_match_all_faces_model_scope(client, create_blogger, create_model, monkeypatch):
    """scope=models 时只与模特库比对，博主特征不参与。"""
    base = _unit(1)
    _setup_blogger_face(client, create_blogger, monkeypatch, base.tolist())
    model = _setup_model_face(client, create_model, monkeypatch, _near(2, base).tolist())
    insp_id = _setup_inspiration_faces(
        client,
        monkeypatch,
        [{"bbox": [0, 0, 10, 10], "det_score": 0.9, "embedding": base.tolist()}],
    )

    async with async_session() as db:
        stats = await match_all_faces(db, scope="models")
        # 脸与博主同向（cos=1）但与模特 ~0.998：scope=models 下命中模特
        assert stats["matched"] == 1
        detections = (
            await db.execute(
                select(InspirationFaceDetection).where(
                    InspirationFaceDetection.inspiration_id == insp_id
                )
            )
        ).scalars().all()
        det = detections[0]
        assert det.matched_model_id == model["id"]
        assert det.matched_blogger_id is None
        assert det.match_status == "pending"


async def test_match_all_faces_skips_excluded(client, create_blogger, monkeypatch):
    """人工「不匹配」（match_excluded=True）的人脸不再参与全库匹配。

    修复前：reject 只清空匹配字段，下次 match_all_faces 会重新匹配并再次
    产出 pending 候选（同一张被拒图反复出现）。现在 excluded 记录被排除。
    """
    base = _unit(1)
    blogger = _setup_blogger_face(client, create_blogger, monkeypatch, base.tolist())
    insp_id = _setup_inspiration_faces(
        client,
        monkeypatch,
        [{"bbox": [0, 0, 10, 10], "det_score": 0.9, "embedding": base.tolist()}],
    )

    async with async_session() as db:
        # 第一轮全库匹配：产出 pending 候选
        stats = await match_all_faces(db)
        assert stats["matched"] == 1
        dets = (
            await db.execute(
                select(InspirationFaceDetection).where(
                    InspirationFaceDetection.inspiration_id == insp_id
                )
            )
        ).scalars().all()
        assert dets[0].matched_blogger_id == blogger["id"]
        assert dets[0].match_status == "pending"

        # 人工「不匹配」：置 match_excluded=True（等价于扫描页 reject 动作）
        dets[0].match_excluded = True
        dets[0].matched_blogger_id = None
        dets[0].confidence = None
        dets[0].match_status = None
        await db.commit()

        # 第二轮全库匹配：excluded 记录被排除，不再产出候选
        stats2 = await match_all_faces(db)
        assert stats2["total_faces"] == 0
        assert stats2["matched"] == 0

        dets2 = (
            await db.execute(
                select(InspirationFaceDetection).where(
                    InspirationFaceDetection.inspiration_id == insp_id
                )
            )
        ).scalars().all()
        assert dets2[0].match_excluded is True
        assert dets2[0].match_status is None
        assert dets2[0].matched_blogger_id is None
