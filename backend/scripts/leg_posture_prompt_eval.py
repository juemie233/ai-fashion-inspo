"""在人工 pilot 金标准上评估两版腿部 Prompt（只读、不写库）。

用法：
    python backend/scripts/leg_posture_prompt_eval.py \
        --candidate-file G:/fashion-inspo-storage/audit/prompt-v3-candidate.txt \
        --json G:/fashion-inspo-storage/audit/prompt-v3-pilot.json
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.services.ai_parser import parse_analysis_response  # noqa: E402

MODEL = "qwen3-vl:8b-instruct"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="人工金标准 Prompt 评估（只读）")
    parser.add_argument("--candidate-file", type=Path, required=True)
    parser.add_argument(
        "--gold-dir",
        type=Path,
        default=Path(settings.storage_root) / "audit" / "leg-posture-gold-20261124-205716",
    )
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=40)
    return parser.parse_args()


def _b64(path: Path) -> str:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((1024, 1024))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=88, optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _call(prompt: str, path: Path) -> dict:
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "images": [_b64(path)],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0, "num_predict": 500, "num_ctx": 16384},
    }
    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=240) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return parse_analysis_response(body.get("response") or "") or {}


def _has_together(data: dict) -> bool:
    values = data.get("leg_posture") or data.get("Leg_Posture") or []
    if not isinstance(values, list):
        values = [values]
    return any(str(v).strip() == "双腿并拢" for v in values)


def _metrics(rows: list[dict], key: str) -> dict:
    tp = fp = fn = tn = 0
    for row in rows:
        predicted = row[key]
        truth = row["truth"]
        if predicted and truth:
            tp += 1
        elif predicted and not truth:
            fp += 1
        elif not predicted and truth:
            fn += 1
        else:
            tn += 1
    return {
        "positive": tp + fp,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": round(tp / (tp + fp), 4) if tp + fp else 0,
        "recall": round(tp / (tp + fn), 4) if tp + fn else 0,
    }


def main() -> int:
    args = parse_args()
    gold = json.loads((args.gold_dir / "gold-set.json").read_text(encoding="utf-8"))
    labels = json.loads((args.gold_dir / "pilot-labels-40.json").read_text(encoding="utf-8"))
    label_by_no = {row["gold_no"]: row for row in labels["rows"]}
    records = [
        row
        for row in gold["records"]
        if row["gold_no"] in label_by_no
    ][: args.limit]
    current_prompt = json.loads(
        (_BACKEND_DIR / "prompt_configs.json").read_text(encoding="utf-8")
    )[MODEL]
    candidate = args.candidate_file.read_text(encoding="utf-8")

    rows = []
    for index, record in enumerate(records, start=1):
        path = Path(settings.storage_root) / (record.get("thumbnail_path") or record["file_path"])
        if not path.is_file():
            path = Path(settings.storage_root) / record["file_path"]
        if not path.is_file():
            continue
        label = label_by_no[record["gold_no"]]
        try:
            current_data = _call(current_prompt, path)
            candidate_data = _call(candidate, path)
            rows.append(
                {
                    "gold_no": record["gold_no"],
                    "gold_id": record["gold_id"],
                    "truth": bool(label["human_meaningful"]),
                    "current": _has_together(current_data),
                    "candidate": _has_together(candidate_data),
                    "current_posture": current_data.get("leg_posture") or current_data.get("Leg_Posture"),
                    "candidate_posture": candidate_data.get("leg_posture") or candidate_data.get("Leg_Posture"),
                }
            )
        except Exception as exc:
            print(f"  {record['gold_id']} 调用失败：{exc}")
        if index % 10 == 0:
            print(f"  {index}/{len(records)}")

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "model": MODEL,
        "sample_size": len(rows),
        "gold_positive": sum(row["truth"] for row in rows),
        "current_prompt": _metrics(rows, "current"),
        "candidate_prompt": _metrics(rows, "candidate"),
        "rows": rows,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
