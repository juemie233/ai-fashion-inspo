"""负样本初筛器（quality_learner）的直连边缘单测。

既有 test_unit_quality_learner.py 覆盖「训练成功/样本不足/状态/回滚」主路径；
本文件直接 await 服务函数并打桩向量与落盘目录，补齐：``_train_sync`` 的两类
error、训练指标与元数据落盘、样本极不均衡时的非分层降级、``_load_model`` 的
mtime 缓存、``_predict_sync``/``predict_vector`` 的阈值与异常、``get_status``
的坏元数据、``reset`` 的删除异常吞掉、``collect_samples`` 的隐私与 AI 图排除，
以及 ``train`` 的「样本不足」提示文案。
"""

import json
import os
from pathlib import Path

import pytest

from app.database import async_session
from app.models.inspiration import Inspiration
from app.services import quality_learner as ql
from app.services.vector import store as vector_store
from app.utils.time import utcnow


class _FakeModel:
    """可被 joblib 序列化的假分类器：predict_proba 返回固定概率。"""

    def __init__(self, garbage_proba: float = 0.5, raises: bool = False) -> None:
        self.garbage_proba = garbage_proba
        self.raises = raises

    def predict_proba(self, _vectors):
        if self.raises:
            raise RuntimeError("predict boom")
        return [[self.garbage_proba, 1.0 - self.garbage_proba]]


@pytest.fixture
def model_paths(monkeypatch, tmp_path):
    """把模型/元数据路径指到 tmp，并清空进程内模型缓存（结束恢复）。"""
    model_dir = tmp_path / "quality_classifier"
    monkeypatch.setattr(ql, "_MODEL_DIR", model_dir)
    monkeypatch.setattr(ql, "_MODEL_PATH", model_dir / "classifier.joblib")
    monkeypatch.setattr(ql, "_META_PATH", model_dir / "meta.json")
    monkeypatch.setattr(ql, "_model_cache", {"mtime": None, "model": None})
    ql._drop_model_cache()
    yield model_dir
    ql._drop_model_cache()


async def _insp(**overrides) -> Inspiration:
    data = {
        "file_path": "images/learner.jpg",
        "media_type": "image",
        "quality_status": "pending",
    }
    data.update(overrides)
    async with async_session() as db:
        insp = Inspiration(**data)
        db.add(insp)
        await db.commit()
        return insp


def _vectors(*seeds: str) -> dict[str, list[float]]:
    """给一批 id 造确定性 4 维向量。"""
    return {seed: [float(i + 1), 1.0, 0.0, 0.0] for i, seed in enumerate(seeds)}


# ═══════════════════════════════════════════════════════════════
#  _train_sync：两类 error / 指标 / 落盘
# ═══════════════════════════════════════════════════════════════


def test_train_sync_single_class_error(model_paths):
    result = ql._train_sync([[1.0], [2.0]], [1, 1])

    assert result == {"error": "正负样本至少各需 1 条（当前只有单类样本，无法训练分类器）"}
    assert not model_paths.exists()


def test_train_sync_too_few_samples_error(model_paths):
    result = ql._train_sync([[1.0], [2.0]], [1, 0])

    assert "样本量过少" in result["error"]
    assert "2 条" in result["error"]


def test_train_sync_writes_model_and_meta(model_paths):
    """12 条均衡样本：产出 accuracy/precision/recall/f1/混淆矩阵并原子落盘。"""
    X = [[2.0, 2.0], [2.1, 1.9], [1.9, 2.2], [2.2, 2.1], [2.0, 2.05], [1.8, 2.0],
         [-2.0, -2.0], [-2.1, -1.9], [-1.9, -2.2], [-2.2, -2.1], [-2.0, -2.05], [-1.8, -2.0]]
    y = [1] * 6 + [0] * 6

    meta = ql._train_sync(X, y)

    assert meta["sample_total"] == 12
    assert meta["positive"] == 6 and meta["negative"] == 6
    assert meta["dim"] > 0
    assert "trained_at" in meta and meta["trained_at"].endswith("Z")
    metrics = meta["metrics"]
    assert set(metrics) >= {
        "accuracy",
        "precision",
        "recall",
        "f1",
        "false_reject_rate",
        "test_size",
        "confusion",
    }
    assert 0.0 <= metrics["accuracy"] <= 1.0
    assert set(metrics["confusion"]) == {"tn", "fp", "fn", "tp"}
    # 模型与元数据都落盘；元数据可解析且与返回值一致
    assert (model_paths / "classifier.joblib").exists()
    saved = json.loads((model_paths / "meta.json").read_text(encoding="utf-8"))
    assert saved["sample_total"] == 12
    assert ql._model_cache["model"] is None  # 训练后清空缓存


def test_train_sync_unbalanced_fallback_without_stratify(model_paths):
    """正负极不均衡（1:10）时降级为非分层划分，仍能完成并返回指标或单类说明。"""
    X = [[2.0 + i * 0.01, 2.0] for i in range(10)] + [[-2.0, -2.0]]
    y = [1] * 10 + [0]

    meta = ql._train_sync(X, y)

    assert "error" not in meta
    assert meta["sample_total"] == 11
    assert "metrics" in meta
    metrics = meta["metrics"]
    assert "note" in metrics or "accuracy" in metrics


# ═══════════════════════════════════════════════════════════════
#  模型加载 / 预测
# ═══════════════════════════════════════════════════════════════


def test_load_model_missing_and_mtime_cache(model_paths):
    assert ql._load_model() is None  # 未训练

    import joblib

    model_paths.mkdir(parents=True, exist_ok=True)
    joblib.dump(_FakeModel(), ql._MODEL_PATH)
    first = ql._load_model()
    assert first is not None
    assert ql._load_model() is first  # mtime 未变 → 命中进程缓存

    os.utime(ql._MODEL_PATH, (0, 0))  # 文件被重训替换（mtime 变化）
    second = ql._load_model()
    assert second is not None and second is not first


def test_predict_sync_threshold_and_errors(model_paths, monkeypatch):
    from app.config import settings

    assert ql._predict_sync([0.1]) is None  # 无模型

    monkeypatch.setattr(ql, "_load_model", lambda: _FakeModel(garbage_proba=0.9))
    monkeypatch.setattr(settings, "quality_classifier_threshold", 0.5)
    assert ql._predict_sync([0.1]) == (True, 0.9)

    monkeypatch.setattr(settings, "quality_classifier_threshold", 0.95)
    assert ql._predict_sync([0.1]) == (False, 0.9)

    monkeypatch.setattr(ql, "_load_model", lambda: _FakeModel(raises=True))
    assert ql._predict_sync([0.1]) is None  # 预测异常 → 降级为 None（走 VLM 复审）


async def test_predict_vector_delegates(model_paths, monkeypatch):
    monkeypatch.setattr(ql, "_load_model", lambda: _FakeModel(garbage_proba=0.3))
    result = await ql.predict_vector([0.0, 1.0])
    assert result == (False, 0.3)


def test_get_status_corrupted_meta(model_paths):
    assert ql.get_status()["trained"] is False
    assert ql.get_status()["meta"] is None

    model_paths.mkdir(parents=True, exist_ok=True)
    ql._MODEL_PATH.write_bytes(b"fake")
    ql._META_PATH.write_text("not-json", encoding="utf-8")
    status = ql.get_status()
    assert status["trained"] is True
    assert status["meta"] is None  # 坏元数据不 500
    assert "threshold" in status

    ql._META_PATH.write_text('{"sample_total": 3}', encoding="utf-8")
    assert ql.get_status()["meta"] == {"sample_total": 3}


def test_reset_swallows_unlink_errors(model_paths, monkeypatch):
    model_paths.mkdir(parents=True, exist_ok=True)
    ql._MODEL_PATH.write_bytes(b"fake")
    ql._META_PATH.write_text("{}", encoding="utf-8")

    real_unlink = Path.unlink

    def _flaky(self, *args, **kwargs):
        if self == ql._MODEL_PATH:
            raise OSError("文件被占用")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _flaky)
    result = ql.reset()

    assert result["reset"] is True
    assert result["removed"] == ["meta.json"]  # 模型删不掉也不抛异常
    assert ql._MODEL_PATH.exists()


# ═══════════════════════════════════════════════════════════════
#  样本收集 / train 入口
# ═══════════════════════════════════════════════════════════════


async def test_collect_samples_excludes_privacy_and_ai_generated(client, monkeypatch):
    """负样本排除「隐私」与 AI 生成图；按原因分层统计；无向量样本跳过。"""
    pos1 = await _insp(quality_status="approved")
    pos2 = await _insp(quality_status="approved")
    await _insp(quality_status="rejected")  # 未删除的 rejected 不是负样本
    neg_quality = await _insp(
        deleted_at=utcnow(), trash_reason="质量差", trash_source="manual"
    )
    await _insp(deleted_at=utcnow(), trash_reason="重复", trash_source="auto")
    await _insp(deleted_at=utcnow(), trash_reason="隐私", trash_source="manual")
    await _insp(
        deleted_at=utcnow(),
        trash_reason="质量差",
        trash_source="manual",
        is_ai_generated=True,
    )
    await _insp(
        deleted_at=utcnow(), trash_reason="质量差", trash_source="manual", media_type="video"
    )
    vectors = _vectors(pos1.id, pos2.id, neg_quality.id)  # neg_dup 无向量

    async def _fake_get_vectors(_kind, ids):
        return {i: vectors[i] for i in ids if i in vectors}

    monkeypatch.setattr(vector_store, "get_vectors_batch", _fake_get_vectors)

    async with async_session() as db:
        X, y, stats = await ql.collect_samples(db)

    assert stats["positive_total"] == 2
    assert stats["negative_total"] == 2  # 只算「质量差」+「重复」两条
    assert stats["negative_by_reason"] == {"质量差": 1, "重复": 1}
    assert stats["excluded_reasons"] == ["隐私"]
    assert stats["with_vector"] == 3
    assert stats["positive_with_vector"] == 2
    assert stats["negative_with_vector"] == 1
    assert X == [vectors[pos1.id], vectors[pos2.id], vectors[neg_quality.id]]
    assert y == [1, 1, 0]


async def test_train_reports_insufficient_samples(client, monkeypatch):
    """正负各 1 条（<10）：返回「样本不足」提示，不落盘。"""
    pos = await _insp(quality_status="approved")
    neg = await _insp(deleted_at=utcnow(), trash_reason="不喜欢", trash_source="manual")
    vectors = _vectors(pos.id, neg.id)

    async def _fake_get_vectors(_kind, ids):
        return {i: vectors[i] for i in ids}

    monkeypatch.setattr(vector_store, "is_lancedb_available", lambda: True)
    monkeypatch.setattr(vector_store, "get_vectors_batch", _fake_get_vectors)

    async with async_session() as db:
        meta = await ql.train(db)

    assert "样本不足" in meta["error"]
    assert "正 1 / 负 1" in meta["error"]
    assert ql.get_status()["trained"] is False
