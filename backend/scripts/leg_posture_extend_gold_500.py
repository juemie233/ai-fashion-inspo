"""把现有 200 张金标准扩展到 500 张（只读/生成标注数据，不跑 AI）。

新增 300 张：
- 200 张来自清理前旧 `并拢` 关联（困难候选/低价值负例都包含）
- 100 张随机对照（当前库中不带旧并拢的素材）

保留原 1~200 的编号和人工标签；新增 201~500。生成 25 张联系人表并重生成
annotate.html，已有 xlsx 标注会被页面自动加载。

用法：
    python backend/scripts/leg_posture_extend_gold_500.py
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.config import settings  # noqa: E402
from scripts.leg_posture_gold_set import _draw_sheet  # noqa: E402

BACKUP_DB = Path(settings.storage_root) / "audit" / "leg-posture-full-cleanup-backup-20261124-204644.sqlite"
LEGACY_TAG_ID = 16
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="扩展腿部金标准到 500 张")
    parser.add_argument(
        "--gold-dir",
        type=Path,
        default=Path(settings.storage_root) / "audit" / "leg-posture-gold-20261124-205716",
    )
    parser.add_argument("--seed", type=int, default=20261125)
    return parser.parse_args()


def _read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def _exists(path: Path) -> bool:
    return path.is_file()


def main() -> int:
    args = parse_args()
    gold_dir = args.gold_dir
    gold_path = gold_dir / "gold-set.json"
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    records = gold["records"]
    if len(records) >= 500:
        print("已经是 500 张或更多，跳过")
        return 0

    existing_ids = {str(r["id"]) for r in records}
    old_ids: list[str] = []
    if BACKUP_DB.is_file():
        with _read_only(BACKUP_DB) as con:
            old_ids = [
                str(row[0])
                for row in con.execute(
                    "SELECT DISTINCT inspiration_id FROM inspiration_tags "
                    "WHERE tag_id=? AND source='ai_generated'",
                    (LEGACY_TAG_ID,),
                )
            ]
    rng = random.Random(args.seed)
    rng.shuffle(old_ids)

    current = _read_only(Path(settings.db_file_path))
    current.row_factory = sqlite3.Row
    alive = {
        str(row["id"]): row
        for row in current.execute(
            "SELECT id, file_path, thumbnail_path FROM inspirations WHERE deleted_at IS NULL"
        )
    }
    current.close()

    old_pool = [i for i in old_ids if i in alive and i not in existing_ids]
    old_set = set(old_pool)
    control_pool = [i for i in alive if i not in existing_ids and i not in old_set]
    rng.shuffle(control_pool)

    selected: list[tuple[str, sqlite3.Row]] = []
    for iid in old_pool:
        if len(selected) >= 200:
            break
        row = alive[iid]
        path = Path(settings.storage_root) / row["file_path"]
        if path.suffix.lower() in IMAGE_EXTS and _exists(path):
            selected.append((iid, row))
    for iid in control_pool:
        if len(selected) >= 300:
            break
        row = alive[iid]
        path = Path(settings.storage_root) / row["file_path"]
        if path.suffix.lower() in IMAGE_EXTS and _exists(path):
            selected.append((iid, row))

    if len(selected) < 300:
        print(f"可用素材不足：只选到 {len(selected)} 张")
        return 2

    # 保留原 200 张
    if not (gold_dir / "gold-set-200.json").is_file():
        shutil.copyfile(gold_path, gold_dir / "gold-set-200.json")

    next_no = len(records) + 1
    for iid, row in selected:
        records.append(
            {
                "gold_no": next_no,
                "gold_id": f"X-{next_no:03d}",
                "gold_group_do_not_show_reviewer": "new",
                "id": iid,
                "file_path": row["file_path"],
                "thumbnail_path": row["thumbnail_path"],
                "existing_postures": [],
                "audit_silver": None,
                "human": {
                    "legs_visible": "",
                    "leg_relation": "",
                    "is_default_standing": None,
                    "meaningful_together": None,
                    "notes": "",
                },
            }
        )
        next_no += 1

    gold["sample_size"] = len(records)
    gold["extended_at"] = datetime.now().isoformat(timespec="seconds")
    gold_path.write_text(json.dumps(gold, ensure_ascii=False, indent=2), encoding="utf-8")

    # 重生成全部联系人表（旧 10 张会被同内容覆盖）
    contact_dir = gold_dir / "contact-sheets"
    contact_dir.mkdir(exist_ok=True)
    for start in range(0, len(records), 20):
        _draw_sheet(records[start : start + 20], start // 20 + 1, contact_dir)

    print(
        json.dumps(
            {
                "gold_dir": str(gold_dir),
                "total": len(records),
                "new": len(records) - 200,
                "contact_sheets": str(contact_dir),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
