"""腿部姿态 Prompt A/B（离线、只读、不写库）。

对审计样本中的图片分别用旧 Prompt（prompt_versions.json 最后版本）和新 Prompt
（当前 prompt_configs.json）调用本地视觉模型，比较腿部标签产出与审计初筛真值。

用法::

    python backend/scripts/leg_posture_ab.py --sample 60
    python backend/scripts/leg_posture_ab.py --sample 60 --json out.json
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import sys
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.services.ai_parser import parse_analysis_response  # noqa: E402

MODEL = "qwen3-vl:8b-instruct"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="腿部姿态 Prompt A/B（只读）")
    parser.add_argument("--sample", type=int, default=60, help="抽样数量")
    parser.add_argument("--seed", type=int, default=20261124)
    parser.add_argument("--audit-dir", type=Path, default=None, help="审计输出目录")
    parser.add_argument("--json", type=Path, help="输出报告路径")
    return parser.parse_args()


def _image_b64(path: Path) -> str:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((1024, 1024))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=88, optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _call(prompt: str, image_path: Path) -> dict:
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "images": [_image_b64(image_path)],
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0,
            "num_predict": 500,
            # 与生产 ai_num_ctx 对齐；默认 4096 会因新 Prompt + 视觉 token 超窗
            "num_ctx": 16384,
        },
    }
    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=240) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    raw = body.get("response") or ""
    return parse_analysis_response(raw) or {}


def _leg_names(data: dict) -> list[str]:
    values = data.get("leg_posture") or data.get("Leg_Posture") or []
    if not isinstance(values, list):
        values = [values]
    return [str(v) for v in values]


def _truth(row: dict) -> bool:
    audit = row.get("audit") or {}
    return (
        audit.get("legs_visible") == "full"
        and audit.get("leg_relation") == "together"
        and audit.get("is_default_standing") is False
    )


def _metrics(rows: list[dict], side: str, label: str) -> dict:
    tp = fp = fn = tn = 0
    positives = 0
    for row in rows:
        predicted = label in row[f"{side}_leg"]
        truth = _truth(row)
        if predicted:
            positives += 1
        if predicted and truth:
            tp += 1
        elif predicted and not truth:
            fp += 1
        elif not predicted and truth:
            fn += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if tp + fp else 0
    recall = tp / (tp + fn) if tp + fn else 0
    return {
        "positive": positives,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "prevalence": round(positives / len(rows), 4) if rows else 0,
    }


def main() -> int:
    args = parse_args()
    audit_dir = args.audit_dir
    if audit_dir is None:
        candidates = sorted(
            (
                path
                for path in (Path(settings.storage_root) / "audit").glob("leg-posture-*")
                if (path / "manifest.json").is_file()
            ),
            reverse=True,
        )
        if not candidates:
            print("没有审计样本目录，请先运行 audit_leg_posture.py")
            return 2
        audit_dir = candidates[0]
    manifest = json.loads((audit_dir / "manifest.json").read_text(encoding="utf-8"))
    rows = [r for r in manifest["rows"] if r.get("audit")]
    # 稳定抽样：优先覆盖有并拢的样本与部分对照
    import random

    rng = random.Random(args.seed)
    sample = rows[:]
    rng.shuffle(sample)
    sample = sample[: args.sample]

    prompt_versions = json.loads((_BACKEND_DIR / "prompt_versions.json").read_text(encoding="utf-8"))
    old_prompt = prompt_versions[MODEL][-1]["prompt"]
    prompts = json.loads((_BACKEND_DIR / "prompt_configs.json").read_text(encoding="utf-8"))
    new_prompt = prompts[MODEL]
    if "双腿并拢" not in new_prompt:
        print("当前模型 Prompt 不是新词表，终止 A/B")
        return 2

    out = []
    for index, row in enumerate(sample, start=1):
        image = Path(settings.storage_root) / (row.get("thumbnail_path") or row["file_path"])
        if not image.is_file():
            image = Path(settings.storage_root) / row["file_path"]
        if not image.is_file():
            continue
        try:
            old_data = _call(old_prompt, image)
            new_data = _call(new_prompt, image)
            out.append(
                {
                    "id": row["id"],
                    "sample_no": row["sample_no"],
                    "stratum": row.get("stratum"),
                    "truth_meaningful": _truth(row),
                    "audit": row.get("audit"),
                    "old_leg": _leg_names(old_data),
                    "new_leg": _leg_names(new_data),
                }
            )
        except Exception as exc:
            print(f"  {row['id']} 调用失败：{exc}")
        if index % 10 == 0:
            print(f"  A/B {index}/{len(sample)}")

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "model": MODEL,
        "sample_size": len(out),
        "truth_definition": "audit legs_visible=full & relation=together & is_default_standing=false",
        "old_prompt_positive": _metrics(out, "old", "并拢"),
        "new_prompt_positive": _metrics(out, "new", "双腿并拢"),
        "old_label_counter": dict(Counter(name for r in out for name in r["old_leg"])),
        "new_label_counter": dict(Counter(name for r in out for name in r["new_leg"])),
        "empty_rate": {
            "old": round(sum(1 for r in out if not r["old_leg"]) / len(out), 4) if out else 0,
            "new": round(sum(1 for r in out if not r["new_leg"]) / len(out), 4) if out else 0,
        },
        "rows": out,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
