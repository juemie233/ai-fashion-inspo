"""quality_learner（负样本初筛器）单测：训练、样本不足、状态、回滚。

向量来源（vector_store）用 monkeypatch 替代，避免依赖真实 LanceDB/CLIP。
"""

import random

from app.database import async_session
from app.services import quality_learner
from app.services.vector import store as vector_store


def _make_vec(seed: str) -> list[float]:
    rng = random.Random(seed)
    return [round(rng.random(), 6) for _ in range(512)]


async def _fake_get_vectors(kind, insp_ids):
    """vector_store.get_vectors_batch 的异步替身：为每个 id 返回固定随机向量。"""
    return {i: _make_vec(i) for i in insp_ids}


def test_status_untrained_by_default():
    quality_learner.reset()  # 兜底：确保无残留模型
    status = quality_learner.get_status()
    assert status["trained"] is False
    assert status["model_path"]


def test_reset_removes_model_files():
    # 伪造已训练状态（写入假模型文件）
    quality_learner._MODEL_DIR.mkdir(parents=True, exist_ok=True)
    quality_learner._MODEL_PATH.write_bytes(b"fake-model")
    quality_learner._META_PATH.write_text("{}", encoding="utf-8")
    assert quality_learner.get_status()["trained"] is True

    r = quality_learner.reset()
    assert r["reset"] is True
    assert not quality_learner._MODEL_PATH.exists()
    assert quality_learner.get_status()["trained"] is False


async def test_train_success_and_rollback(client, upload, monkeypatch):
    """足够样本（正 6 / 负 6）训练成功，落盘后 reset 回滚。"""
    for i in range(12):
        insp = upload().json()
        status = "approved" if i % 2 == 0 else "rejected"
        client.patch(
            f"/api/inspirations/{insp['id']}", json={"quality_status": status}
        )

    monkeypatch.setattr(vector_store, "is_lancedb_available", lambda: True)
    monkeypatch.setattr(vector_store, "get_vectors_batch", _fake_get_vectors)

    async with async_session() as db:
        meta = await quality_learner.train(db)

    assert "error" not in meta, meta
    assert meta["sample_total"] == 12
    assert meta["positive"] == 6
    assert meta["negative"] == 6
    assert "metrics" in meta

    assert quality_learner.get_status()["trained"] is True

    # 回滚（指标变差时删除模型回到纯 VLM 审核）
    r = quality_learner.reset()
    assert r["reset"] is True
    assert quality_learner.get_status()["trained"] is False


async def test_train_single_class_error(client, upload, monkeypatch):
    """只有单类样本 → 明确 error，不落盘。"""
    for _ in range(6):
        insp = upload().json()
        client.patch(f"/api/inspirations/{insp['id']}", json={"quality_status": "approved"})

    monkeypatch.setattr(vector_store, "is_lancedb_available", lambda: True)
    monkeypatch.setattr(vector_store, "get_vectors_batch", _fake_get_vectors)

    async with async_session() as db:
        meta = await quality_learner.train(db)
    assert "error" in meta
    assert quality_learner.get_status()["trained"] is False


async def test_train_lancedb_unavailable(client, monkeypatch):
    """lancedb 不可用时直接返回 error，不查询。"""
    monkeypatch.setattr(vector_store, "is_lancedb_available", lambda: False)

    async with async_session() as db:
        meta = await quality_learner.train(db)
    assert "error" in meta
