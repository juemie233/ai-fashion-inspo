"""R2 两段式批量评估：10 张随机四星素材 × 每张 1 个随机单人姿势。

流程（与 scripts/oneoff/r2_controlnet_poc.py + r2_stage2_cleanup.py 一致）：
阶段1：SDXL + ControlNet-Union(lineart) txt2img 出「姿势正确」的图；
阶段2：Qwen-Image-Edit（参考顺序 [阶段1图, 主体照片]）保姿势、去护具、换回日常服装。

产物目录：`<storage_root>/poc/r2_batch10_<日期>/`（storage、gitignore、不入库）：
- `s01..s10/`：每样本的 subject.jpg / stage1.png / stage2.png；
- `summary.json`：样本、姿势、耗时（评分由人工看完图后补进报告）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sqlite3
import sys
import time
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
ONEOFF = ROOT / "scripts" / "oneoff"
for _path in (BACKEND, ONEOFF):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import r2_controlnet_poc as r2a
import r2_stage2_cleanup as r2b
from app.config import settings
from app.services import pose_gallery
from app.services.comfyui.client import ComfyUIClient
from app.services.comfyui.workflow import load_template


def _db_path() -> Path:
    return Path(str(settings.database_url).split(":///", 1)[1])


def _random_subjects(seed: int, count: int) -> list[dict]:
    con = sqlite3.connect(f"file:{_db_path().as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "select id, file_path, rating from inspirations "
            "where deleted_at is null and media_type='image' "
            "and is_ai_generated=0 and rating>=4"
        ).fetchall()
    finally:
        con.close()
    available = [
        {"id": row[0], "path": row[1], "rating": row[2]}
        for row in rows
        if (settings.storage_root / str(row[1])).exists()
    ]
    rng = random.Random(seed)
    if len(available) < count:
        raise SystemExit(f"可用素材不足：{len(available)} < {count}")
    return rng.sample(available, count)


def _random_poses(seed: int, count: int, catalog: list[dict]) -> list[dict]:
    single = [p for p in catalog if "双人" not in p["system"] and p["id"] in r2a.POSE_EN]
    rng = random.Random(seed + 7)
    if len(single) < count:
        raise SystemExit(f"可用单人姿势不足：{len(single)} < {count}")
    return rng.sample(single, count)


def _shrink(src: Path, dst: Path, width: int = 320) -> None:
    with Image.open(src) as img:
        ratio = width / img.width
        img.convert("RGB").resize(
            (width, int(img.height * ratio)), Image.Resampling.LANCZOS
        ).save(dst, quality=85)


async def run(args: argparse.Namespace) -> int:
    subjects = _random_subjects(args.seed, args.count)
    catalog = await pose_gallery.load_catalog()
    poses = _random_poses(args.seed, args.count, catalog)

    out_dir = Path(args.out_dir) if args.out_dir else (
        settings.storage_root / "poc" / f"r2_batch10_{time.strftime('%Y%m%d')}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    template, digest = load_template("qwen_edit")
    client = ComfyUIClient(settings.comfyui_base_url, timeout=settings.comfyui_timeout)
    records: list[dict] = []
    try:
        for idx, pose in enumerate(poses, start=1):
            sample_dir = out_dir / f"s{idx:02d}"
            sample_dir.mkdir(exist_ok=True)
            pose_path = await pose_gallery.ensure_pose_image(pose["id"])
            control = sample_dir / f"control_{pose['id']}.png"
            r2a.prepare_control(pose_path, control)
            ctrl_name = await client.upload_image(
                control.read_bytes(), f"r2b10_ctrl_{pose['id']}.png"
            )
            wf = r2a.build_workflow(
                prompt=f"{r2a.BASE_PROMPT}, {r2a.POSE_EN[pose['id']]}",
                seed=args.seed + idx,
                steps=30,
                cfg=7.0,
                control_image=ctrl_name,
                subject_image=None,
                denoise=1.0,
                prefix=f"r2b10_s{idx:02d}_stage1",
            )
            started = time.monotonic()
            pid = await client.submit(wf)
            entry = await client.wait_for_prompt(pid, timeout=args.timeout)
            image = r2a._pick_image(entry)
            if image is None:
                print(f"[失败] s{idx:02d} 阶段1")
                continue
            stage1 = sample_dir / "stage1.png"
            stage1.write_bytes(
                await client.fetch_image(
                    image.get("filename") or "", image.get("subfolder") or ""
                )
            )
            t1 = round(time.monotonic() - started, 1)

            subject = subjects[idx - 1]
            subject_path = settings.storage_root / subject["path"]
            sub_name = await client.upload_image(
                subject_path.read_bytes(), f"r2b10_subject_{idx:02d}{subject_path.suffix}"
            )
            stage_name = await client.upload_image(
                stage1.read_bytes(), f"r2b10_s{idx:02d}_stage1.png"
            )
            wf2 = r2b.build_edit_workflow(
                template,
                reference_names=[stage_name, sub_name],
                PROMPT=r2b.PROMPT_POSE_FIRST,
                NEGATIVE=r2b.NEGATIVE,
                PREFIX=f"r2b10_s{idx:02d}_stage2",
                RESOLUTION=1024,
                STEPS=25,
                CFG=1.0,
                SAMPLER="res_multistep",
                SCHEDULER="simple",
                SHIFT=3.0,
                SEED=args.seed + idx,
            )
            started = time.monotonic()
            pid2 = await client.submit(wf2)
            entry2 = await client.wait_for_prompt(pid2, timeout=args.timeout)
            image2 = r2a._pick_image(entry2)
            if image2 is None:
                print(f"[失败] s{idx:02d} 阶段2")
                continue
            stage2 = sample_dir / "stage2.png"
            stage2.write_bytes(
                await client.fetch_image(
                    image2.get("filename") or "", image2.get("subfolder") or ""
                )
            )
            t2 = round(time.monotonic() - started, 1)
            _shrink(subject_path, sample_dir / "subject.jpg")
            records.append(
                {
                    "idx": idx,
                    "subject_id": subject["id"],
                    "subject_rating": subject["rating"],
                    "pose_id": pose["id"],
                    "pose_name": pose["name"],
                    "pose_system": pose["system"],
                    "pose_angle": pose["angle"],
                    "stage1": str((sample_dir / "stage1.png").relative_to(out_dir)),
                    "stage2": str((sample_dir / "stage2.png").relative_to(out_dir)),
                    "stage1_s": t1,
                    "stage2_s": t2,
                }
            )
            print(f"[完成] s{idx:02d} {pose['id']} 阶段1 {t1}s / 阶段2 {t2}s")
    finally:
        await client.aclose()

    summary = out_dir / "summary.json"
    summary.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完成 {len(records)} 个样本；summary: {summary}；qwen_edit={digest}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="R2 两段式 10 样本批量评估")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20261031)
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
