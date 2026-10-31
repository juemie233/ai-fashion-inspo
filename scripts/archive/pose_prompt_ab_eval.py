"""提示词增强 A/B 实验：当前配置（对照组） vs +描述词 vs +描述词+负向词。

设计：
- 素材：DB 里 rating>=4、未删除、图片类型的素材，固定随机种子抽 10 张；
- 姿态：图库「全身单人・性感系」，固定随机种子抽 3 个，按顺序循环分配（每个姿态 3~4 个素材）；
- 每组同一个 (素材, 姿态) 组合都跑一次，三组严格配对：
  - control：当前配置 = 线稿参考（i2i，未审查第三档）+ 现有姿势模板，无额外描述/负向；
  - g1：同样配置 + 一段通用高质量描述词（不改姿势/主体约束）；
  - g2：g1 + 负向描述（多余肢体/多余手指/畸形等），其它相同。
- 参数：25 步、cfg 1.0、euler/simple、1024²、seed 同一组合三组一致。

用法：
    python scripts/oneoff/pose_prompt_ab_eval.py --dry-run
    python scripts/oneoff/pose_prompt_ab_eval.py --execute

产物：``<storage_root>/poc/pose_prompt_ab_<日期>/``（gitignore 覆盖，不入库）。
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
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import settings
from app.services import pose_gallery
from app.services.comfyui.client import ComfyUIClient
from app.services.comfyui.workflow import (
    build_edit_workflow,
    load_template,
)

MODEL_KEY = "qwen_2_1_uc_i2i"
STEPS = 25
CFG = 1.0
RESOLUTION = 1024
SAMPLER = "euler"
SCHEDULER = "simple"
SHIFT = 3.0

# 对照组=当前配置：线稿参考 + UI 同款姿势模板（不含额外描述词）
BASE_TEMPLATE = (
    "保持第 1 张参考图的人物长相、发型与服装不变，"
    "按最后一张参考图的姿势（{name}·{angle}）重新生成，{framing}。"
)

# 实验组 1：只加通用高质量描述词，不改姿势/身份约束
EXTRA_DESC = (
    "写实摄影，8K 超高清，皮肤质感细腻，自然肤色，服装面料与褶皱清晰，"
    "柔和自然光，浅景深，细节丰富，画质锐利，时尚杂志风格，全身构图完整。"
)

# 实验组 2：描述词 + 负向词（多余肢体/手指、畸形、模糊等）
EXTRA_NEGATIVE = (
    "多余肢体，多余手指，畸形，解剖错误，比例失调，模糊，低质量，噪点，"
    "水印，文字，画面裁切，面部扭曲，手部畸形，重复的人"
)

GROUPS: dict[str, dict[str, str]] = {
    "control": {"desc": "", "negative": ""},
    "g1": {"desc": EXTRA_DESC, "negative": ""},
    "g2": {"desc": EXTRA_DESC, "negative": EXTRA_NEGATIVE},
}


def _pick_image(entry: dict) -> dict | None:
    for output in (entry.get("outputs") or {}).values():
        images = output.get("images") or []
        if images:
            return images[0]
    return None


def _subject_path(subject_id: str) -> Path:
    db = Path(str(settings.database_url).split(":///", 1)[1])
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        row = con.execute(
            "select file_path from inspirations where id=? and deleted_at is null",
            (subject_id,),
        ).fetchone()
    finally:
        con.close()
    if not row:
        raise SystemExit(f"素材不存在：{subject_id}")
    return settings.storage_root / str(row[0])


def _pick_subjects(limit: int, seed: int) -> list[dict[str, Any]]:
    db = Path(str(settings.database_url).split(":///", 1)[1])
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute(
            """
            select id, file_path, rating
            from inspirations
            where deleted_at is null and media_type='image' and rating >= 4
            order by id
            """
        ).fetchall()
    finally:
        con.close()
    candidates = [
        {"id": r[0], "file_path": r[1], "rating": r[2]}
        for r in rows
        if (settings.storage_root / str(r[1])).exists()
    ]
    if len(candidates) < limit:
        raise SystemExit(f"4 星以上素材不足：只有 {len(candidates)} 张")
    return random.Random(seed).sample(candidates, limit)


async def _pick_poses(count: int, seed: int) -> list[dict[str, Any]]:
    items = await pose_gallery.load_catalog()
    pool = [
        item
        for item in items
        if "全身单人" in item["system"] and "性感系" in item["system"]
    ]
    if len(pool) < count:
        raise SystemExit(f"性感系姿势不足：只有 {len(pool)} 个")
    return random.Random(seed + 1).sample(pool, count)


def _fit_png(src: Path, dst: Path) -> None:
    from PIL import Image

    with Image.open(src) as img:
        img.convert("RGB").save(dst)


async def run(args: argparse.Namespace) -> int:
    subjects = _pick_subjects(args.limit, args.seed)
    poses = await _pick_poses(args.pose_count, args.seed)
    out_dir = Path(args.out_dir) if args.out_dir else (
        settings.storage_root / "poc" / f"pose_prompt_ab_{time.strftime('%Y%m%d')}"
    )
    print(f"素材 {len(subjects)} 张：{[s['id'] for s in subjects]}")
    print(f"姿态 {len(poses)} 个：{[p['id'] for p in poses]}")
    for idx, subject in enumerate(subjects):
        pose = poses[idx % len(poses)]
        print(f"  s{idx + 1:02d} 素材={subject['id']} rating={subject['rating']} 姿态={pose['id']} {pose['name']}")
    print(f"分组：{list(GROUPS)}；组数 × 组合 = {len(GROUPS)} × {len(subjects)} = {len(GROUPS) * len(subjects)} 张")
    print(f"参数：{MODEL_KEY} steps={STEPS} cfg={CFG} sampler={SAMPLER}/{SCHEDULER} res={RESOLUTION}²")
    print(f"输出：{out_dir}")
    if args.dry_run:
        print("[dry-run] 不提交 ComfyUI")
        return 0

    template, digest = load_template("qwen_edit")
    print(f"模板 qwen_edit={digest}；UNET={MODEL_KEY}")
    client = ComfyUIClient(settings.comfyui_base_url, timeout=settings.comfyui_timeout)
    records: list[dict[str, Any]] = []
    started_ts = time.monotonic()
    try:
        for idx, subject in enumerate(subjects):
            pose = poses[idx % len(poses)]
            sample_dir = out_dir / f"s{idx + 1:02d}_{pose['id']}"
            sample_dir.mkdir(parents=True, exist_ok=True)
            subject_png = sample_dir / "subject_input.png"
            _fit_png(_subject_path(subject["id"]), subject_png)
            ref_subject = await client.upload_image(
                subject_png.read_bytes(), f"ab_s{idx + 1:02d}_subject.png"
            )
            pose_path = await pose_gallery.ensure_pose_image(pose["id"])
            pose_png = sample_dir / "pose_input.png"
            _fit_png(pose_path, pose_png)
            ref_pose = await client.upload_image(
                pose_png.read_bytes(), f"ab_s{idx + 1:02d}_pose.png"
            )
            framing = "上半身构图（半身照）" if "上半身" in pose["system"] else "全身照"
            base_prompt = BASE_TEMPLATE.format(name=pose["name"], angle=pose["angle"], framing=framing)
            seed = args.seed + idx
            for group, cfg in GROUPS.items():
                prompt = f"{base_prompt} {cfg['desc']}".strip()
                wf = build_edit_workflow(
                    template,
                    reference_names=[ref_subject, ref_pose],
                    PROMPT=prompt,
                    NEGATIVE=cfg["negative"],
                    PREFIX=f"ab_{group}_s{idx + 1:02d}",
                    RESOLUTION=RESOLUTION,
                    STEPS=STEPS,
                    CFG=CFG,
                    SAMPLER=SAMPLER,
                    SCHEDULER=SCHEDULER,
                    SHIFT=SHIFT,
                    SEED=seed,
                )
                t0 = time.monotonic()
                pid = await client.submit(wf)
                entry = await client.wait_for_prompt(pid, timeout=args.timeout)
                image = _pick_image(entry)
                if image is None:
                    print(f"[失败] s{idx + 1:02d} {group}")
                    continue
                raw = await client.fetch_image(
                    image.get("filename") or "", image.get("subfolder") or ""
                )
                target = sample_dir / f"{group}.png"
                target.write_bytes(raw)
                elapsed = round(time.monotonic() - t0, 1)
                records.append(
                    {
                        "idx": idx + 1,
                        "subject_id": subject["id"],
                        "rating": subject["rating"],
                        "pose_id": pose["id"],
                        "pose_name": pose["name"],
                        "pose_angle": pose["angle"],
                        "group": group,
                        "prompt": prompt,
                        "negative": cfg["negative"],
                        "seed": seed,
                        "image": str(target.relative_to(out_dir)),
                        "elapsed_s": elapsed,
                    }
                )
                total = len(records)
                done = len(subjects) * len(GROUPS)
                eta = (time.monotonic() - started_ts) / total * max(0, done - total)
                print(
                    f"[{total}/{done}] s{idx + 1:02d} {group} {elapsed}s"
                    f"（预计剩余 {eta / 60:.1f} 分钟）",
                    flush=True,
                )
    finally:
        await client.aclose()

    summary = out_dir / "summary.json"
    summary.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"完成 {len(records)} 张；summary: {summary}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="提示词增强 A/B：当前配置 vs +描述词 vs +描述词+负向词")
    parser.add_argument("--limit", type=int, default=10, help="素材数量（4 星以上随机抽）")
    parser.add_argument("--pose-count", type=int, default=3, help="性感系随机姿态数（循环分配）")
    parser.add_argument("--seed", type=int, default=20261031)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--out-dir", default="")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", default=True)
    group.add_argument("--execute", dest="dry_run", action="store_false")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
