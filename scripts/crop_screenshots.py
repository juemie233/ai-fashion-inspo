"""独立脚本：裁剪手机截图类手动上传素材的顶部/底部多余区域。

背景
    手动上传的图片中有一部分是手机全屏截图，画面里混入了状态栏、
    底部导航栏等与穿搭无关的区域。本脚本用于就地裁剪这类素材的
    顶部/底部多余区域，并同步更新缩略图、内容哈希、主色调与向量。

    黑边检测与裁剪核心逻辑已迁移至 ``app.services.crop_service``，
    脚本与本服务共用同一份实现（脚本保留为命令行入口）。

两阶段工作流（先扫描、后执行）:
    1. 扫描:
       python scripts/crop_screenshots.py --scan [--auto]
       扫描库中符合「竖屏截图比例」的手动上传图片，把候选清单写入
       scripts/crop_candidates.json。本阶段不做任何修改。
       - 默认模式：所有候选统一使用 --crop-top / --crop-bottom 比例。
       - --auto 模式：逐张检测黑边（逐行统计亮像素占比，取最高的内容
         条带作为照片主体，裁到黑边消失为止）。检测失败的条目自动置为
         未选中并注明原因，留在清单里供人工处理。

    2. 审查候选清单（人工确认）:
       打开 scripts/crop_candidates.json：
       - 把不需要处理的条目删除，或把该条的 "selected" 改为 false
       - 可按条调整 "crop_top" / "crop_bottom"（相对图片高度的裁剪比例）

    3. 执行:
       python scripts/crop_screenshots.py --apply
       逐条处理候选清单中 selected=true 的素材：
       备份原图 → 裁剪替换原图 → 重新生成缩略图 → 更新 content_hash
       → 重建主色调 → 入队向量回填任务。

约定与保护:
    - 标签、收藏、来源等信息完全不动。
    - 原图先备份到 storage/_crop_backup/{时间戳}/，误操作可手动恢复。
    - 裁剪结果若与库中其他素材内容重复，该条自动跳过并保留原图。
    - 已移入垃圾桶的素材不处理。
    - 向量重建以任务队列方式入队，由 worker 进程消费；未启动 worker
      时向量保持旧值，任务会在 worker 下次启动后执行。
    - --auto 黑边检测针对「深色/黑色背景」截图（暗色相册查看器等）；
      浅色背景的截图请用默认比例或人工调整。
"""

import argparse
import asyncio
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

# 添加 backend 目录到 Python 路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from PIL import Image
from sqlalchemy import select

from app.config import settings
from app.database import async_session, init_db
from app.models.inspiration import Inspiration
from app.services.crop_service import (
    DEFAULT_CROP_BOTTOM,
    DEFAULT_CROP_TOP,
    detect_photo_band,
    crop_image_to_temp as _crop_to_temp,
)
from app.services.file_service import generate_thumbnail
from app.services.task_runners.vector_backfill import create_vector_backfill_task
from app.utils.file_hash import file_sha256
from app.utils.image_utils import extract_dominant_colors
from app.utils.time import utcnow

DEFAULT_CANDIDATE_FILE = Path(__file__).resolve().parent / "crop_candidates.json"
DEFAULT_MIN_RATIO = 1.75  # 高/宽 ≥ 1.75 视为竖屏截图候选（9:16≈1.78、19.5:9≈2.17）


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="裁剪手机截图类手动上传素材的多余区域（先扫描后执行）"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--scan", action="store_true", help="扫描候选素材并生成清单（不修改任何数据）"
    )
    mode.add_argument(
        "--apply", action="store_true", help="按候选清单执行裁剪"
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="扫描时自动检测黑边并逐张计算裁剪比例（失败条目置为未选中）",
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=DEFAULT_CANDIDATE_FILE,
        help="候选清单 JSON 路径（默认 scripts/crop_candidates.json）",
    )
    parser.add_argument(
        "--source-type",
        default="manual_upload",
        help="扫描的来源类型（默认 manual_upload）",
    )
    parser.add_argument(
        "--min-ratio",
        type=float,
        default=DEFAULT_MIN_RATIO,
        help=f"竖屏比例阈值 高/宽（默认 {DEFAULT_MIN_RATIO}）",
    )
    parser.add_argument(
        "--crop-top",
        type=float,
        default=DEFAULT_CROP_TOP,
        help=f"顶部裁剪比例，相对图片高度（默认 {DEFAULT_CROP_TOP}）",
    )
    parser.add_argument(
        "--crop-bottom",
        type=float,
        default=DEFAULT_CROP_BOTTOM,
        help=f"底部裁剪比例，相对图片高度（默认 {DEFAULT_CROP_BOTTOM}）",
    )
    parser.add_argument(
        "--no-vectors", action="store_true", help="执行时不入队向量回填任务"
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="扫描最多列出的候选数（0 表示不限制）"
    )
    return parser.parse_args()


async def scan_candidates(args: argparse.Namespace) -> None:
    """扫描竖屏截图候选素材，写入候选清单 JSON（只读不写库）。"""
    await init_db()

    async with async_session() as db:
        result = await db.execute(
            select(
                Inspiration.id,
                Inspiration.file_path,
                Inspiration.media_type,
                Inspiration.quality_status,
                Inspiration.created_at,
            ).where(
                Inspiration.source_type == args.source_type,
                Inspiration.deleted_at.is_(None),
                Inspiration.file_path.isnot(None),
            )
        )
        rows = result.all()

    candidates: list[dict] = []
    skipped_missing = 0
    auto_ok = 0
    auto_fail = 0
    for insp_id, file_path, media_type, quality_status, created_at in rows:
        if media_type != "image":
            continue
        full = settings.storage_root / file_path
        if not full.exists():
            skipped_missing += 1
            continue
        try:
            with Image.open(full) as img:
                width, height = img.size
        except Exception:
            continue  # 无法解码的图片不做候选
        if height / width < args.min_ratio:
            continue
        item = {
            "id": insp_id,
            "file_path": file_path,
            "width": width,
            "height": height,
            "ratio": round(height / width, 3),
            "size_bytes": full.stat().st_size,
            "quality_status": quality_status or "",
            "created_at": created_at.isoformat(sep=" ") if created_at else "",
            "crop_top": args.crop_top,
            "crop_bottom": args.crop_bottom,
            "selected": True,
        }
        # --auto：黑边检测，逐张计算裁剪比例；失败条目置为未选中并注明原因
        if args.auto:
            try:
                top_px, bottom_px = detect_photo_band(full)
                item.update(
                    {
                        "auto": True,
                        "detected_top_px": top_px,
                        "detected_bottom_px": bottom_px,
                        "crop_top": round(top_px / height, 6),
                        "crop_bottom": round((height - 1 - bottom_px) / height, 6),
                    }
                )
                auto_ok += 1
            except ValueError as e:
                item.update({"selected": False, "note": f"自动检测失败：{e}"})
                auto_fail += 1
        candidates.append(item)

    candidates.sort(key=lambda c: c["ratio"], reverse=True)
    if args.limit > 0:
        candidates = candidates[: args.limit]

    plan = {
        "version": 1,
        "generated_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
        "total_candidates": len(candidates),
        "params": {
            "source_type": args.source_type,
            "min_ratio": args.min_ratio,
            "crop_top": args.crop_top,
            "crop_bottom": args.crop_bottom,
        },
        "items": candidates,
    }
    args.file.parent.mkdir(parents=True, exist_ok=True)
    args.file.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"扫描完成：共 {len(candidates)} 个候选（源文件缺失跳过 {skipped_missing} 个）")
    if args.auto:
        print(f"黑边自动检测：成功 {auto_ok} 个，失败 {auto_fail} 个（已置为未选中，请人工确认）")
    print("")
    print(
        f"{'序号':>4}  {'素材 ID':<36}  {'宽x高':<13}  {'高宽比':>6}  "
        f"{'裁剪(上/下)':<13}  {'质量状态':<8}  创建时间"
    )
    for idx, c in enumerate(candidates, start=1):
        crop_str = (
            "待人工"
            if c.get("selected") is False
            else f"{c['crop_top'] * 100:.1f}%/{c['crop_bottom'] * 100:.1f}%"
        )
        print(
            f"{idx:>4}  {c['id']:<36}  {c['width']}x{c['height']:<8}  "
            f"{c['ratio']:>6}  {crop_str:<13}  {c['quality_status']:<8}  {c['created_at']}"
        )
    print("")
    print(f"候选清单已写入: {args.file}")
    print("下一步：审查该 JSON（删除条目 / 改 selected 为 false / 调整 crop_top、crop_bottom）")
    print(f"确认后执行: python scripts/crop_screenshots.py --apply --file {args.file.name}")


async def apply_crops(args: argparse.Namespace) -> None:
    """按候选清单执行裁剪：备份、裁剪、缩略图、哈希、主色调、向量回填。"""
    if not args.file.exists():
        print(f"候选清单不存在: {args.file}，请先运行 --scan")
        return

    try:
        plan = json.loads(args.file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"候选清单解析失败: {e}")
        return

    params = plan.get("params") or {}
    items = [i for i in plan.get("items", []) if i.get("selected", True)]
    if not items:
        print("候选清单中没有选中任何素材，无需处理")
        return

    print(f"开始执行：共 {len(items)} 条待处理")

    await init_db()
    async with async_session() as db:
        result = await db.execute(
            select(Inspiration).where(Inspiration.id.in_([i["id"] for i in items]))
        )
        insp_map = {insp.id: insp for insp in result.scalars()}

        backup_dir = (
            settings.storage_root
            / "_crop_backup"
            / datetime.now().strftime("%Y-%m-%d_%H%M%S")
        )
        backup_dir.mkdir(parents=True, exist_ok=True)

        successes: list[tuple[str, str, str | None, str | None]] = []
        skipped: list[tuple[str, str]] = []

        for item in items:
            insp_id = item["id"]
            insp = insp_map.get(insp_id)
            if insp is None or insp.deleted_at is not None:
                skipped.append((insp_id, "记录不存在或已入垃圾桶"))
                continue
            full = settings.storage_root / insp.file_path
            if not full.exists():
                skipped.append((insp_id, "文件不存在"))
                continue

            # 尺寸守卫：与扫描时记录不一致说明文件已变化（可能已处理过），
            # 按旧比例裁剪会算错，跳过并提示重新扫描
            rec_width = item.get("width")
            rec_height = item.get("height")
            if rec_width is not None and rec_height is not None:
                try:
                    with Image.open(full) as probe:
                        cur_width, cur_height = probe.size
                except Exception:
                    skipped.append((insp_id, "文件无法解码"))
                    continue
                if abs(cur_width - int(rec_width)) > 2 or abs(cur_height - int(rec_height)) > 2:
                    skipped.append(
                        (
                            insp_id,
                            f"文件尺寸已变化（现 {cur_width}x{cur_height}，"
                            f"扫描时 {rec_width}x{rec_height}），请重新扫描",
                        )
                    )
                    continue

            top_frac = float(item.get("crop_top", params.get("crop_top", DEFAULT_CROP_TOP)))
            bottom_frac = float(
                item.get("crop_bottom", params.get("crop_bottom", DEFAULT_CROP_BOTTOM))
            )

            tmp: Path | None = None
            try:
                # 1. 裁剪到临时文件（此阶段原图不动，失败无副作用）
                tmp = _crop_to_temp(full, top_frac, bottom_frac)

                # 2. 新内容哈希 + 去重检查（重复则放弃本条，保留原图）
                new_hash = file_sha256(tmp)
                if new_hash:
                    dup_id = (
                        await db.execute(
                            select(Inspiration.id).where(
                                Inspiration.content_hash == new_hash,
                                Inspiration.id != insp_id,
                                Inspiration.deleted_at.is_(None),
                            )
                        )
                    ).scalars().first()
                    if dup_id:
                        skipped.append((insp_id, f"裁剪结果与素材 {dup_id} 内容重复"))
                        tmp.unlink(missing_ok=True)
                        continue

                # 3. 备份原图
                shutil.copy2(full, backup_dir / f"{insp_id}{full.suffix}")

                # 4. 原子替换原图（此后文件已变更，必须登记为成功）
                os.replace(tmp, full)
                tmp = None

                # 5. 重新生成缩略图（失败仅告警，沿用旧缩略图）
                thumb_path = insp.thumbnail_path
                new_thumb = await generate_thumbnail(full)
                if new_thumb:
                    if thumb_path and (
                        settings.storage_root / thumb_path
                    ) != (settings.storage_root / new_thumb):
                        (settings.storage_root / thumb_path).unlink(missing_ok=True)
                    thumb_path = new_thumb
                else:
                    print(f"  警告 {insp_id}: 缩略图生成失败，沿用旧缩略图")

                # 6. 主色调重算（仅原值非空时刷新，避免引入新的过滤属性）
                colors = insp.dominant_colors
                if colors is not None:
                    new_colors = extract_dominant_colors(full)
                    colors = json.dumps(new_colors) if new_colors else insp.dominant_colors

                successes.append((insp_id, new_hash or "", thumb_path, colors))
                print(
                    f"  完成 {insp_id}: 裁剪顶部 {top_frac * 100:.1f}% / "
                    f"底部 {bottom_frac * 100:.1f}%"
                )
            except Exception as e:
                if tmp is not None and tmp.exists():
                    tmp.unlink(missing_ok=True)
                skipped.append((insp_id, f"处理失败: {e}"))

        # 7. 写回数据库（标签/收藏/来源等字段不动）
        for insp_id, new_hash, thumb_path, colors in successes:
            insp = insp_map[insp_id]
            if new_hash:
                insp.content_hash = new_hash
            insp.thumbnail_path = thumb_path
            insp.dominant_colors = colors
            insp.updated_at = utcnow()
        await db.commit()

        # 8. 向量回填：图像向量按新图重建，文本向量沿用现有标签
        vector_task_id: int | None = None
        if successes and not args.no_vectors:
            task = await create_vector_backfill_task(db, [s[0] for s in successes])
            vector_task_id = task.id if task else None

    print("")
    print(f"执行完毕：成功 {len(successes)} 条，跳过 {len(skipped)} 条")
    for insp_id, reason in skipped:
        print(f"  跳过 {insp_id}: {reason}")
    if successes:
        print(f"原图备份目录: {backup_dir}")
        if vector_task_id:
            print(f"已入队向量回填任务 #{vector_task_id}（由 worker 消费）")
        else:
            print("未入队向量回填任务（--no-vectors），图像向量保持旧值")
        print("如需恢复某条素材，用备份目录中的原图覆盖回原路径即可")


async def main() -> None:
    """脚本入口：按模式分发扫描/执行。"""
    # Windows 控制台默认 GBK，重配为 UTF-8 保证中文输出不乱码
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    if args.auto and args.apply:
        print("--auto 仅用于 --scan 模式（自动检测在扫描阶段完成，执行阶段按清单比例裁剪）")
        return
    if args.scan:
        await scan_candidates(args)
    else:
        await apply_crops(args)


if __name__ == "__main__":
    asyncio.run(main())
