"""独立爬虫执行脚本 — 通过子进程隔离 Playwright，避免事件循环冲突。

调用方式:
  python scripts/run_scraper.py <task_id>

由 scraper_service 通过 subprocess 调用。
"""

import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# 添加后端路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings
from app.database import async_session
from app.models.inspiration import Inspiration
from app.models.scraper import ScraperTask


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def download_image(url: str, save_dir: Path, referer: str = "") -> tuple[str | None, str | None]:
    """下载图片并返回 (file_path, thumb_path)。"""
    import httpx
    try:
        resp = httpx.get(url, headers={
            "Referer": referer,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }, timeout=30, follow_redirects=True)
        if resp.status_code != 200:
            return None, None
    except Exception:
        return None, None

    content_type = resp.headers.get("content-type", "")
    ext = ".jpg"
    if "png" in content_type:
        ext = ".png"
    elif "webp" in content_type:
        ext = ".webp"

    file_id = str(uuid.uuid4()).replace("-", "")[:16]
    filename = f"{file_id}{ext}"
    filepath = save_dir / filename
    filepath.write_bytes(resp.content)
    return str(filepath), None


def run_scraper_sync(task_id: int):
    """同步执行采集任务（在独立进程中运行）。"""
    from playwright.sync_api import sync_playwright

    # 加载任务
    import asyncio

    async def _load_task():
        async with async_session() as db:
            task = await db.get(ScraperTask, task_id)
            return task

    task = asyncio.run(_load_task())
    if not task or task.status in ("completed", "cancelled"):
        print(f"Task {task_id} already completed/cancelled")
        return

    # 更新状态为运行中
    async def _set_running():
        async with async_session() as db:
            t = await db.get(ScraperTask, task_id)
            if t:
                t.status = "running"
                await db.commit()

    asyncio.run(_set_running())

    config = json.loads(task.config) if isinstance(task.config, str) else task.config or {}
    keywords = config.get("keywords", [])
    max_count = config.get("max_count", 50)
    platform = task.platform

    all_image_urls = []
    pw = None
    browser = None

    try:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=settings.scraper_browser_headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()

        for keyword in keywords:
            if not keyword.strip():
                continue
            per_kw = max(1, max_count // max(len(keywords), 1))

            if platform == "xiaohongshu":
                search_url = f"https://www.xiaohongshu.com/search_result/?keyword={keyword}&source=web_search_result_notes"
            elif platform == "douyin":
                search_url = f"https://www.douyin.com/search/{keyword}?type=general"
            else:
                continue

            print(f"搜索: {keyword} ({search_url})")
            try:
                page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                time.sleep(3)
            except Exception as e:
                print(f"页面加载失败: {e}")
                continue

            # 提取所有图片链接
            img_elements = page.query_selector_all("img")
            for img in img_elements[:per_kw * 3]:
                src = img.get_attribute("src") or img.get_attribute("data-src") or ""
                if src and src.startswith("http") and len(src) > 20:
                    all_image_urls.append(src)

    except Exception as e:
        import traceback
        err = str(e) if str(e) else type(e).__name__
        print(f"采集异常: {err}")
        traceback.print_exc()

        async def _set_failed():
            async with async_session() as db:
                t = await db.get(ScraperTask, task_id)
                if t:
                    t.status = "failed"
                    t.error = err[:500]
                    await db.commit()

        asyncio.run(_set_failed())
        return

    finally:
        try:
            if browser:
                browser.close()
            if pw:
                pw.stop()
        except Exception:
            pass

    # 下载图片并入库
    today = utcnow().strftime("%Y-%m")
    images_dir = settings.images_dir / today
    images_dir.mkdir(parents=True, exist_ok=True)

    items_found = len(all_image_urls)
    items_added = 0

    for img_url in all_image_urls[:max_count]:
        try:
            filepath, _ = download_image(img_url, images_dir)
            if not filepath:
                continue

            rel_path = f"images/{today}/{Path(filepath).name}"
            insp = Inspiration(
                id=str(uuid.uuid4()),
                source_type="scraper",
                source_url=img_url,
                file_path=rel_path,
                media_type="image",
            )

            async def _save(insp=insp):
                async with async_session() as db:
                    db.add(insp)
                    await db.commit()

            asyncio.run(_save(insp))
            items_added += 1
        except Exception as e:
            print(f"下载失败 {img_url}: {e}")

    # 更新任务完成状态
    async def _set_done():
        async with async_session() as db:
            t = await db.get(ScraperTask, task_id)
            if t:
                t.status = "completed"
                t.items_found = items_found
                t.items_added = items_added
                await db.commit()

    asyncio.run(_set_done())
    print(f"任务 {task_id} 完成: found={items_found}, added={items_added}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/run_scraper.py <task_id>")
        sys.exit(1)
    run_scraper_sync(int(sys.argv[1]))
