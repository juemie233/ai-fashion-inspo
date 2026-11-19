"""模特人脸注册（`services/model_face.py`）边界补测。

既有 `tests/test_model_face.py` 走 API 主路径；这里直接调服务函数，补齐：
top_k 校验、无照片/照片不可读、子服务不可用、item 级失败告警、置信度/人脸宽度
过滤、零向量、Top-K 截断与覆盖注册、状态查询的时间格式等分支。
"""

from __future__ import annotations

import io
import uuid

import numpy as np
import pytest
from fastapi import HTTPException
from PIL import Image

from app.config import settings
from app.database import async_session
from app.models.face import ModelFaceEmbedding
from app.models.person import Model, ModelPhoto, ModelPhotoSet
from app.services.blogger_face import LOW_CONFIDENCE_THRESHOLD
from app.services.model_face import (
    DEFAULT_TOP_K,
    MAX_TOP_K,
    get_model_face_status,
    register_model_face,
)
from app.services.face_client import FaceServiceUnavailableError


def _unit_embedding(seed: int = 1) -> list[float]:
    rng = np.random.default_rng(seed)
    emb = rng.standard_normal(512).astype(np.float32)
    emb /= np.linalg.norm(emb)
    return emb.tolist()


def _image_bytes(color=(90, 120, 150), size=(100, 100)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


async def _setup_model(
    n_photos: int = 3, missing_files: tuple[int, ...] = ()
) -> int:
    """建模特 + 一个照片组 + n 张照片（文件真实落盘到临时存储）。"""
    async with async_session() as db:
        model = Model(name="面部模特")
        db.add(model)
        await db.flush()
        photo_set = ModelPhotoSet(model_id=model.id, name="写真组")
        db.add(photo_set)
        await db.flush()
        for index in range(n_photos):
            rel = f"model_photos/{uuid.uuid4().hex}_{index}.jpg"
            if index not in missing_files:
                path = settings.storage_root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(_image_bytes(color=(index * 20, 100, 150)))
            db.add(ModelPhoto(set_id=photo_set.id, file_path=rel, sort_order=index))
        await db.commit()
        return model.id


def _patch_embed_batch(monkeypatch, items: list[dict]) -> None:
    async def fake_embed_batch(images, filenames=None):  # noqa: ANN001
        return {"items": items, "failed": 0}

    monkeypatch.setattr("app.services.model_face.face_client.embed_batch", fake_embed_batch)


def _face_item(index: int, faces: list[dict]) -> dict:
    return {"index": index, "face_count": len(faces), "faces": faces}


def _face(det_score: float, bbox: list | None = None, embedding=None) -> dict:
    return {
        "bbox": bbox if bbox is not None else [0, 0, 20, 20],
        "det_score": det_score,
        "embedding": embedding if embedding is not None else _unit_embedding(1),
    }


# ── 参数 / 前置校验 ──


@pytest.mark.parametrize("top_k", [0, MAX_TOP_K + 1])
async def test_top_k_out_of_range(top_k: int):
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await register_model_face(db, 1, top_k=top_k)
    assert exc.value.status_code == 422
    assert "1~9" in exc.value.detail


async def test_model_not_found():
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await register_model_face(db, 999999)
    assert exc.value.status_code == 404
    assert exc.value.detail == "模特未找到"


async def test_no_photos_raises_400():
    model_id = await _setup_model(n_photos=0)
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await register_model_face(db, model_id)
    assert exc.value.status_code == 400
    assert "暂无照片" in exc.value.detail


async def test_all_photos_unreadable_raises_400():
    model_id = await _setup_model(n_photos=2, missing_files=(0, 1))
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await register_model_face(db, model_id)
    assert exc.value.status_code == 400
    assert "均无法读取" in exc.value.detail


async def test_face_service_unavailable_maps_to_503(monkeypatch):
    model_id = await _setup_model(n_photos=1)

    async def _down(images, filenames=None):  # noqa: ANN001
        raise FaceServiceUnavailableError("人脸子服务未配置")

    monkeypatch.setattr("app.services.model_face.face_client.embed_batch", _down)
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await register_model_face(db, model_id)
    assert exc.value.status_code == 503
    assert exc.value.detail == "人脸子服务未配置"


# ── 单张失败告警 / 质量过滤 ──


async def test_mixed_unreadable_photo_produces_warning(monkeypatch):
    """部分照片读不到：跳过并记 warning，其余照片照常注册。"""
    model_id = await _setup_model(n_photos=2, missing_files=(0,))
    _patch_embed_batch(monkeypatch, [_face_item(0, [_face(0.9)])])

    async with async_session() as db:
        out = await register_model_face(db, model_id)

    assert out["photos_total"] == 2
    assert out["photos_used"] == 1
    assert len(out["warnings"]) == 1
    assert "读取失败" in out["warnings"][0]


async def test_item_level_failures_become_warnings(monkeypatch):
    """embed-batch 返回的 error / 缺失 index / 越界 index 都跳过并告警。"""
    model_id = await _setup_model(n_photos=4)
    items = [
        {"index": 0, "error": "decode failed", "faces": []},
        {"index": None, "faces": []},
        {"index": 99, "faces": []},
        _face_item(3, [_face(0.9)]),
    ]
    _patch_embed_batch(monkeypatch, items)

    async with async_session() as db:
        out = await register_model_face(db, model_id)

    assert out["photos_used"] == 1
    assert len(out["warnings"]) == 3
    assert all("处理失败" in w for w in out["warnings"])


async def test_low_confidence_faces_filtered(monkeypatch):
    model_id = await _setup_model(n_photos=2)
    _patch_embed_batch(
        monkeypatch,
        [_face_item(0, [_face(LOW_CONFIDENCE_THRESHOLD - 0.01)]), _face_item(1, [])],
    )
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await register_model_face(db, model_id)
    assert exc.value.status_code == 400
    assert "未检出清晰人脸" in exc.value.detail


async def test_small_face_filtered(monkeypatch):
    """人脸宽度占比低于门槛 → 该照片跳过；全部跳过则 400。"""
    model_id = await _setup_model(n_photos=1)
    _patch_embed_batch(monkeypatch, [_face_item(0, [_face(0.9)])])
    monkeypatch.setattr(
        "app.services.model_face._face_width_ratio", lambda bbox, size: 0.01
    )
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await register_model_face(db, model_id)
    assert exc.value.status_code == 400


async def test_zero_vector_rejected(monkeypatch):
    """平均池化后为零向量（norm≈0）→ 400，不写库。"""
    model_id = await _setup_model(n_photos=1)
    zero = [0.0] * 512
    _patch_embed_batch(monkeypatch, [_face_item(0, [_face(0.9, embedding=zero)])])
    monkeypatch.setattr(
        "app.services.model_face._face_width_ratio", lambda bbox, size: None
    )
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await register_model_face(db, model_id)
    assert exc.value.status_code == 400
    assert "零向量" in exc.value.detail


# ── 成功路径：Top-K 截断 / 归一化 / 覆盖注册 / 状态查询 ──


async def test_top_k_truncation_and_normalization(monkeypatch):
    model_id = await _setup_model(n_photos=3)
    _patch_embed_batch(
        monkeypatch,
        [
            _face_item(0, [_face(0.9, embedding=_unit_embedding(1))]),
            _face_item(1, [_face(0.8, embedding=_unit_embedding(2))]),
            _face_item(2, [_face(0.7, embedding=_unit_embedding(3))]),
        ],
    )

    async with async_session() as db:
        out = await register_model_face(db, model_id, top_k=2)

    assert out["registered"] is True
    assert out["photos_total"] == 3
    assert out["photos_used"] == 2  # Top-K 截断
    assert out["qualified"] == 3
    assert out["warnings"] == []
    assert out["updated_at"]

    async with async_session() as db:
        record = (
            await db.execute(
                ModelFaceEmbedding.__table__.select().where(
                    ModelFaceEmbedding.model_id == model_id
                )
            )
        ).first()
        assert record is not None
        vector = np.frombuffer(record.embedding, dtype=np.float32)
        assert vector.shape == (512,)
        assert float(np.linalg.norm(vector)) == pytest.approx(1.0, abs=1e-5)


async def test_reregister_overwrites_single_row(monkeypatch):
    model_id = await _setup_model(n_photos=3)
    _patch_embed_batch(
        monkeypatch,
        [_face_item(index, [_face(0.9 - index * 0.01)]) for index in range(3)],
    )
    async with async_session() as db:
        first = await register_model_face(db, model_id, top_k=DEFAULT_TOP_K)
    assert first["photos_used"] == 3

    async with async_session() as db:
        second = await register_model_face(db, model_id, top_k=1)
    assert second["photos_used"] == 1

    async with async_session() as db:
        rows = (
            await db.execute(
                ModelFaceEmbedding.__table__.select().where(
                    ModelFaceEmbedding.model_id == model_id
                )
            )
        ).all()
    assert len(rows) == 1  # 一位模特一条（覆盖式）


async def test_face_status_registered_and_unregistered(monkeypatch):
    plain_id = (await _setup_model(n_photos=0))
    async with async_session() as db:
        status = await get_model_face_status(db, plain_id)
    assert status == {"registered": False, "model_id": plain_id}

    model_id = await _setup_model(n_photos=1)
    _patch_embed_batch(monkeypatch, [_face_item(0, [_face(0.9)])])
    async with async_session() as db:
        await register_model_face(db, model_id)
    async with async_session() as db:
        status = await get_model_face_status(db, model_id)
    assert status["registered"] is True
    assert status["model_id"] == model_id
    assert status["updated_at"].endswith("Z")  # API 输出必须带时区后缀
