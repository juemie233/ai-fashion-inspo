"""生成腿部姿态人工金标准抽检包（只采样/生成联系人表，不跑 AI）。

从已有的 400 张审计抽样中，按“只收刻意并拢”口径分层构造约 200 张待标注集：
- positive：审计初筛为“双腿完整可见、together、非默认站姿”
- negative_default：审计初筛为“双腿并拢但普通站姿”
- negative_other：交叉/分开/抬腿/踮脚/不可见等非 together

输出：
- gold-set.json：空白 human 标注 + 审计 silver 初筛（供复核，不直接当答案）
- annotations.csv：人工填写表
- contact-sheets/：不显示任何已有标签的联系人表

用法：
    python backend/scripts/leg_posture_gold_set.py --limit 200
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.config import settings  # noqa: E402

FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)
SHEET_COLS = 4
SHEET_ROWS = 5
CELL_W = 320
CELL_H = 420


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成腿部姿态人工金标准抽检包")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--seed", type=int, default=20261124)
    parser.add_argument("--audit-dir", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def _font(size: int):
    for candidate in FONT_CANDIDATES:
        path = Path(candidate)
        if not path.is_file():
            continue
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            continue
    return ImageFont.load_default()


def _latest_audit_dir() -> Path:
    base = Path(settings.storage_root) / "audit"
    candidates = sorted(
        (
            path
            for path in base.glob("leg-posture-*")
            if (path / "manifest.json").is_file()
        ),
        reverse=True,
    )
    if not candidates:
        raise SystemExit("未找到审计 manifest，请先运行 audit_leg_posture.py")
    return candidates[0]


def _truth(row: dict) -> bool:
    audit = row.get("audit") or {}
    return (
        audit.get("legs_visible") == "full"
        and audit.get("leg_relation") == "together"
        and audit.get("is_default_standing") is False
    )


def _is_default(row: dict) -> bool:
    audit = row.get("audit") or {}
    return (
        audit.get("legs_visible") == "full"
        and audit.get("leg_relation") == "together"
        and audit.get("is_default_standing") is True
    )


def build_selection(rows: list[dict], limit: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    positives = [r for r in rows if _truth(r)]
    defaults = [r for r in rows if _is_default(r)]
    others = [r for r in rows if not _truth(r) and not _is_default(r)]
    rng.shuffle(positives)
    rng.shuffle(defaults)
    rng.shuffle(others)

    # 目标构成：约 40% 正例、30% 普通站姿负例、30% 其他负例
    pos_n = min(len(positives), round(limit * 0.40))
    default_n = min(len(defaults), round(limit * 0.30))
    other_n = min(len(others), limit - pos_n - default_n)
    selected = (
        [dict(r, gold_group="positive") for r in positives[:pos_n]]
        + [dict(r, gold_group="negative_default") for r in defaults[:default_n]]
        + [dict(r, gold_group="negative_other") for r in others[:other_n]]
    )
    rng.shuffle(selected)
    prefix = {"positive": "P", "negative_default": "ND", "negative_other": "NO"}
    for index, row in enumerate(selected, start=1):
        row["gold_no"] = index
        row["gold_id"] = f"{prefix[row['gold_group']]}-{index:03d}"
    return selected


def _draw_sheet(rows: list[dict], sheet_no: int, out_dir: Path) -> Path:
    sheet = Image.new("RGB", (SHEET_COLS * CELL_W, SHEET_ROWS * CELL_H), "white")
    draw = ImageDraw.Draw(sheet)
    font = _font(18)
    small = _font(14)
    root = Path(settings.storage_root)
    for index, row in enumerate(rows):
        col = index % SHEET_COLS
        line = index // SHEET_COLS
        x = col * CELL_W
        y = line * CELL_H
        path = root / (row.get("thumbnail_path") or row["file_path"])
        if not path.is_file():
            path = root / row["file_path"]
        try:
            with Image.open(path) as image:
                image = ImageOps.exif_transpose(image).convert("RGB")
                image = ImageOps.contain(image, (CELL_W - 12, CELL_H - 40))
        except OSError:
            image = Image.new("RGB", (CELL_W - 12, CELL_H - 40), "#ddd")
        sheet.paste(image, (x + (CELL_W - image.width) // 2, y + 4))
        # 只给编号，不展示任何已有标签/模型判断，避免标注偏差
        draw.multiline_text(
            (x + 6, y + CELL_H - 32),
            f"#{row['gold_no']:03d} / {row['gold_id']}",
            font=small,
            fill="black",
        )
        draw.rectangle((x, y, x + CELL_W - 1, y + CELL_H - 1), outline="#ccc")
    draw.text((8, 4), f"leg together gold set / sheet {sheet_no:02d}", font=font, fill="black")
    out = out_dir / f"sheet-{sheet_no:02d}.jpg"
    sheet.save(out, quality=88)
    return out


def main() -> int:
    args = parse_args()
    audit_dir = args.audit_dir or _latest_audit_dir()
    manifest = json.loads((audit_dir / "manifest.json").read_text(encoding="utf-8"))
    selected = build_selection(manifest["rows"], args.limit, args.seed)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = args.output or Path(settings.storage_root) / "audit" / f"leg-posture-gold-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    records = []
    for row in selected:
        records.append(
            {
                "gold_no": row["gold_no"],
                "gold_id": row["gold_id"],
                "gold_group_do_not_show_reviewer": row["gold_group"],
                "id": row["id"],
                "file_path": row["file_path"],
                "thumbnail_path": row.get("thumbnail_path"),
                "existing_postures": row.get("postures", []),
                "audit_silver": row.get("audit"),
                "human": {
                    "legs_visible": "",
                    "leg_relation": "",
                    "is_default_standing": None,
                    "meaningful_together": None,
                    "notes": "",
                },
            }
        )
    (out_dir / "gold-set.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "source_audit": str(audit_dir),
                "sample_size": len(records),
                "definition": "only deliberate legs-together; natural stance close legs is NOT positive",
                "records": records,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    with (out_dir / "annotations.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "gold_no",
                "gold_id",
                "legs_visible(none/partial/full)",
                "leg_relation(none/together/crossed/apart/one_leg_raised/tiptoe/bent/other)",
                "is_default_standing(true/false)",
                "meaningful_together(true/false)",
                "notes",
            ]
        )
        for row in records:
            writer.writerow([row["gold_no"], row["gold_id"], "", "", "", "", ""])

    contact_dir = out_dir / "contact-sheets"
    contact_dir.mkdir(exist_ok=True)
    for start in range(0, len(records), SHEET_COLS * SHEET_ROWS):
        _draw_sheet(records[start : start + SHEET_COLS * SHEET_ROWS], start // (SHEET_COLS * SHEET_ROWS) + 1, contact_dir)

    summary = {
        "output": str(out_dir),
        "sample_size": len(records),
        "groups": {
            "positive_silver": sum(1 for r in records if r["gold_group_do_not_show_reviewer"] == "positive"),
            "negative_default_silver": sum(1 for r in records if r["gold_group_do_not_show_reviewer"] == "negative_default"),
            "negative_other_silver": sum(1 for r in records if r["gold_group_do_not_show_reviewer"] == "negative_other"),
        },
        "annotation_csv": str(out_dir / "annotations.csv"),
        "contact_sheets": str(contact_dir),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
