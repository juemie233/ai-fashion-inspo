"""用 CLIP 图像向量 + 人工 pilot 标签做小样本线性探针（只读）。

目的：验证“人工标签 + 图像 embedding + 小分类器”是否比 VLM/几何规则更可行。
不做任何生产写入，只输出 leave-one-out 指标。

用法：
    python backend/scripts/leg_posture_clip_probe.py \
        --gold-dir G:/fashion-inspo-storage/audit/leg-posture-gold-20261124-205716 \
        --json G:/fashion-inspo-storage/audit/leg-clip-probe-pilot.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import precision_score, recall_score
from sklearn.preprocessing import StandardScaler

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.services.vector.embedding import generate_image_embeddings  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CLIP 小样本线性探针（只读）")
    parser.add_argument(
        "--gold-dir",
        type=Path,
        default=Path(settings.storage_root) / "audit" / "leg-posture-gold-20261124-205716",
    )
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=40)
    return parser.parse_args()


async def _embed(records: list[dict]) -> tuple[list[dict], np.ndarray]:
    paths = []
    kept = []
    for record in records:
        path = Path(settings.storage_root) / record["file_path"]
        if not path.is_file():
            continue
        kept.append(record)
        paths.append(str(path))
    embeddings = await generate_image_embeddings(paths)
    rows = []
    vectors = []
    for record, vector in zip(kept, embeddings, strict=True):
        if vector is None:
            continue
        rows.append(record)
        vectors.append(vector)
    return rows, np.asarray(vectors, dtype=np.float32)


def _fit_predict(x_train, y_train, x_test) -> int:
    scaler = StandardScaler()
    xt = scaler.fit_transform(x_train)
    xtest = scaler.transform(x_test)
    model = LogisticRegression(
        C=0.05,
        class_weight="balanced",
        max_iter=2000,
        solver="liblinear",
        random_state=20261124,
    )
    model.fit(xt, y_train)
    return int(model.predict(xtest)[0])


def main() -> int:
    args = parse_args()
    gold = json.loads((args.gold_dir / "gold-set.json").read_text(encoding="utf-8"))
    labels = json.loads((args.gold_dir / "pilot-labels-40.json").read_text(encoding="utf-8"))
    label_by_no = {row["gold_no"]: row for row in labels["rows"]}
    records = [r for r in gold["records"] if r["gold_no"] in label_by_no][: args.limit]
    rows, x = asyncio.run(_embed(records))
    y = np.array([int(label_by_no[row["gold_no"]]["human_meaningful"]) for row in rows])

    if len(rows) < 10 or y.sum() == 0:
        print("样本不足")
        return 2

    preds = []
    for index in range(len(rows)):
        mask = np.arange(len(rows)) != index
        preds.append(_fit_predict(x[mask], y[mask], x[index:index + 1]))
    preds = np.array(preds)
    report = {
        "sample_size": len(rows),
        "positive": int(y.sum()),
        "tp": int(((preds == 1) & (y == 1)).sum()),
        "fp": int(((preds == 1) & (y == 0)).sum()),
        "fn": int(((preds == 0) & (y == 1)).sum()),
        "tn": int(((preds == 0) & (y == 0)).sum()),
        "precision": round(float(precision_score(y, preds, zero_division=0)), 4),
        "recall": round(float(recall_score(y, preds, zero_division=0)), 4),
        "rows": [
            {"gold_no": row["gold_no"], "gold_id": row["gold_id"], "truth": int(y[i]), "pred": int(preds[i])}
            for i, row in enumerate(rows)
        ],
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
