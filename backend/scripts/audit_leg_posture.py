"""腿部姿态标签抽检：分层抽样 + 本地 VLM 独立复核 + 联系人表。

只读数据库/图片，不修改任何标签。用于回答：
- `并拢` 标签有多少是“腿不可见也标”的明显错标；
- 有多少是“腿确实并拢但只是默认站姿”的正确但无检索价值；
- 有多少是真正有意义的并拢姿态；
- 以及各属性/冲突组合下的误差差异。

用法（项目根执行）::

    python backend/scripts/audit_leg_posture.py --sample 400 --vlm
    python backend/scripts/audit_leg_posture.py --sample 400 --skip-vlm  # 只生成抽样清单
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import random
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.config import settings  # noqa: E402

LEG_TAG = "并拢"
CONTRADICTORY = {"交叉腿", "腿交叉", "腿部交叉", "跷腿", "翘腿", "分开", "分腿"}
HALF_ATTRS = {"半身", "七分"}
STRATA = [
    ("contradiction", 60),
    ("half_visible", 60),
    ("seated", 60),
    ("full_standing", 80),
    ("other", 60),
    ("control", 80),
]
SHEET_COLS = 4
SHEET_ROWS = 5
SHEET_CELL_W = 320
SHEET_CELL_H = 460
FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)

VLM_PROMPT = """你是独立的图像标注审计员。只根据图片判断腿部可见性和腿部关系，禁止参考任何已有标签或文字提示。

输出一个 JSON 对象，不要输出其他文字：
{
  "legs_visible": "none|partial|full",
  "leg_relation": "none|together|crossed|apart|one_leg_raised|tiptoe|bent|other",
  "is_default_standing": true,
  "label_should_be_together": false,
  "confidence": 0.0,
  "reason": "不超过30个字的中文依据"
}

判定规则：
- 半身图/腰部以上取景/看不到腿：legs_visible=none。
- 只能看到大腿或膝盖以上、看不到双脚：legs_visible=partial。
- 双腿和双脚基本完整可见：legs_visible=full。
- 自然站立、双脚只是自然靠近，没有主动明显并拢：leg_relation=none，label_should_be_together=false。
- 双脚或双膝主动明显靠拢、贴近，形成可识别的并拢姿态：leg_relation=together，label_should_be_together=true。
- 交叉腿/二郎腿：leg_relation=crossed。
- 两腿之间有明确可见间隙：leg_relation=apart。
- 一条腿离地/抬起：leg_relation=one_leg_raised。
- 脚跟离地：leg_relation=tiptoe。
- 膝盖明显弯曲：leg_relation=bent。
- 看不清或不确定：confidence 给低值，不要猜。
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="腿部姿态标签分层抽检（只读）")
    parser.add_argument("--sample", type=int, default=400, help="抽样总数（建议 300~500）")
    parser.add_argument("--seed", type=int, default=20261124, help="随机种子")
    parser.add_argument("--output", type=Path, default=None, help="输出目录")
    parser.add_argument("--vlm", action="store_true", help="调用本地 Ollama VLM 独立复核")
    parser.add_argument("--skip-vlm", action="store_true", help="只生成抽样清单，不调用 VLM")
    parser.add_argument("--model", default="qwen3-vl:8b-instruct")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    return parser.parse_args()


def load_rows() -> list[dict]:
    db_path = Path(settings.db_file_path)
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute(
            """
            SELECT i.id, i.file_path, i.thumbnail_path,
              (SELECT group_concat(t.name, '|')
                 FROM tags t JOIN inspiration_tags it ON it.tag_id=t.id
                WHERE it.inspiration_id=i.id AND t.category='leg_posture') AS postures,
              (SELECT group_concat(t.name, '|')
                 FROM tags t JOIN inspiration_tags it ON it.tag_id=t.id
                WHERE it.inspiration_id=i.id AND t.category='attribute') AS attrs
              FROM inspirations i
             WHERE i.deleted_at IS NULL AND i.media_type='image'
            """
        ).fetchall()
    finally:
        con.close()
    result = []
    storage_root = Path(settings.storage_root)
    for id_, file_path, thumb, postures, attrs in rows:
        if not file_path:
            continue
        posture_set = set(filter(None, (postures or "").split("|")))
        attr_set = set(filter(None, (attrs or "").split("|")))
        if not (storage_root / file_path).is_file():
            continue
        result.append(
            {
                "id": id_,
                "file_path": file_path,
                "thumbnail_path": thumb,
                "postures": sorted(posture_set),
                "attrs": sorted(attr_set),
                "has_label": LEG_TAG in posture_set,
            }
        )
    return result


def classify(row: dict) -> str | None:
    postures = set(row["postures"])
    attrs = set(row["attrs"])
    if not row["has_label"]:
        if "全身" in attrs and "站姿" in attrs:
            return "control"
        return None
    if postures & CONTRADICTORY:
        return "contradiction"
    if attrs & HALF_ATTRS:
        return "half_visible"
    if "坐姿" in attrs:
        return "seated"
    if "全身" in attrs and "站姿" in attrs:
        return "full_standing"
    return "other"


def build_sample(rows: list[dict], total: int, seed: int) -> list[dict]:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        kind = classify(row)
        if kind:
            buckets[kind].append(row)
    rng = random.Random(seed)
    allocated: list[dict] = []
    for name, quota in STRATA:
        pool = buckets.get(name, [])
        take = min(quota, len(pool))
        allocated.extend(rng.sample(pool, take))
    # 某些分层样本不足时，从剩余池按比例补齐；仍不足则少抽并在报告中声明。
    if len(allocated) < total:
        used = {row["id"] for row in allocated}
        remain = [r for r in rows if r["id"] not in used and r["has_label"]]
        rng.shuffle(remain)
        allocated.extend(remain[: max(0, total - len(allocated))])
    allocated = allocated[:total]
    rng.shuffle(allocated)
    for index, row in enumerate(allocated, start=1):
        row["sample_no"] = index
        row["stratum"] = classify(row)
    return allocated


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).is_file():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _image_bytes(path: Path, max_side: int = 1024) -> bytes:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=88, optimize=True)
        return buf.getvalue()


def call_vlm(row: dict, model: str, base_url: str) -> dict:
    path = Path(settings.storage_root) / row["file_path"]
    payload = {
        "model": model,
        "prompt": VLM_PROMPT,
        "images": [base64.b64encode(_image_bytes(path)).decode("ascii")],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0, "num_predict": 220},
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/generate",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    text = body.get("response") or ""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("VLM 未返回 JSON")
    return json.loads(match.group(0))


def verdict(row: dict, audit: dict) -> str:
    visible = audit.get("legs_visible")
    relation = audit.get("leg_relation")
    should = bool(audit.get("label_should_be_together"))
    if not row["has_label"]:
        if should:
            return "missed_meaningful"
        if relation == "together":
            return "control_together_default"
        return "control_ok"
    if visible == "none":
        return "wrong_visibility"
    if visible == "partial":
        return "uncertain_visibility"
    if relation == "together":
        # 用户口径：腿确实并拢但属于普通站姿 = 正确但无检索价值；
        # 只有刻意/非默认的并拢姿态才算真正有价值。
        return "correct_low_value" if audit.get("is_default_standing") else "correct_meaningful"
    if relation in {"crossed", "apart", "one_leg_raised", "tiptoe", "bent", "other"}:
        return "wrong_relation"
    if relation == "none":
        return "wrong_relation"
    return "uncertain_relation"


def annotate_manifest(rows: list[dict], vlm_results: dict[str, dict]) -> None:
    for row in rows:
        result = vlm_results.get(row["id"])
        if result is None:
            row["audit"] = None
            row["verdict"] = "pending"
            continue
        row["audit"] = result
        row["verdict"] = verdict(row, result)


def draw_sheet(rows: list[dict], sheet_no: int, out_dir: Path) -> Path:
    sheet = Image.new("RGB", (SHEET_COLS * SHEET_CELL_W, SHEET_ROWS * SHEET_CELL_H), "white")
    draw = ImageDraw.Draw(sheet)
    title_font = _font(17)
    small = _font(13)
    storage_root = Path(settings.storage_root)
    for cell_index, row in enumerate(rows):
        col = cell_index % SHEET_COLS
        row_no = cell_index // SHEET_COLS
        x = col * SHEET_CELL_W
        y = row_no * SHEET_CELL_H
        path = storage_root / (row.get("thumbnail_path") or row["file_path"])
        if not path.is_file():
            path = storage_root / row["file_path"]
        try:
            with Image.open(path) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")
                im = ImageOps.contain(im, (SHEET_CELL_W - 12, 360))
        except OSError:
            im = Image.new("RGB", (SHEET_CELL_W - 12, 360), "#ddd")
        cell_x = x + (SHEET_CELL_W - im.width) // 2
        sheet.paste(im, (cell_x, y + 8))
        audit = row.get("audit") or {}
        status = row.get("verdict", "pending")
        text = (
            f"#{row['sample_no']:03d} {row['stratum']}\n"
            f"label: {','.join(row['postures'])}\n"
            f"VLM: {audit.get('legs_visible', '?')}/{audit.get('leg_relation', '?')} "
            f"default={audit.get('is_default_standing', '?')}\n"
            f"verdict: {status}"
        )
        draw.multiline_text((x + 6, y + 380), text, font=small, fill="black", spacing=2)
        draw.rectangle((x, y, x + SHEET_CELL_W - 1, y + SHEET_CELL_H - 1), outline="#ccc")
    draw.text((8, 4), f"leg posture audit sample / sheet {sheet_no:02d}", font=title_font, fill="black")
    out = out_dir / f"sheet-{sheet_no:02d}.jpg"
    sheet.save(out, quality=86)
    return out


def summarize(rows: list[dict], group: str | None = None) -> dict:
    subset = [r for r in rows if group is None or r["stratum"] == group]
    counts = Counter(r.get("verdict", "pending") for r in subset)
    total = len(subset)
    return {
        "total": total,
        "verdicts": dict(counts),
        "wrong_visibility": counts["wrong_visibility"],
        "wrong_relation": counts["wrong_relation"],
        "correct_low_value": counts["correct_low_value"],
        "correct_meaningful": counts["correct_meaningful"],
        "uncertain": counts["uncertain_visibility"] + counts["uncertain_relation"] + counts["pending"],
    }


def main() -> int:
    args = parse_args()
    rows = load_rows()
    sample = build_sample(rows, args.sample, args.seed)
    if not sample:
        print("没有可用样本")
        return 2
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = args.output or Path(settings.storage_root) / "audit" / f"leg-posture-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"数据库图片 {len(rows)} 张，抽样 {len(sample)} 张 -> {out_dir}")

    vlm_results: dict[str, dict] = {}
    if args.vlm and not args.skip_vlm:
        jsonl_path = out_dir / "vlm-results.jsonl"
        done: set[str] = set()
        if jsonl_path.is_file():
            for line in jsonl_path.read_text(encoding="utf-8").splitlines():
                try:
                    item = json.loads(line)
                    vlm_results[item["id"]] = item["audit"]
                    done.add(item["id"])
                except (ValueError, KeyError):
                    continue
        for index, row in enumerate(sample, start=1):
            if row["id"] in done:
                continue
            last_error = ""
            for attempt in range(3):
                try:
                    audit = call_vlm(row, args.model, args.ollama_url)
                    vlm_results[row["id"]] = audit
                    with jsonl_path.open("a", encoding="utf-8") as f:
                        f.write(json.dumps({"id": row["id"], "audit": audit}, ensure_ascii=False) + "\n")
                    last_error = ""
                    break
                except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
                    last_error = str(exc)
                    time.sleep(1.5 * (attempt + 1))
            if last_error:
                print(f"  VLM 失败 {row['id']}: {last_error[:120]}")
            if index % 20 == 0:
                print(f"  VLM 进度 {index}/{len(sample)}")
    else:
        print("跳过 VLM；只生成抽样清单和联系人表")

    annotate_manifest(sample, vlm_results)
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "sample_size": len(sample),
                "seed": args.seed,
                "model": args.model if vlm_results else None,
                "rows": sample,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    contact_dir = out_dir / "contact-sheets"
    contact_dir.mkdir(exist_ok=True)
    for start in range(0, len(sample), SHEET_COLS * SHEET_ROWS):
        draw_sheet(sample[start : start + SHEET_COLS * SHEET_ROWS], start // (SHEET_COLS * SHEET_ROWS) + 1, contact_dir)

    labeled = [r for r in sample if r["has_label"]]
    summary = {
        "sample_size": len(sample),
        "labeled_sample_size": len(labeled),
        "control_sample_size": len(sample) - len(labeled),
        "strata": {name: sum(1 for r in sample if r["stratum"] == name) for name, _ in STRATA},
        "overall_labeled": summarize(labeled),
        "control": summarize([r for r in sample if not r["has_label"]]),
        "by_stratum": {
            name: summarize(
                [r for r in sample if r["stratum"] == name and r["has_label"]]
            )
            for name, _ in STRATA
        },
        "contact_sheets": str(contact_dir),
        "manifest": str(manifest_path),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
