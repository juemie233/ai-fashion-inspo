"""采集服务：编排采集任务、下载图片、入库、触发 AI 分析。"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.config import settings
from app.database import async_session, get_db
from app.models.inspiration import AIAnalysisLog, Inspiration
from app.models.scraper import ScraperTask

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    """返回当前 UTC 时间。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def run_scraper_task(task_id: int):
    """执行采集任务：加载配置 → 运行爬虫 → 下载图片 → 入库 → 触发 AI 分析。"""
    from app.services.file_service import generate_thumbnail

    async with async_session() as db:
        task = await db.get(ScraperTask, task_id)
        if not task:
            logger.error(f"采集任务 {task_id} 未找到")
            return
        if task.status in ("completed", "cancelled"):
            return

        task.status = "running"
        await db.commit()

    config = json.loads(task.config) if isinstance(task.config, str) else task.config or {}
    keywords = config.get("keywords", [])
    max_count = config.get("max_count", 50)
    platform = task.platform

    # 初始化爬虫
    scraper = None
    try:
        if platform == "xiaohongshu":
            from app.scrapers.xiaohongshu import XiaohongshuScraper
            scraper = XiaohongshuScraper(
                headless=settings.scraper_browser_headless,
                cookie_file=config.get("cookie_file"),
            )
        elif platform == "douyin":
            from app.scrapers.douyin import DouyinScraper
            scraper = DouyinScraper(headless=settings.scraper_browser_headless)
        else:
            raise ValueError(f"不支持的平台: {platform}")

        # 搜索关键词
        all_content = []
        for keyword in keywords:
            if not keyword.strip():
                continue
            per_kw = max(1, max_count // max(len(keywords), 1))
            results = await scraper.search(keyword.strip(), count=per_kw)
            all_content.extend(results)
            await asyncio.sleep(settings.scraper_request_delay)

        task.items_found = len(all_content)

        if not all_content:
            task.status = "completed"
            task.items_added = 0
            async with async_session() as db:
                await db.merge(task)
                await db.commit()
            return

        # 下载图片并入库
        items_added = 0
        today = utcnow().strftime("%Y-%m")
        images_dir = settings.images_dir / today
        images_dir.mkdir(parents=True, exist_ok=True)

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as http:
            for content in all_content:
                try:
                    for img_url in content.image_urls:
                        if not img_url:
                            continue
                        # 下载图片
                        try:
                            resp = await http.get(img_url, headers={
                                "Referer": content.url,
                                "User-Agent": (
                                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                    "AppleWebKit/537.36"
                                ),
                            })
                            if resp.status_code != 200:
                                continue
                        except Exception:
                            continue

                        # 确定扩展名
                        content_type = resp.headers.get("content-type", "")
                        ext = ".jpg"
                        if "png" in content_type:
                            ext = ".png"
                        elif "webp" in content_type:
                            ext = ".webp"
                        elif "gif" in content_type:
                            ext = ".gif"

                        # 保存文件
                        file_id = str(uuid.uuid4()).replace("-", "")[:16]
                        filename = f"{file_id}{ext}"
                        filepath = images_dir / filename
                        filepath.write_bytes(resp.content)

                        # 生成缩略图
                        rel_path = f"images/{today}/{filename}"
                        thumb_path = None
                        try:
                            thumb_path = await generate_thumbnail(filepath)
                        except Exception:
                            pass

                        # 入库
                        insp = Inspiration(
                            id=str(uuid.uuid4()),
                            source_type="scraper",
                            source_url=content.url,
                            source_author=content.author,
                            source_platform_id=content.platform_id,
                            file_path=rel_path,
                            thumbnail_path=thumb_path,
                            media_type="image",
                        )

                        async with async_session() as db:
                            db.add(insp)
                            await db.commit()

                        items_added += 1
                        break  # 每个笔记只取一张图

                except Exception as e:
                    logger.warning(f"下载图片失败 {content.url}: {e}")
                    continue

        task.items_added = items_added
        task.status = "completed"

    except Exception as e:
        import traceback
        err_msg = str(e) if str(e) else f"{type(e).__name__}"
        logger.error(f"采集任务 {task_id} 失败: {err_msg}\n{traceback.format_exc()}")
        task.status = "failed"
        task.error = err_msg[:500]

    finally:
        if scraper:
            try:
                await scraper.close()
            except Exception:
                pass
        async with async_session() as db:
            await db.merge(task)
            await db.commit()
