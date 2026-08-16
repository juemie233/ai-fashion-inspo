"""负样本初筛器：用 CLIP 图像向量训练轻量分类器，做质量审核前置初筛。

方案阶段 0（热身验证）与阶段 2（初筛器）的核心模块：

- 正样本：``quality_status=approved`` 且未删除的图片素材
- 负样本：``quality_status=rejected``（未删除）或 ``trash_reason=质量差``（已删除）的图片素材
- 输入：LanceDB 中已存储的 512 维 CLIP 图像向量（垃圾桶素材向量保留，天然可复用）
- 模型：sklearn 逻辑回归（无需 GPU，秒级训练）
- 落盘：``storage/quality_classifier/``（joblib 序列化模型 + JSON 元数据）

「宁缺毋滥」哲学：分类器只做前置初筛，高置信度垃圾直接拒绝，低置信度仍走 VLM 复审；
阈值通过 ``settings.quality_classifier_threshold`` 调整，人工翻案机制原样保留。
若指标变差，可删除落盘模型（reset）回滚到纯 VLM 审核。
"""

import asyncio
import json
import logging
import os
import threading
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.inspiration import Inspiration
from app.services.vector import store as vector_store

logger = logging.getLogger(__name__)

_MODEL_DIR = settings.storage_root / "quality_classifier"
_MODEL_PATH = _MODEL_DIR / "classifier.joblib"
_META_PATH = _MODEL_DIR / "meta.json"

# 负样本删除原因（与 inspiration_service.TRASH_REASONS 中的「质量差」保持一致）
_NEGATIVE_TRASH_REASON = "质量差"

# 模型缓存（跨进程重训后按文件 mtime 失效重载；预测在 to_thread 线程池运行，加锁保护）
_model_cache: dict = {"mtime": None, "model": None}
_model_lock = threading.Lock()


def _utcnow() -> datetime:
    """返回当前 UTC 时间（naive datetime）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def collect_samples(db: AsyncSession) -> tuple[list[list[float]], list[int], dict]:
    """收集正负样本及其 CLIP 图像向量，返回 (X, y, 统计)。

    正样本 label=1（合格），负样本 label=0（垃圾）。只取已在 LanceDB 中存有
    图像向量的样本（无向量样本跳过，训练不现场编码 CLIP 以免阻塞）。

    策略说明：正样本不排除 ``is_ai_generated=True`` 的素材——项目对 AI 生成图
    采取「只标记不拒绝」策略，将其纳入正样本与策略一致（初筛器不会因此把
    AI 生成图学成垃圾）。若未来策略改为拒绝 AI 生成图，需在此同步过滤。
    """
    positive_ids = (
        await db.execute(
            select(Inspiration.id).where(
                Inspiration.quality_status == "approved",
                Inspiration.deleted_at.is_(None),
                Inspiration.media_type == "image",
            )
        )
    ).scalars().all()

    negative_ids = (
        await db.execute(
            select(Inspiration.id).where(
                Inspiration.media_type == "image",
                (
                    (Inspiration.quality_status == "rejected")
                    & (Inspiration.deleted_at.is_(None))
                )
                | (
                    (Inspiration.trash_reason == _NEGATIVE_TRASH_REASON)
                    & (Inspiration.deleted_at.isnot(None))
                ),
            )
        )
    ).scalars().all()

    # 批量读取已存储的图像向量（一次表加载），避免逐条 get_vector 造成 O(N²) 扫描
    pos_map = await vector_store.get_vectors_batch("image", list(positive_ids))
    neg_map = await vector_store.get_vectors_batch("image", list(negative_ids))

    X: list[list[float]] = []
    y: list[int] = []
    for insp_id in positive_ids:
        if insp_id in pos_map:
            X.append(pos_map[insp_id])
            y.append(1)
    for insp_id in negative_ids:
        if insp_id in neg_map:
            X.append(neg_map[insp_id])
            y.append(0)

    stats = {
        "positive_total": len(positive_ids),
        "negative_total": len(negative_ids),
        "with_vector": len(X),
        "positive_with_vector": int(sum(y)),
        "negative_with_vector": int(len(y) - sum(y)),
    }
    return X, y, stats


def _train_sync(X: list[list[float]], y: list[int]) -> dict:
    """同步训练并评估（放入线程池），落盘模型与元数据，返回元数据字典。"""
    import joblib
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import (
        accuracy_score,
        confusion_matrix,
        f1_score,
        precision_score,
        recall_score,
    )
    from sklearn.model_selection import train_test_split
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    if len(set(y)) < 2:
        return {"error": "正负样本至少各需 1 条（当前只有单类样本，无法训练分类器）"}
    if len(X) < 10:
        return {"error": f"样本量过少（{len(X)} 条），不足以训练有意义的分类器，请先积累样本"}

    # stratify 保证训练/验证集正负比例一致（样本不均衡时尤为重要）
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.25, random_state=42, stratify=y
    )

    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(max_iter=2000, class_weight="balanced"),
    )
    model.fit(X_train, y_train)
    pred = model.predict(X_test)

    # label=1 为「合格」，label=0 为「垃圾」；误杀率 = 合格被判为垃圾的比例
    tn, fp, fn, tp = confusion_matrix(y_test, pred).ravel()
    good_total = tp + fn
    false_reject_rate = round(fn / good_total, 4) if good_total else 0.0

    metrics = {
        "accuracy": round(float(accuracy_score(y_test, pred)), 4),
        "precision": round(float(precision_score(y_test, pred, zero_division=0)), 4),
        "recall": round(float(recall_score(y_test, pred, zero_division=0)), 4),
        "f1": round(float(f1_score(y_test, pred, zero_division=0)), 4),
        "false_reject_rate": false_reject_rate,
        "test_size": int(len(y_test)),
        "confusion": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
    }

    _MODEL_DIR.mkdir(parents=True, exist_ok=True)
    # 先写临时文件再原子替换：进程中途被杀也不会留下损坏的模型文件
    tmp_path = _MODEL_PATH.with_suffix(".joblib.tmp")
    joblib.dump(model, tmp_path)
    os.replace(tmp_path, _MODEL_PATH)

    meta = {
        "trained_at": _utcnow().isoformat(),
        "sample_total": len(X),
        "positive": int(sum(y)),
        "negative": int(len(y) - sum(y)),
        "dim": settings.lancedb_image_dim,
        "threshold": settings.quality_classifier_threshold,
        "metrics": metrics,
    }
    _META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    # 训练后清空进程内缓存，保证本进程下次预测加载最新模型
    _drop_model_cache()
    return meta


async def train(db: AsyncSession) -> dict:
    """构建数据集并训练分类器，返回元数据（含指标）。"""
    if not vector_store.is_lancedb_available():
        return {"error": "lancedb 未安装，请先执行：pip install lancedb"}

    X, y, stats = await collect_samples(db)
    if len(set(y)) < 2 or len(X) < 10:
        return {"error": f"样本不足：共 {len(X)} 条含向量样本（正 {stats['positive_with_vector']} / 负 {stats['negative_with_vector']}），无法训练"}

    meta = await asyncio.to_thread(_train_sync, X, y)
    meta["dataset"] = stats
    return meta


def _drop_model_cache() -> None:
    """清空进程内模型缓存（训练 / 重置后调用）。"""
    global _model_cache
    with _model_lock:
        _model_cache["mtime"] = None
        _model_cache["model"] = None


def _load_model():
    """懒加载模型（按文件 mtime 失效重载，跨进程重训后自动生效，线程安全）。"""
    global _model_cache
    if not _MODEL_PATH.exists():
        return None
    mtime = _MODEL_PATH.stat().st_mtime
    with _model_lock:
        if _model_cache["model"] is not None and _model_cache["mtime"] == mtime:
            return _model_cache["model"]
        import joblib

        model = joblib.load(_MODEL_PATH)
        _model_cache["model"] = model
        _model_cache["mtime"] = mtime
        return model


def _predict_sync(vector: list[float]) -> tuple[bool, float] | None:
    """同步预测单条图像向量，返回 (是否垃圾, 垃圾置信度)；模型未训练返回 None。"""
    model = _load_model()
    if model is None:
        return None
    try:
        proba_good = float(model.predict_proba([vector])[0][1])
    except Exception as e:
        logger.warning(f"初筛器预测失败: {e}")
        return None
    proba_garbage = 1.0 - proba_good
    return proba_garbage >= settings.quality_classifier_threshold, round(proba_garbage, 4)


async def predict_vector(vector: list[float]) -> tuple[bool, float] | None:
    """对图像向量做负样本初筛，返回 (是否垃圾, 垃圾置信度)；未训练时返回 None。"""
    return await asyncio.to_thread(_predict_sync, vector)


def get_status() -> dict:
    """返回初筛器状态（是否已训练、指标、样本量、阈值）。"""
    trained = _MODEL_PATH.exists()
    meta: dict | None = None
    if _META_PATH.exists():
        try:
            meta = json.loads(_META_PATH.read_text(encoding="utf-8"))
        except Exception:
            meta = None
    return {
        "trained": trained,
        "model_path": str(_MODEL_PATH),
        "threshold": settings.quality_classifier_threshold,
        "meta": meta,
    }


def reset() -> dict:
    """删除已训练的模型与元数据，回滚到纯 VLM 审核（指标变差时使用）。"""
    _drop_model_cache()
    removed = []
    for p in (_MODEL_PATH, _META_PATH):
        try:
            if p.exists():
                p.unlink()
                removed.append(p.name)
        except Exception:
            pass
    return {"reset": bool(removed), "removed": removed}
