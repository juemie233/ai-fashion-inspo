"""素材入库（自 scripts/import_f2_downloads.py 拆出，行为不变）。"""

import json
import shutil
import sqlite3
import sys
import uuid
from pathlib import Path

# 与 backend/scripts 下其它脚本一致：把 backend 加入 sys.path，便于模块方式执行
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings  # noqa: E402
from app.services.file_service import (  # noqa: E402
    _generate_image_thumbnail_sync,
    validate_media,
)
from .scraper_common import utcnow  # noqa: E402
from .scraper_download import extract_video_thumbnail_sync  # noqa: E402

from .f2_plan import (  # noqa: E402
    AUTHOR_MATCH_CONFIDENCE,
    IMPORT_BATCH_DIRNAME,
    ImportDecision,
    library_db_path,
    source_url_for,
)


INSERT_F2_SQL = (
    "INSERT INTO inspirations (id, source_type, source_url, source_author, "
    "source_platform_id, file_path, thumbnail_path, media_type, dominant_colors, "
    "is_favorite, quality_status, rating, is_ai_generated, content_hash, caption, "
    "scraper_task_id, created_at, updated_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, 'pending', 0, 0, ?, ?, NULL, ?, ?)"
)


def apply_import(
    decisions: list[ImportDecision],
    db_path: Path | None = None,
    storage_root: Path | None = None,
    make_thumbnails: bool = True,
    batch_dir: Path | None = None,
    on_progress=None,
    should_stop=None,
) -> dict:
    """把决策为 import 的文件复制进素材库并写库——**不做标签分析、不建向量**。

    步骤：合法性校验 → 复制文件 → 生成缩略图 → INSERT（含 content_hash /
    caption / 平台 ID / 来源作者）→ 博主关联 → 落批次清单（不写话题存档表，
    理由见模块 docstring 第 3 条）。单条失败只回滚该条（删掉已复制的文件），
    不影响整批。

    Args:
        decisions: :func:`build_import_plan` 的结果（其中 action=skip 的会被忽略）。
        db_path: 素材库路径（缺省 :func:`library_db_path`）。
        storage_root: 存储根目录（缺省 settings.storage_root）。
        make_thumbnails: 是否生成缩略图（图片走 PIL、视频走 ffmpeg 首帧）。
        batch_dir: 批次清单目录（缺省 storage/import_batches）。
        on_progress: 可选回调 ``on_progress(done, total)``，供后台任务报告进度。
        should_stop: 可选回调 ``should_stop() -> bool``，返回 True 时提前收尾
            （供任务暂停/取消；已入库的部分保留，并写入批次清单）。

    Returns:
        统计字典：imported / failed / ids / errors / batch_file / stopped。
    """
    db_path = db_path or library_db_path()
    storage_root = storage_root or settings.storage_root
    batch_dir = batch_dir or (storage_root / IMPORT_BATCH_DIRNAME)
    today = utcnow().strftime("%Y-%m")
    now_str = utcnow().strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(str(db_path))

    imported_ids: list[str] = []
    imported_rows: list[dict] = []
    errors: list[dict] = []
    total = sum(1 for d in decisions if d.action == "import")
    processed = 0
    stopped = False

    # 作品类型判据：同一作品下只要有 video 文件就是视频作品。封面（kind=cover）
    # 单看 kind 会被误判成图集，写出打不开的 /note/ 链接。
    video_work_keys = {
        d.item.work_key for d in decisions if d.item.kind == "video"
    }

    for decision in decisions:
        if decision.action != "import":
            continue
        if should_stop is not None and should_stop():
            stopped = True
            break
        item = decision.item
        dest: Path | None = None
        thumb: str | None = None
        try:
            # 合法性/体积校验（与上传路径同一套规则），不合格的直接跳过
            validate_media(item.path)

            sub_dir = "videos" if item.media_type == "video" else "images"
            dest_dir = storage_root / sub_dir / today
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{uuid.uuid4().hex[:16]}{item.path.suffix.lower()}"
            shutil.copy2(item.path, dest)

            if make_thumbnails:
                if item.media_type == "video":
                    # 与图片一致：显式传 storage 下的缩略图目录，避免 --storage-root
                    # 试跑时缩略图落进真实存储（DB 里的相对路径在目标根下找不到）
                    thumb = extract_video_thumbnail_sync(
                        dest, today, thumbs_dir=storage_root / "thumbnails"
                    )
                else:
                    # 显式传入 storage 下的缩略图目录：与素材文件同根，便于整体迁移/清理
                    thumb = _generate_image_thumbnail_sync(
                        dest, thumbs_dir=storage_root / "thumbnails"
                    )

            rel_path = f"{sub_dir}/{today}/{dest.name}"
            insp_id = str(uuid.uuid4())
            conn.execute(
                INSERT_F2_SQL,
                (
                    insp_id,
                    "douyin",
                    # 无作品 ID 的历史文件留空，不造伪链接
                    source_url_for(
                        item, is_video_work=item.work_key in video_work_keys
                    ),
                    item.author_key or item.author_dir,
                    decision.platform_id,
                    rel_path,
                    thumb,
                    item.media_type,
                    decision.content_hash,
                    item.caption or None,
                    now_str,
                    now_str,
                ),
            )
            if decision.blogger_id:
                conn.execute(
                    "INSERT OR IGNORE INTO inspiration_bloggers "
                    "(inspiration_id, blogger_id, confidence) VALUES (?, ?, ?)",
                    (insp_id, decision.blogger_id, AUTHOR_MATCH_CONFIDENCE),
                )
            conn.commit()

            imported_ids.append(insp_id)
            imported_rows.append(
                {
                    "inspiration_id": insp_id,
                    "file_path": rel_path,
                    "thumbnail_path": thumb,
                    "source_file": str(item.path),
                    "platform_id": decision.platform_id,
                    "author_dir": item.author_dir,
                    "work_key": item.work_key,
                    "media_type": item.media_type,
                    "caption": item.caption[:200],
                    "hashtags": item.hashtags,
                    "blogger_id": decision.blogger_id,
                }
            )
        except Exception as exc:  # noqa: BLE001 —— 单条失败不影响整批
            try:
                conn.rollback()
            except Exception:
                pass
            # 清理本条已落盘的文件与缩略图（缩略图是相对路径，需拼回存储根，
            # 否则每次失败都会留下一张孤儿缩略图）
            leftovers = [dest]
            if thumb:
                leftovers.append(storage_root / thumb)
            for leftover in leftovers:
                if leftover is None:
                    continue
                try:
                    leftover.unlink(missing_ok=True)
                except Exception:
                    pass
            errors.append({"source_file": str(item.path), "error": str(exc)[:200]})
        finally:
            processed += 1
            if on_progress is not None:
                on_progress(processed, total)

    # 批次清单：记录本批导入的 id 与来源，便于审计与将来一键回滚。
    # 时间戳精确到秒 + 4 位随机后缀：同秒内跑两次（CLI 与任务队列撞车）时，
    # 纯秒级 batch_id 会互相覆盖清单文件，被覆盖的那一批就再也无法回滚了。
    batch_id = f"f2-{utcnow().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    batch_file = batch_dir / f"{batch_id}.json"
    batch_error: str | None = None
    try:
        batch_dir.mkdir(parents=True, exist_ok=True)
        batch_file.write_text(
            json.dumps(
                {
                    "batch_id": batch_id,
                    "created_at": now_str,
                    "db": str(db_path),
                    "storage_root": str(storage_root),
                    "imported": imported_rows,
                    "errors": errors,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        # 单独记录：清单写失败不影响已入库素材，但**这批将无法回滚**，
        # 必须与「素材失败」区分开并醒目提示（曾把两者混在一个 failed 计数里）
        batch_error = str(exc)[:200]
        batch_file = None  # type: ignore[assignment]
    conn.close()

    return {
        "imported": len(imported_ids),
        "failed": len(errors),
        "ids": imported_ids,
        "errors": errors,
        "batch_file": str(batch_file) if batch_file else "",
        "batch_error": batch_error,
        "stopped": stopped,
    }
