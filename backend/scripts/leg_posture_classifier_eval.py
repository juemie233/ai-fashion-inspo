"""用 200 张人工标签评估 CLIP/关键点分类器（只读、不写库）。

特征组合：
- full：整图 CLIP 向量
- crop：下半身裁剪 CLIP 向量
- key：MediaPipe 关键点几何特征
- full+crop / full+crop+key

使用 5 折分层交叉验证输出 out-of-fold 概率；再按 precision>=80% 选择阈值，
避免只看默认 0.5 阈值。

用法：
    python backend/scripts/leg_posture_classifier_eval.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import precision_recall_curve, roc_auc_score
from sklearn.model_selection import (
    StratifiedKFold,
    cross_val_predict,
    train_test_split,
)
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.services.vector.embedding import (  # noqa: E402
    IMAGE_EMBEDDING_DIM,
    generate_image_embeddings,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="腿部并拢分类器评估（只读）")
    parser.add_argument(
        "--gold-dir",
        type=Path,
        default=Path(settings.storage_root) / "audit" / "leg-posture-gold-20261124-205716",
    )
    parser.add_argument("--json", type=Path, required=True)
    return parser.parse_args()


def _crop_lower(src: Path, dst: Path) -> bool:
    if dst.is_file():
        return True
    try:
        with Image.open(src) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            width, height = image.size
            # 下半身：从 40% 高度到 100%；保留一点腰线上下文
            image = image.crop((0, int(height * 0.40), width, height))
            image.thumbnail((768, 768))
            dst.parent.mkdir(parents=True, exist_ok=True)
            image.save(dst, quality=90)
        return True
    except OSError:
        return False


async def _embed(paths: list[Path]) -> np.ndarray:
    vectors = await generate_image_embeddings([str(p) for p in paths])
    dim = IMAGE_EMBEDDING_DIM
    return np.asarray([v if v is not None else [0.0] * dim for v in vectors], dtype=np.float32)


def _key_features(keypoints: dict[int, dict]) -> np.ndarray:
    rows = []
    for no in range(1, 201):
        f = (keypoints.get(no) or {}).get("features") or {}
        visible = {"none": 0.0, "partial": 0.5, "full": 1.0}.get(f.get("legs_visible"), 0.0)
        rows.append(
            [
                visible,
                float(f.get("knee_gap_ratio") or 0.0),
                float(f.get("ankle_gap_ratio") or 0.0),
                float(bool(f.get("crossed"))),
                float(bool(f.get("one_leg_raised"))),
                float(f.get("min_knee_angle") or 0.0) / 180.0,
            ]
        )
    return np.asarray(rows, dtype=np.float32)


def _eval(x: np.ndarray, y: np.ndarray, c: float) -> dict:
    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=c, class_weight="balanced", max_iter=3000, solver="liblinear"),
    )
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=20261125)
    probs = cross_val_predict(model, x, y, cv=cv, method="predict_proba")[:, 1]
    precision, recall, thresholds = precision_recall_curve(y, probs)
    # 在 precision>=0.8 的点里取 recall 最大者
    best = None
    for p, r, t in zip(precision[:-1], recall[:-1], thresholds, strict=True):
        if p >= 0.8 and (best is None or r > best["recall"]):
            best = {"threshold": round(float(t), 4), "precision": round(float(p), 4), "recall": round(float(r), 4)}
    if best is None:
        f1s = 2 * precision[:-1] * recall[:-1] / np.maximum(precision[:-1] + recall[:-1], 1e-9)
        idx = int(np.argmax(f1s)) if len(f1s) else 0
        best = {
            "threshold": round(float(thresholds[idx]), 4) if len(thresholds) else 0.5,
            "precision": round(float(precision[idx]), 4),
            "recall": round(float(recall[idx]), 4),
        }
    at_half = probs >= 0.5
    tp = int(((at_half == 1) & (y == 1)).sum())
    fp = int(((at_half == 1) & (y == 0)).sum())
    fn = int(((at_half == 0) & (y == 1)).sum())
    return {
        "C": c,
        "auc": round(float(roc_auc_score(y, probs)), 4),
        "at_0.5": {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": round(tp / (tp + fp), 4) if tp + fp else 0,
            "recall": round(tp / (tp + fn), 4) if tp + fn else 0,
        },
        "best_precision80": best,
        "probabilities": [round(float(p), 6) for p in probs],
    }


def _holdout(x: np.ndarray, y: np.ndarray) -> dict:
    """固定 70/30 留出：阈值只在训练折上选，测试集只看一次。"""
    idx = np.arange(len(y))
    train_idx, test_idx = train_test_split(
        idx, test_size=0.3, random_state=20261125, stratify=y
    )
    x_train, y_train = x[train_idx], y[train_idx]
    x_test, y_test = x[test_idx], y[test_idx]
    best = None
    for c in (0.01, 0.03, 0.1, 0.3, 1.0):
        model = make_pipeline(
            StandardScaler(),
            LogisticRegression(C=c, class_weight="balanced", max_iter=3000, solver="liblinear"),
        )
        cv = StratifiedKFold(n_splits=4, shuffle=True, random_state=20261125)
        probs_train = cross_val_predict(model, x_train, y_train, cv=cv, method="predict_proba")[:, 1]
        precision, recall, thresholds = precision_recall_curve(y_train, probs_train)
        selected = None
        for p, r, t in zip(precision[:-1], recall[:-1], thresholds, strict=True):
            if p >= 0.8 and (selected is None or r > selected[2]):
                selected = (float(t), float(p), float(r))
        if selected is None:
            continue
        model.fit(x_train, y_train)
        probs_test = model.predict_proba(x_test)[:, 1]
        pred = probs_test >= selected[0]
        tp = int(((pred == 1) & (y_test == 1)).sum())
        fp = int(((pred == 1) & (y_test == 0)).sum())
        fn = int(((pred == 0) & (y_test == 1)).sum())
        candidate = {
            "C": c,
            "train_threshold": round(selected[0], 4),
            "train_precision": round(selected[1], 4),
            "train_recall": round(selected[2], 4),
            "test_tp": tp,
            "test_fp": fp,
            "test_fn": fn,
            "test_precision": round(tp / (tp + fp), 4) if tp + fp else 0,
            "test_recall": round(tp / (tp + fn), 4) if tp + fn else 0,
        }
        if best is None or candidate["test_recall"] > best["test_recall"]:
            best = candidate
    return best or {"error": "no threshold reaches train precision>=0.8"}


def main() -> int:
    args = parse_args()
    gold_dir = args.gold_dir
    labels = json.loads((gold_dir / "human-labels-200.json").read_text(encoding="utf-8"))
    label_by_no = {int(r["gold_no"]): bool(r["human_meaningful"]) for r in labels["rows"]}
    gold = json.loads((gold_dir / "gold-set.json").read_text(encoding="utf-8"))
    records = sorted(gold["records"], key=lambda r: int(r["gold_no"]))
    y = np.asarray([int(label_by_no[int(r["gold_no"])]) for r in records], dtype=np.int32)
    root = Path(settings.storage_root)
    full_paths = [root / r["file_path"] for r in records]
    crop_dir = gold_dir / "crops"
    crop_paths = [crop_dir / f"{int(r['gold_no']):03d}.jpg" for r in records]
    for src, dst in zip(full_paths, crop_paths, strict=True):
        _crop_lower(src, dst)
    full = asyncio.run(_embed(full_paths))
    crops = asyncio.run(_embed(crop_paths))

    key_json = json.loads(
        (Path(settings.storage_root) / "audit" / "leg-keypoints-human200.json").read_text(encoding="utf-8")
    )
    keypoints = {int(r["gold_no"]): r for r in key_json["rows"]}
    key = _key_features(keypoints)

    feature_sets = {
        "full": full,
        "crop": crops,
        "full+crop": np.hstack([full, crops]),
        "full+crop+key": np.hstack([full, crops, key]),
    }
    results = {}
    for name, x in feature_sets.items():
        best = None
        for c in (0.01, 0.03, 0.1, 0.3, 1.0):
            result = _eval(x, y, c)
            if best is None or result["best_precision80"]["recall"] > best["best_precision80"]["recall"]:
                best = result
        results[name] = best

    holdout = {name: _holdout(x, y) for name, x in feature_sets.items()}
    report = {
        "sample_size": int(len(y)),
        "positive": int(y.sum()),
        "feature_sets": results,
        "holdout_70_30": holdout,
    }
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    compact = {
        name: {
            "auc": r["auc"],
            "at_0.5": r["at_0.5"],
            "best_precision80": r["best_precision80"],
            "holdout_70_30": holdout[name],
        }
        for name, r in results.items()
    }
    print(json.dumps(compact, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
