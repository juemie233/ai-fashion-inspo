"""在人工 pilot 金标准上评估专用“双腿并拢”二分类 Prompt（只读）。

用法：
    python backend/scripts/leg_posture_binary_eval.py \
        --prompt-file G:/fashion-inspo-storage/audit/prompt-leg-together-binary.txt \
        --json G:/fashion-inspo-storage/audit/leg-together-binary-pilot.json
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
    parser = argparse.ArgumentParser(description="双腿并拢二分类评估（只读）")
    parser.add_argument("--prompt-file", type=Path, required=True)
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
        "options": {"temperature": 0, "num_predict": 120, "num_ctx": 8192},
    }
    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return parse_analysis_response(body.get("response") or "") or {}


def main() -> int:
    args = parse_args()
    gold = json.loads((args.gold_dir / "gold-set.json").read_text(encoding="utf-8"))
    labels = json.loads((args.gold_dir / "pilot-labels-40.json").read_text(encoding="utf-8"))
    label_by_no = {row["gold_no"]: row for row in labels["rows"]}
    records = [r for r in gold["records"] if r["gold_no"] in label_by_no][: args.limit]
    prompt = args.prompt_file.read_text(encoding="utf-8")
    rows = []
    for index, record in enumerate(records, start=1):
        path = Path(settings.storage_root) / (record.get("thumbnail_path") or record["file_path"])
        if not path.is_file():
            path = Path(settings.storage_root) / record["file_path"]
        if not path.is_file():
            continue
        label = label_by_no[record["gold_no"]]
        try:
            data = _call(prompt, path)
            visible = data.get("legs_visible")
            predicted = (
                visible == "full"
                and data.get("legs_together") is True
                and data.get("is_default_standing") is not True
            )
            rows.append(
                {
                    "gold_no": record["gold_no"],
                    "gold_id": record["gold_id"],
                    "truth": bool(label["human_meaningful"]),
                    "predicted": predicted,
                    "raw": data,
                }
            )
        except Exception as exc:
            print(f"  {record['gold_id']} 调用失败：{exc}")
        if index % 10 == 0:
            print(f"  {index}/{len(records)}")

    tp = sum(1 for r in rows if r["predicted"] and r["truth"])
    fp = sum(1 for r in rows if r["predicted"] and not r["truth"])
    fn = sum(1 for r in rows if not r["predicted"] and r["truth"])
    tn = sum(1 for r in rows if not r["predicted"] and not r["truth"])
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sample_size": len(rows),
        "gold_positive": sum(r["truth"] for r in rows),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": round(tp / (tp + fp), 4) if tp + fp else 0,
        "recall": round(tp / (tp + fn), 4) if tp + fn else 0,
        "rows": rows,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
