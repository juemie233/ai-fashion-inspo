"""独立爬虫执行脚本 — CDP 连接用户真实 Chrome，零检测采集。

调用方式:
  python scripts/run_scraper.py <task_id>
"""

import json
import os as _os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings
from app.database import async_session
from app.models.inspiration import Inspiration
from app.models.scraper import ScraperTask

# ── UTF-8 输出 ──
import io
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _rdsleep(lo=0.5, hi=2.0):
    import random
    time.sleep(random.uniform(lo, hi))


# ═══════════════════════════════════════════════════════════════
#  搜索与提取
# ═══════════════════════════════════════════════════════════════

def _search_xiaohongshu(page, keyword: str, max_count: int) -> list[str]:
    """在已登录的页面上搜索并提取图片 URL。

    采用触底循环滚动策略，持续滚到懒加载不出新卡片或达到上限为止。
    从每张卡片中提取多张图片（轮播帖），最大化采集数量。
    """
    if page.is_closed():
        raise RuntimeError("页面已关闭")

    url = f"https://www.xiaohongshu.com/search_result/?keyword={keyword}&source=web_search_result_notes"
    print(f"  导航到搜索页: {keyword}")
    page.goto(url, wait_until="domcontentloaded", timeout=30000)

    # 等待搜索结果卡片渲染完成
    try:
        page.wait_for_selector("section.note-item", timeout=15000)
        print("  搜索结果已渲染")
    except Exception:
        print("  等待搜索结果超时，尝试继续...")
    _rdsleep(1.5, 3.0)

    # ── 触底循环滚动 ──
    MAX_SCROLLS = 30
    CONSECUTIVE_NO_NEW = 3  # 连续 N 次无新卡片则停止
    no_new_count = 0
    last_card_count = 0

    for scroll_i in range(MAX_SCROLLS):
        # 滚动到页面底部
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        _rdsleep(1.0, 2.0)

        # 检查是否有新内容加载
        cards_now = len(page.query_selector_all("section.note-item"))

        if cards_now == last_card_count:
            no_new_count += 1
        else:
            no_new_count = 0
            last_card_count = cards_now

        # 已获取足够卡片，提前停止
        target_cards = max_count * 3  # 每卡片可能有 0~N 张图，多取一些卡片保证数量
        if cards_now >= target_cards:
            print(f"  滚动 {scroll_i + 1} 次后已获取 {cards_now} 个卡片（目标 {target_cards}），停止滚动")
            break

        # 连续无新内容，页面已到底
        if no_new_count >= CONSECUTIVE_NO_NEW:
            print(f"  连续 {CONSECUTIVE_NO_NEW} 次无新内容，页面已到底（{cards_now} 卡片）")
            break

    # ── DOM 提取 ──
    cards = page.query_selector_all("section.note-item")
    total_cards = len(cards)
    print(f"  共找到 {total_cards} 个笔记卡片，开始提取图片...")

    image_urls: list[str] = []
    seen: set[str] = set()
    cards_with_img = 0
    cards_without_img = 0
    skipped_small = 0
    skipped_icon = 0

    for card in cards[: max_count * 4]:
        try:
            # 从每张卡片中提取所有图片（轮播帖含多图）
            imgs = card.query_selector_all("img")
            if not imgs:
                cards_without_img += 1
                continue
            cards_with_img += 1

            for img in imgs:
                src = img.get_attribute("src") or img.get_attribute("data-src") or ""
                if not src or not src.startswith("http"):
                    continue
                # 过滤图标类 URL
                if any(k in src.lower() for k in ["icon", "avatar", "logo", "favicon", "emoji"]):
                    skipped_icon += 1
                    continue
                # 过滤小尺寸（< 200px 任意边，比之前的 100px 更严格但不影响数量）
                w = img.get_attribute("width") or ""
                h = img.get_attribute("height") or ""
                try:
                    if w and h and (int(w) < 100 or int(h) < 100):
                        skipped_small += 1
                        continue
                except ValueError:
                    pass
                if src not in seen:
                    seen.add(src)
                    image_urls.append(src)
        except Exception:
            continue

    # ── 漏斗日志 ──
    print(f"  ┌─ 提取漏斗 ─────────────────────────────")
    print(f"  │ DOM 卡片总数: {total_cards}")
    print(f"  │ 有图片的卡片: {cards_with_img}")
    print(f"  │ 无图片的卡片: {cards_without_img}")
    print(f"  │ 跳过小尺寸:   {skipped_small}")
    print(f"  │ 跳过图标:     {skipped_icon}")
    print(f"  │ 提取到 URL:   {len(image_urls)}")
    print(f"  │ 目标数量:     {max_count}")
    print(f"  └──────────────────────────────────────────")

    return image_urls[:max_count * 2]  # 多返回一些，下载阶段有重试和丢弃


# ═══════════════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════════════

def run_scraper_sync(task_id: int):
    from playwright.sync_api import sync_playwright
    import asyncio

    # ── 加载任务 ──
    async def _load():
        async with async_session() as db:
            return await db.get(ScraperTask, task_id)
    task = asyncio.run(_load())
    if not task or task.status in ("completed", "cancelled"):
        print(f"任务 {task_id} 已完结，跳过")
        return

    # ── 设为运行中 ──
    async def _run():
        async with async_session() as db:
            t = await db.get(ScraperTask, task_id)
            if t:
                t.status = "running"
                await db.commit()
    asyncio.run(_run())

    config = json.loads(task.config) if isinstance(task.config, str) else task.config or {}
    keywords = [k.strip() for k in config.get("keywords", []) if k.strip()]
    max_count = config.get("max_count", 50)
    platform = task.platform

    if not keywords:
        print("无关键词，退出")
        return

    all_urls: list[str] = []
    diagnostics: list[str] = []

    try:
        pw = sync_playwright().start()

        # ── 唯一路径：连接 CDP Chrome ──
        CDP_PORT = 9222
        cdp_url = f"http://localhost:{CDP_PORT}"
        print(f"连接 CDP Chrome: {cdp_url}")

        try:
            browser = pw.chromium.connect_over_cdp(cdp_url)
        except Exception as e:
            chrome_exe = settings.chrome_executable
            data_dir = settings.chrome_user_data_dir
            raise RuntimeError(
                f"无法连接 CDP Chrome (端口 {CDP_PORT})。\n"
                f"请先用调试模式启动 Chrome:\n"
                f'"{chrome_exe}" '
                f"--remote-debugging-port={CDP_PORT} "
                f'--user-data-dir="{data_dir}"'
            ) from e

        print(f"已连接 Chrome {browser.version}")
        context = browser.contexts[0]

        # 创建新标签页用于采集
        page = context.new_page()

        # ── 登录检查 ──
        LOGIN_TIMEOUT = 180

        def _check_login() -> bool:
            cookies = context.cookies()
            xhs = [c for c in cookies if "xiaohongshu" in c.get("domain", "")]
            return any(c.get("name") in ("web_session", "a1") for c in xhs)

        if _check_login():
            print("已在登录状态，直接开始采集")
        else:
            print(f"\n{'='*50}")
            print(" >>> 请在 Chrome 中登录小红书 <<<")
            print(" 在地址栏输入 xiaohongshu.com，扫码登录")
            print(f" 登录完成后脚本自动检测并继续（{LOGIN_TIMEOUT}s 超时）")
            print(f"{'='*50}")

            for waited in range(0, LOGIN_TIMEOUT, 5):
                time.sleep(5)
                if _check_login():
                    print(f"检测到登录 ({waited + 5}s)")
                    time.sleep(1)
                    break
                if (waited + 5) % 30 == 0:
                    print(f"  等待登录... ({waited + 5}s / {LOGIN_TIMEOUT}s)")
            else:
                print("登录超时，将尝试当前状态")

        # ── 搜索 ──
        for idx, kw in enumerate(keywords):
            print(f"\n{'='*50}")
            print(f"[{idx + 1}/{len(keywords)}] {kw}")
            print(f"{'='*50}")

            try:
                if platform == "xiaohongshu":
                    urls = _search_xiaohongshu(page, kw, max_count)
                else:
                    diagnostics.append(f"不支持的平台: {platform}")
                    continue

                if urls:
                    all_urls.extend(urls)
                    print(f"  [OK] 获取到 {len(urls)} 张图片")
                else:
                    msg = f"[{kw}] 0 条结果（未找到笔记卡片，可能需登录或页面结构变更）"
                    diagnostics.append(msg)
                    print(f"  [FAIL] {msg}")
            except Exception as e:
                err = str(e) or type(e).__name__
                diagnostics.append(f"[{kw}] 异常: {err}")
                print(f"  [ERR] {err}")

    except Exception as e:
        import traceback
        err = str(e) or type(e).__name__
        print(f"采集失败: {err}")
        traceback.print_exc()

        async def _fail():
            async with async_session() as db:
                t = await db.get(ScraperTask, task_id)
                if t:
                    t.status = "failed"
                    t.error = str(e)[:500]
                    await db.commit()
        asyncio.run(_fail())
        return

    finally:
        # CDP 模式不关浏览器
        try:
            if 'pw' in dir() and pw:
                pw.stop()
        except Exception:
            pass

    # ── 下载图片 ──
    today = utcnow().strftime("%Y-%m")
    img_dir = settings.images_dir / today
    img_dir.mkdir(parents=True, exist_ok=True)

    items_found = len(all_urls)
    items_added = 0
    download_failed = 0
    download_skipped_non200 = 0
    download_skipped_network = 0
    import httpx

    # 去重（不同 URL 可能指向同一图片）
    unique_urls = list(dict.fromkeys(all_urls))  # 保序去重
    if len(unique_urls) < len(all_urls):
        print(f"  去重: {len(all_urls)} → {len(unique_urls)} URL")

    for img_url in unique_urls:
        if items_added >= max_count:
            break

        last_error = None
        downloaded = False
        MAX_RETRIES = 3

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = httpx.get(img_url, headers={
                    "Referer": "https://www.xiaohongshu.com/",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                }, timeout=30, follow_redirects=True)
                if resp.status_code != 200:
                    last_error = f"HTTP {resp.status_code}"
                    download_skipped_non200 += 1
                    break  # 非 200 不重试，CDN 拒绝重试也没用
                ext = ".jpg"
                ct = resp.headers.get("content-type", "")
                if "png" in ct: ext = ".png"
                elif "webp" in ct: ext = ".webp"
                fname = f"{str(uuid.uuid4()).replace('-', '')[:16]}{ext}"
                fpath = img_dir / fname
                fpath.write_bytes(resp.content)

                async def _save(path=str(fpath)):
                    async with async_session() as db:
                        insp = Inspiration(
                            id=str(uuid.uuid4()),
                            source_type="scraper",
                            source_url=img_url,
                            file_path=f"images/{today}/{Path(path).name}",
                            media_type="image",
                        )
                        db.add(insp)
                        await db.commit()
                asyncio.run(_save())
                items_added += 1
                downloaded = True
                break  # 成功，跳出重试循环
            except Exception as e:
                last_error = str(e)[:80]
                if attempt < MAX_RETRIES:
                    backoff = 2 ** attempt  # 2s, 4s
                    print(f"    下载重试 ({attempt}/{MAX_RETRIES}) {img_url[:50]}... ({last_error})，{backoff}s 后重试")
                    time.sleep(backoff)
                else:
                    print(f"    下载失败 {img_url[:50]}... ({last_error})")
                    download_failed += 1
                    download_skipped_network += 1

    # ── 下载漏斗日志 ──
    print(f"  ┌─ 下载漏斗 ─────────────────────────────")
    print(f"  │ 提取 URL 总数:  {items_found}")
    print(f"  │ 去重后 URL:     {len(unique_urls)}")
    print(f"  │ 下载成功:       {items_added}")
    print(f"  │ HTTP 非 200:    {download_skipped_non200}")
    print(f"  │ 网络失败:       {download_skipped_network}")
    print(f"  │ 最终入库:       {items_added}")
    print(f"  └──────────────────────────────────────────")

    # ── 完成任务 ──
    error_msg = None
    if items_found == 0 and diagnostics:
        error_msg = " | ".join(diagnostics)[:500]

    async def _done():
        async with async_session() as db:
            t = await db.get(ScraperTask, task_id)
            if t:
                t.status = "completed"
                t.items_found = items_found
                t.items_added = items_added
                if error_msg:
                    t.error = error_msg
                await db.commit()
    asyncio.run(_done())

    print(f"\n任务 {task_id} 完成: found={items_found}, added={items_added}")
    if error_msg:
        print(f"诊断: {error_msg}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/run_scraper.py <task_id>")
        sys.exit(1)
    run_scraper_sync(int(sys.argv[1]))
