"""下载模块 — 图片批量下载、视频流式下载、话题存档与缩略图提取。

负责：
  - 图片批量下载入库（URL 去重 + 内容 MD5 去重 + 墓碑表去重）
  - 视频流式下载入库（大小上限 + ffmpeg 首帧缩略图）
  - 话题标签自动提取与存档（scraper_hashtags）
  - 视频首帧缩略图同步提取

依赖：
  - scraper_common 中的通用工具与常量
"""

import hashlib
import json
import random
import sqlite3 as _sqlite3
import subprocess
import time
import uuid
from pathlib import Path

from app.config import settings

from .scraper_common import (
    DEFAULT_VIDEO_MAX_BYTES,
    MAX_VIDEO_BYTES,
    utcnow,
    _rdsleep,
    clean_media_url,
    is_content_image,
    build_download_headers,
)

# ═══════════════════════════════════════════════════════════════
#  话题存档常量
# ═══════════════════════════════════════════════════════════════

"""单条话题的来源明细保留条数（防 source_meta 无限膨胀）。"""
_HASHTAG_META_MAX = 10


# ═══════════════════════════════════════════════════════════════
#  素材入库 INSERT（直写 sqlite 路径）
# ═══════════════════════════════════════════════════════════════

"""图片入库 INSERT：列清单 / 值 / 占位符三者必须一一对应。

教训：曾漏写 updated_at 的占位符（14 列 13 值），sqlite 报
「13 values for 14 columns」，重试包装器误当网络错误反复重新下载，
整条抖音采集链路静默颗粒无收（任务 #46）。提取为常量供回归测试
直接执行，防止再次漂移。"""
INSERT_INSPIRATION_IMAGE_SQL = (
    "INSERT INTO inspirations (id, source_type, source_url, file_path, "
    "thumbnail_path, media_type, dominant_colors, is_favorite, "
    "quality_status, rating, is_ai_generated, content_hash, caption, "
    "scraper_task_id, created_at, updated_at) "
    "VALUES (?, ?, ?, ?, NULL, ?, NULL, 0, 'pending', 0, 0, ?, ?, ?, ?, ?)"
)

"""视频入库 INSERT：thumbnail 为 ffmpeg 首帧非空，content_hash 暂空。"""
INSERT_INSPIRATION_VIDEO_SQL = (
    "INSERT INTO inspirations (id, source_type, source_url, file_path, "
    "thumbnail_path, media_type, dominant_colors, is_favorite, "
    "quality_status, rating, is_ai_generated, content_hash, caption, "
    "scraper_task_id, created_at, updated_at) "
    "VALUES (?, ?, ?, ?, ?, ?, NULL, 0, 'pending', 0, 0, NULL, ?, ?, ?, ?)"
)

"""每篇笔记提取的话题数上限（防脏数据）。"""
_HASHTAG_PER_NOTE_MAX = 20

"""本次脚本会话累计处理的话题数（含重复命中，供任务完成日志统计）。"""
_HASHTAG_SAVED_COUNT = [0]


# ═══════════════════════════════════════════════════════════════
#  话题存档
# ═══════════════════════════════════════════════════════════════


def ensure_hashtag_table(conn) -> None:
    """确保话题存档表存在（脚本独立进程兜底；主库由 Alembic 迁移建表）。

    Args:
        conn: 同步 sqlite3 连接。
    """
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS scraper_hashtags ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name VARCHAR(64) NOT NULL UNIQUE, "
            "seen_count INTEGER NOT NULL DEFAULT 1, "
            "first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "source_kind VARCHAR(16) NOT NULL DEFAULT 'blogger', "
            "source_id INTEGER, "
            "note_url TEXT, "
            "source_meta TEXT)"
        )
        conn.commit()
    except Exception:
        pass  # 建表失败不阻塞采集主流程


def save_hashtags(conn, meta: dict | None, note_url: str) -> int:
    """把一篇笔记的话题 upsert 进 scraper_hashtags（同笔记只处理一次）。

    幂等：meta["hashtags_saved"] 标记——同一笔记的图片/视频多次入库时
    只累计一次计数。返回本次处理的话题数（0 = 已处理/无话题）。

    Args:
        conn: 攒批提交用的 sqlite 连接（与素材入库同连接，事务一致）。
        meta: meta_map 中该笔记的元数据（含 caption/blogger_id/tags）。
        note_url: 笔记页面 URL（作为来源明细记录）。

    Returns:
        本次处理的话题数（0 = 已处理/无话题）。
    """
    if not meta or meta.get("hashtags_saved"):
        return 0
    tags = meta.get("tags") or []
    meta["hashtags_saved"] = True  # 先标记：即使后续失败也不再重复处理
    if not tags:
        return 0
    now_str = utcnow().strftime("%Y-%m-%d %H:%M:%S")
    kind = meta.get("source_kind", "blogger")
    source_id = meta.get("blogger_id")
    saved = 0
    for tag in tags[:_HASHTAG_PER_NOTE_MAX]:
        name = (tag or "").strip().lstrip("#").strip()
        if not name or len(name) > 64:
            continue
        try:
            row = conn.execute(
                "SELECT seen_count, source_meta FROM scraper_hashtags "
                "WHERE name = ?",
                (name,),
            ).fetchone()
            item = {
                "kind": kind,
                "id": source_id,
                "note_url": note_url,
                "at": now_str,
            }
            if row is None:
                conn.execute(
                    "INSERT INTO scraper_hashtags "
                    "(name, seen_count, last_seen_at, source_kind, source_id, "
                    "note_url, source_meta) "
                    "VALUES (?, 1, ?, ?, ?, ?, ?)",
                    (name, now_str, kind, source_id, note_url,
                     json.dumps([item], ensure_ascii=False)),
                )
            else:
                try:
                    items = json.loads(row[1]) if row[1] else []
                except Exception:
                    items = []
                items = (items + [item])[-_HASHTAG_META_MAX:]
                conn.execute(
                    "UPDATE scraper_hashtags SET seen_count = seen_count + 1, "
                    "last_seen_at = ?, source_kind = ?, source_id = ?, "
                    "note_url = ?, source_meta = ? WHERE name = ?",
                    (now_str, kind, source_id, note_url,
                     json.dumps(items, ensure_ascii=False), name),
                )
            saved += 1
            _HASHTAG_SAVED_COUNT[0] += 1
        except Exception:
            pass  # 话题写入失败不影响采集主流程
    return saved


# ═══════════════════════════════════════════════════════════════
#  视频缩略图提取
# ═══════════════════════════════════════════════════════════════


def extract_video_thumbnail_sync(video_path: Path, today: str) -> str | None:
    """用 ffmpeg 提取视频首帧缩略图（同步 subprocess），失败返回 None。

    Args:
        video_path: 视频文件路径。
        today: 日期字符串（用于构建缩略图子目录）。

    Returns:
        缩略图相对路径（如 "thumbnails/2025-01/thumb_xxx.jpg"），失败返回 None。
    """
    thumb_dir = settings.thumbnails_dir / today
    thumb_dir.mkdir(parents=True, exist_ok=True)
    thumb_name = f"thumb_{video_path.stem}.jpg"
    thumb_path = thumb_dir / thumb_name
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-ss", "1", "-i", str(video_path),
                "-frames:v", "1", "-vf", "scale=400:-2", str(thumb_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except Exception:
        return None
    if thumb_path.exists() and thumb_path.stat().st_size > 0:
        return f"thumbnails/{today}/{thumb_name}"
    return None


# ═══════════════════════════════════════════════════════════════
#  图片下载
# ═══════════════════════════════════════════════════════════════


def download_batch(
    urls: list[tuple[str, str]],
    task_id: int,
    existing_url_set: set[str],
    remaining: int,
    img_dir: Path,
    today: str,
    httpx_module,
    cookies: dict | None = None,
    content_hash_set: set[str] | None = None,
    meta_map: dict[str, dict] | None = None,
    platform: str = "xiaohongshu",
) -> tuple[int, int, int, int, int]:
    """下载一批图片，立即入库。使用同步 sqlite3 避免 event loop 冲突。

    urls 中每项为 (笔记页面 URL, 图片 CDN URL)：笔记页面 URL 存入 source_url 作为
    「原始链接」，图片 CDN URL 用于下载与去重。

    meta_map（可选）：笔记页面 URL → {"caption": str, "blogger_id": int}
    —— 按博主采集时传入，图片入库同步写入笔记正文 caption，并建立
    inspiration_bloggers 关联（博主标记）。

    platform：下载请求头按平台取 Referer（xiaohongshu | douyin），CDN 鉴权必需。

    去重策略（三层）：
    1. 图片 URL 内存去重 — 同次运行内相同图片不重复下载
    2. DB 墓碑表去重 — 跨次采集相同图片 URL 不重复入库
    3. 内容 MD5 去重 — 同一图片不同 URL（CDN 多节点）不重复入库

    Args:
        urls: (笔记页面 URL, 图片 CDN URL) 列表。
        task_id: 采集任务 ID。
        existing_url_set: 已存在 URL 集合（增量更新）。
        remaining: 剩余需要采集的数量。
        img_dir: 图片存储目录。
        today: 日期字符串（用于构建子目录）。
        httpx_module: httpx 模块（从调用方传入，避免循环导入）。
        cookies: 浏览器 Cookie 字典。
        content_hash_set: 内容 MD5 集合（用于内容去重）。
        meta_map: 笔记元数据映射。
        platform: 平台标识。

    Returns:
        (added, skipped_existing, skipped_non200, skipped_network, skipped_content_dup)
    """
    # 构建平台匹配的请求头（带浏览器 Cookie 以通过 CDN 鉴权）
    req_headers = build_download_headers(platform, cookies)

    db_path = settings.storage_root.parent / "fashion_inspo.db"

    # 按图片 URL 去重（同一图片可能在不同卡片/搜索中重复出现）
    unique: list[tuple[str, str]] = []
    _seen_url: set[str] = set()
    for note_url, img_url in urls:
        if img_url not in _seen_url:
            _seen_url.add(img_url)
            unique.append((note_url, img_url))

    # 查询这批图片 URL 及笔记 URL 中已在墓碑表中的（同步查询）
    # 删除素材时写入的是素材的 source_url（笔记页地址），采集成功时写入的是图片 CDN 地址，
    # 两者都需匹配：任一命中即视为「已删除/已采集」，跳过入库。
    if unique:
        img_urls = [img_url for _, img_url in unique]
        note_urls = [note_url for note_url, _ in unique if note_url]
        conn = None
        try:
            conn = _sqlite3.connect(str(db_path))
            # 确保墓碑表存在
            conn.execute(
                "CREATE TABLE IF NOT EXISTS scraper_seen_urls "
                "(source_url TEXT PRIMARY KEY, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
            conn.commit()
            placeholders = ",".join("?" * (len(img_urls) + len(note_urls)))
            cur = conn.execute(
                f"SELECT source_url FROM scraper_seen_urls "
                f"WHERE source_url IN ({placeholders})",
                img_urls + note_urls,
            )
            db_existing = {r[0] for r in cur.fetchall()}
            existing_url_set.update(db_existing)
        except Exception:
            pass
        finally:
            if conn is not None:
                conn.close()

    added = 0
    skipped_existing = 0
    skipped_content_dup = 0
    skipped_non200 = 0
    skipped_network = 0

    # 每张图独立提交（素材行 + 墓碑同一事务），失败只影响当前图：
    # 此前「攒批 20 条一次提交」在批内任一张失败时 rollback 会连同之前
    # 已成功写入的图一起回滚（行丢失 + 文件残留成孤儿），抖音链路审查
    # 发现（2026-08）。逐条提交后失败窗口只剩当前图，其文件随行删除。
    _BATCH_COMMIT = 20  # 向量回填任务的攒批阈值（独立事务入队）
    batch_conn = None
    # 本批待回填向量的素材 ID：攒批后合并为一个向量回填任务入队，
    # 避免每张图各建一个任务导致任务队列膨胀（此前 75 张图=75 个任务）。
    backfill_ids: list[str] = []
    try:
        batch_conn = _sqlite3.connect(str(db_path))
        ensure_hashtag_table(batch_conn)  # 话题存档表兜底（独立进程）
    except Exception:
        batch_conn = None

    def _flush_backfill():
        """把攒批的素材 ID 合并为一个向量回填任务入队（独立事务提交）。

        素材行已逐条提交，此处失败不影响素材本身；向量回填任务缺失时
        由 worker 启动兜底 / 手动一键回填补上（不阻塞采集主流程）。
        """
        if batch_conn is None or not backfill_ids:
            return
        now_str = utcnow().strftime("%Y-%m-%d %H:%M:%S")
        # priority 显式置 0（列 NOT NULL 且无 SQL 端默认值，原生 INSERT 必须携带）
        batch_conn.execute(
            "INSERT INTO task_queue (type, status, priority, progress, total, done, "
            "result, max_retries, retry_count, created_at, updated_at) "
            "VALUES ('vector_backfill', 'pending', 0, 0, ?, 0, ?, 2, 0, ?, ?)",
            (
                len(backfill_ids),
                json.dumps(
                    {"inspiration_ids": list(backfill_ids)}
                ),
                now_str,
                now_str,
            ),
        )
        batch_conn.commit()
        backfill_ids.clear()

    for note_url, img_url in unique:
        if added >= remaining:
            break
        # 图片 URL 或笔记 URL 命中墓碑/已见集合：删除过的素材不再重复采集
        if img_url in existing_url_set or note_url in existing_url_set:
            skipped_existing += 1
            continue

        for attempt in range(1, 4):
            try:
                resp = httpx_module.get(
                    img_url,
                    headers=req_headers,
                    timeout=30,
                    follow_redirects=True,
                )
                if resp.status_code != 200:
                    skipped_non200 += 1
                    break
                ext = ".jpg"
                ct = resp.headers.get("content-type", "")
                if "png" in ct:
                    ext = ".png"
                elif "webp" in ct:
                    ext = ".webp"
                fname = (
                    f"{str(uuid.uuid4()).replace('-', '')[:16]}{ext}"
                )
                fpath = img_dir / fname
                content = resp.content
                fpath.write_bytes(content)

                # 内容 MD5 去重：相同图片不同 URL（CDN 多节点）不重复入库
                # （注意：hash 在入库成功后才写入集合——若入库失败重试，
                # 同一内容不应被自己误判为重复，见下方成功路径）
                if content_hash_set is not None:
                    content_md5 = hashlib.md5(content).hexdigest()
                    if content_md5 in content_hash_set:
                        fpath.unlink()  # 删除刚下载的重复文件
                        skipped_content_dup += 1
                        # 将重复 URL 写入墓碑表，避免下次采集重复下载
                        try:
                            _conn = _sqlite3.connect(str(db_path))
                            _conn.execute(
                                "INSERT OR IGNORE INTO scraper_seen_urls "
                                "(source_url) VALUES (?)",
                                (img_url,),
                            )
                            _conn.commit()
                            _conn.close()
                        except Exception:
                            pass
                        existing_url_set.add(img_url)
                        break

                # 同步写入数据库（素材行 + 墓碑同一事务，逐条提交）：
                # 失败仅回滚当前图并删除其文件，不影响本批已入库的图。
                if batch_conn is None:
                    raise RuntimeError("数据库连接不可用")
                try:
                    insp_id = str(uuid.uuid4())
                    rel_path = f"images/{today}/{fname}"
                    now_str = utcnow().strftime("%Y-%m-%d %H:%M:%S")
                    # 与主库 content_hash 列一致（SHA-256），供上传/管理页索引查重
                    content_sha256 = hashlib.sha256(content).hexdigest()
                    # 按博主采集：同笔记的图片共享正文 caption，并关联博主
                    meta = meta_map.get(note_url) if meta_map else None
                    caption_val = (meta or {}).get("caption")
                    blogger_id = (meta or {}).get("blogger_id")
                    save_hashtags(
                        batch_conn, meta, note_url
                    )  # 话题存档（幂等，每笔记一次）
                    batch_conn.execute(
                        INSERT_INSPIRATION_IMAGE_SQL,
                        (
                            insp_id,
                            "scraper",
                            note_url or img_url,
                            rel_path,
                            "image",
                            content_sha256,
                            caption_val,
                            task_id,
                            now_str,
                            now_str,
                        ),
                    )
                    if blogger_id:
                        batch_conn.execute(
                            "INSERT OR IGNORE INTO inspiration_bloggers "
                            "(inspiration_id, blogger_id, confidence) VALUES (?, ?, 1.0)",
                            (insp_id, blogger_id),
                        )
                    batch_conn.execute(
                        "INSERT OR IGNORE INTO scraper_seen_urls (source_url) VALUES (?)",
                        (img_url,),
                    )
                    batch_conn.commit()
                except Exception:
                    try:
                        batch_conn.rollback()  # 清除 aborted 事务态（仅当前图受影响）
                    except Exception:
                        pass
                    try:
                        fpath.unlink()
                    except Exception:
                        pass
                    raise  # 重新抛出，让外层重试逻辑处理

                # 入库成功后才记录 MD5：入库失败重试时同一内容不被自己误判为重复
                if content_hash_set is not None:
                    content_hash_set.add(content_md5)
                added += 1
                existing_url_set.add(img_url)

                # 向量回填任务攒批入队（独立事务；失败不阻塞采集，worker 兜底）
                backfill_ids.append(insp_id)
                if len(backfill_ids) >= _BATCH_COMMIT:
                    try:
                        _flush_backfill()
                    except Exception:
                        pass

                # 下载间隔：模拟人类逐张保存的行为
                time.sleep(random.uniform(0.3, 1.0))
                break
            except Exception as e:
                err = str(e)[:60]
                if attempt < 3:
                    backoff = 2 ** attempt
                    print(
                        f"    下载重试 ({attempt}/3) {img_url[:40]}..."
                        f" ({err})，{backoff}s 后重试"
                    )
                    time.sleep(backoff)
                else:
                    print(
                        f"    下载失败 {img_url[:40]}... ({err})"
                    )
                    skipped_network += 1

    # 收尾：把剩余攒批向量任务入队并关闭连接（入队失败不阻塞，worker 兜底）
    try:
        _flush_backfill()
    except Exception:
        pass
    if batch_conn is not None:
        batch_conn.close()

    return (
        added,
        skipped_existing,
        skipped_non200,
        skipped_network,
        skipped_content_dup,
    )


# ═══════════════════════════════════════════════════════════════
#  视频下载
# ═══════════════════════════════════════════════════════════════


def download_videos(
    video_pairs: list[tuple[str, str]],
    task_id: int,
    existing_url_set: set[str],
    remaining: int,
    videos_dir: Path,
    today: str,
    httpx_module,
    cookies: dict | None = None,
    meta_map: dict[str, dict] | None = None,
    platform: str = "xiaohongshu",
) -> tuple[int, int]:
    """下载一批短视频并入库为 video 类型（同步 sqlite3 + 同步 ffmpeg 缩略图）。

    video_pairs 每项为 (笔记页面 URL, 视频 CDN URL)。去重策略与图片一致
    （URL 内存去重 + 墓碑表去重）；视频不做内容哈希去重（同一视频多 CDN 节点
    罕见，且逐字节哈希代价高）。

    meta_map（可选）：笔记页面 URL → {"caption": str, "blogger_id": int}
    —— 按博主采集时传入，视频入库同步写入笔记正文 caption 并关联博主。

    platform：下载请求头按平台取 Referer（xiaohongshu | douyin），CDN 鉴权必需。
    大小上限按平台区分（MAX_VIDEO_BYTES，抖音单条更小以控磁盘占用）。

    Args:
        video_pairs: (笔记页面 URL, 视频 CDN URL) 列表。
        task_id: 采集任务 ID。
        existing_url_set: 已存在 URL 集合。
        remaining: 剩余需要采集的数量。
        videos_dir: 视频存储目录。
        today: 日期字符串。
        httpx_module: httpx 模块。
        cookies: 浏览器 Cookie 字典。
        meta_map: 笔记元数据映射。
        platform: 平台标识。

    Returns:
        (added, skipped): 成功入库数与跳过数（已存在 / 下载失败）。
    """
    # 构建平台匹配的请求头（带浏览器 Cookie 以通过 CDN 鉴权）
    req_headers = build_download_headers(platform, cookies)

    # 单个视频下载大小上限：避免超大视频撑爆磁盘
    max_video_bytes = MAX_VIDEO_BYTES.get(platform, DEFAULT_VIDEO_MAX_BYTES)

    db_path = settings.storage_root.parent / "fashion_inspo.db"

    # 视频 URL 内存去重
    unique: list[tuple[str, str]] = []
    _seen_url: set[str] = set()
    for note_url, video_url in video_pairs:
        if video_url and video_url not in _seen_url:
            _seen_url.add(video_url)
            unique.append((note_url, video_url))

    # 查询这批视频 URL 中已在墓碑表中的
    if unique:
        video_urls = [u for _, u in unique]
        conn = None
        try:
            conn = _sqlite3.connect(str(db_path))
            conn.execute(
                "CREATE TABLE IF NOT EXISTS scraper_seen_urls "
                "(source_url TEXT PRIMARY KEY, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
            conn.commit()
            placeholders = ",".join("?" * len(video_urls))
            cur = conn.execute(
                f"SELECT source_url FROM scraper_seen_urls "
                f"WHERE source_url IN ({placeholders})",
                video_urls,
            )
            db_existing = {r[0] for r in cur.fetchall()}
            existing_url_set.update(db_existing)
        except Exception:
            pass
        finally:
            if conn is not None:
                conn.close()

    added = 0
    skipped = 0
    batch_conn = None
    try:
        batch_conn = _sqlite3.connect(str(db_path))
        ensure_hashtag_table(batch_conn)  # 话题存档表兜底（独立进程）
    except Exception:
        batch_conn = None

    for note_url, video_url in unique:
        if added >= remaining:
            break
        if video_url in existing_url_set:
            skipped += 1
            continue

        fpath: Path | None = None
        try:
            # 流式下载视频（边下边写，避免整体驻留内存）
            with httpx_module.stream(
                "GET",
                video_url,
                headers=req_headers,
                timeout=60,
                follow_redirects=True,
            ) as resp:
                if resp.status_code != 200:
                    skipped += 1
                    continue
                fname = f"{str(uuid.uuid4()).replace('-', '')[:16]}.mp4"
                fpath = videos_dir / fname
                total = 0
                with open(fpath, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                        total += len(chunk)
                        if total > max_video_bytes:
                            raise RuntimeError(
                                f"视频超过大小上限"
                                f"（{max_video_bytes // (1024*1024)}MB），跳过"
                            )
                        f.write(chunk)
                if total == 0:
                    fpath.unlink(missing_ok=True)
                    fpath = None
                    skipped += 1
                    continue

            # ffmpeg 提取首帧缩略图
            thumb_rel = extract_video_thumbnail_sync(fpath, today)

            # 逐条提交（素材行 + 墓碑同一事务）：失败仅回滚当前视频并
            # 删除其文件，不影响本批已入库的视频（同 download_batch 修复）。
            if batch_conn is None:
                raise RuntimeError("数据库连接不可用")
            insp_id = str(uuid.uuid4())
            rel_path = f"videos/{today}/{fname}"
            now_str = utcnow().strftime("%Y-%m-%d %H:%M:%S")
            # 按博主采集：视频同步写入笔记正文 caption 并关联博主
            meta = meta_map.get(note_url) if meta_map else None
            caption_val = (meta or {}).get("caption")
            blogger_id = (meta or {}).get("blogger_id")
            try:
                save_hashtags(batch_conn, meta, note_url)  # 话题存档（幂等，每笔记一次）
                batch_conn.execute(
                    INSERT_INSPIRATION_VIDEO_SQL,
                    (
                        insp_id,
                        "scraper",
                        note_url or video_url,
                        rel_path,
                        thumb_rel,
                        "video",
                        caption_val,
                        task_id,
                        now_str,
                        now_str,
                    ),
                )
                if blogger_id:
                    batch_conn.execute(
                        "INSERT OR IGNORE INTO inspiration_bloggers "
                        "(inspiration_id, blogger_id, confidence) VALUES (?, ?, 1.0)",
                        (insp_id, blogger_id),
                    )
                batch_conn.execute(
                    "INSERT OR IGNORE INTO scraper_seen_urls (source_url) VALUES (?)",
                    (video_url,),
                )
                batch_conn.commit()
            except Exception:
                try:
                    batch_conn.rollback()  # 清除 aborted 事务态（仅当前视频受影响）
                except Exception:
                    pass
                raise

            added += 1
            existing_url_set.add(video_url)
            # 视频较大，下载间隔稍长
            time.sleep(random.uniform(1.0, 2.0))
        except Exception as e:
            try:
                if batch_conn is not None:
                    batch_conn.rollback()
            except Exception:
                pass
            if fpath is not None:
                try:
                    fpath.unlink(missing_ok=True)
                except Exception:
                    pass
            print(
                f"    视频下载失败 {video_url[:40]}..."
                f" ({str(e)[:60]})"
            )
            skipped += 1

    if batch_conn is not None:
        batch_conn.close()

    return added, skipped
