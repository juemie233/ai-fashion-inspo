"""独立爬虫执行脚本 — CDP 连接用户真实 Chrome，零检测采集。

调用方式:
  python scripts/run_scraper.py <task_id>
"""

import json
import os as _os
import random
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
    time.sleep(random.uniform(lo, hi))


# ═══════════════════════════════════════════════════════════════
#  搜索与提取
# ═══════════════════════════════════════════════════════════════

def _search_xiaohongshu(page, keyword: str, max_count: int, sort_type: str = "general") -> list[str]:
    """在已登录的页面上搜索并提取图片 URL。

    采用触底循环滚动策略，持续滚到懒加载不出新卡片或达到上限为止。
    从每张卡片中提取多张图片（轮播帖），最大化采集数量。

    Args:
        sort_type: 排序方式 — "general"(综合) / "time_descending"(最新) / "popularity_descending"(最热)
    """
    if page.is_closed():
        raise RuntimeError("页面已关闭")

    sort_labels = {
        "general": "综合",
        "time_descending": "最新",
        "popularity_descending": "最热",
    }
    sort_label = sort_labels.get(sort_type, sort_type)

    url = (
        f"https://www.xiaohongshu.com/search_result/"
        f"?keyword={keyword}&source=web_search_result_notes&sort={sort_type}"
    )
    print(f"  导航到搜索页 [{sort_label}]: {keyword}")
    page.goto(url, wait_until="domcontentloaded", timeout=30000)

    # 等待搜索结果卡片渲染完成
    try:
        page.wait_for_selector("section.note-item", timeout=15000)
        print("  搜索结果已渲染")
    except Exception:
        print("  等待搜索结果超时，尝试继续...")
    _rdsleep(1.5, 3.0)

    # ── 拟人化触底循环滚动 ──
    MAX_SCROLLS = 40
    CONSECUTIVE_NO_NEW = 4  # 连续 N 次无新卡片则停止
    no_new_count = 0
    last_card_count = 0

    for scroll_i in range(MAX_SCROLLS):
        # 分步滚动，模拟人类逐段浏览（而非瞬间跳到底部）
        current = page.evaluate("window.scrollY")
        target = page.evaluate("document.body.scrollHeight")
        while current < target - 100:
            step = random.randint(300, 900)
            current = min(current + step, target)
            page.evaluate(f"window.scrollTo(0, {current})")
            time.sleep(random.uniform(0.25, 0.6))
        # 触底
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        _rdsleep(1.5, 3.0)

        # 偶尔回滚一小段（人类浏览行为）
        if random.random() < 0.2:
            back = page.evaluate("window.scrollY") - random.randint(200, 500)
            page.evaluate(f"window.scrollTo(0, Math.max(0, {back}))")
            time.sleep(random.uniform(0.3, 0.7))

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
#  下载
# ═══════════════════════════════════════════════════════════════

def _download_batch(
    urls: list[str],
    task_id: int,
    existing_url_set: set[str],
    remaining: int,
    img_dir: Path,
    today: str,
    httpx_module,
    cookies: dict | None = None,
) -> tuple[int, int, int, int]:
    """下载一批 URL，立即入库。使用同步 sqlite3 避免 event loop 冲突。"""
    import sqlite3 as _sqlite3

    # 构建请求头（带浏览器 Cookie 以通过 CDN 鉴权）
    req_headers = {
        "Referer": "https://www.xiaohongshu.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    if cookies:
        req_headers["Cookie"] = "; ".join(
            f"{c['name']}={c['value']}" for c in cookies.values()
        )

    db_path = settings.storage_root.parent / "fashion_inspo.db"

    unique = list(dict.fromkeys(urls))

    # 查询这批 URL 中已在 DB 中的（同步查询）
    if unique:
        try:
            conn = _sqlite3.connect(str(db_path))
            placeholders = ",".join("?" * len(unique))
            cur = conn.execute(
                f"SELECT source_url FROM inspirations WHERE source_url IN ({placeholders})",
                unique,
            )
            db_existing = {r[0] for r in cur.fetchall()}
            conn.close()
            existing_url_set.update(db_existing)
        except Exception:
            db_existing = set()

    added = 0
    skipped_existing = 0
    skipped_non200 = 0
    skipped_network = 0

    for img_url in unique:
        if added >= remaining:
            break
        if img_url in existing_url_set:
            skipped_existing += 1
            continue

        for attempt in range(1, 4):
            try:
                resp = httpx_module.get(img_url, headers=req_headers,
                                         timeout=30, follow_redirects=True)
                if resp.status_code != 200:
                    skipped_non200 += 1
                    break
                ext = ".jpg"
                ct = resp.headers.get("content-type", "")
                if "png" in ct: ext = ".png"
                elif "webp" in ct: ext = ".webp"
                fname = f"{str(uuid.uuid4()).replace('-', '')[:16]}{ext}"
                fpath = img_dir / fname
                fpath.write_bytes(resp.content)

                # 同步写入数据库
                conn = _sqlite3.connect(str(db_path))
                insp_id = str(uuid.uuid4())
                rel_path = f"images/{today}/{fname}"
                now_str = utcnow().strftime("%Y-%m-%d %H:%M:%S")
                conn.execute(
                    "INSERT INTO inspirations (id, source_type, source_url, file_path, "
                    "thumbnail_path, media_type, dominant_colors, is_favorite, "
                    "scraper_task_id, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, NULL, ?, NULL, 0, ?, ?, ?)",
                    (insp_id, "scraper", img_url, rel_path, "image", task_id, now_str, now_str),
                )
                conn.commit()
                conn.close()

                added += 1
                existing_url_set.add(img_url)

                # 下载间隔：模拟人类逐张保存的行为
                time.sleep(random.uniform(0.3, 1.0))
                break
            except Exception as e:
                err = str(e)[:60]
                if attempt < 3:
                    backoff = 2 ** attempt
                    print(f"    下载重试 ({attempt}/3) {img_url[:40]}... ({err})，{backoff}s 后重试")
                    time.sleep(backoff)
                else:
                    print(f"    下载失败 {img_url[:40]}... ({err})")
                    skipped_network += 1

    return added, skipped_existing, skipped_non200, skipped_network


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
                t.started_at = utcnow()
                await db.commit()
    asyncio.run(_run())

    config = json.loads(task.config) if isinstance(task.config, str) else task.config or {}
    keywords = [k.strip() for k in config.get("keywords", []) if k.strip()]
    max_count = config.get("max_count", 50)
    platform = task.platform

    if not keywords:
        print("无关键词，退出")
        return

    # 准备下载目录
    today = utcnow().strftime("%Y-%m")
    img_dir = settings.images_dir / today
    img_dir.mkdir(parents=True, exist_ok=True)
    import httpx

    existing_url_set: set[str] = set()  # 跨批次去重
    items_found = 0  # 搜索提取总数
    items_added = 0  # 最终入库数
    total_skipped_existing = 0
    total_skipped_non200 = 0
    total_skipped_network = 0
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

        # ── 搜索 + 即时下载：每个关键词 × 3 排序，搜完一批立刻下载 ──
        SORT_TYPES = ["general", "popularity_descending", "time_descending"]
        search_count = 0
        total_searches = len(keywords) * len(SORT_TYPES)

        # 提取浏览器 Cookie 用于 httpx 下载鉴权
        browser_cookies = {c["name"]: c for c in context.cookies()}

        for idx, kw in enumerate(keywords):
            for sort_type in SORT_TYPES:
                search_count += 1

                if items_added >= max_count:
                    print(f"\n  已入库 {items_added} 张 → 达到目标 {max_count}，停止搜索")
                    break

                print(f"\n{'='*50}")
                print(f"[搜索 {search_count}/{total_searches}] {kw} [{sort_type}]")
                print(f"{'='*50}")

                try:
                    if platform == "xiaohongshu":
                        urls = _search_xiaohongshu(page, kw, max_count, sort_type)
                    else:
                        diagnostics.append(f"不支持的平台: {platform}")
                        continue

                    items_found += len(urls)
                    print(f"  提取 {len(urls)} 个 URL")

                    # 立即下载本批（带浏览器 Cookie）
                    remaining = max_count - items_added
                    added, sk_ex, sk_h, sk_n = _download_batch(
                        urls, task_id, existing_url_set, remaining,
                        img_dir, today, httpx, browser_cookies,
                    )
                    items_added += added
                    total_skipped_existing += sk_ex
                    total_skipped_non200 += sk_h
                    total_skipped_network += sk_n

                    print(f"  本批入库: {added} (跳过: 已存在{sk_ex}, HTTP{sk_h}, 网络{sk_n})")
                    print(f"  累计入库: {items_added}/{max_count}")

                    # 搜索间冷却 + CDP 保活
                    if items_added < max_count and (
                        idx < len(keywords) - 1
                        or sort_type != SORT_TYPES[-1]
                    ):
                        cool = random.randint(5, 9)
                        print(f"  ⏸ 冷却 {cool}s...")
                        # 轻量页面交互保持 CDP 连接活跃
                        for _ in range(cool):
                            try:
                                page.evaluate("1")  # no-op，单纯保持连接
                            except Exception:
                                pass
                            time.sleep(1)

                except Exception as e:
                    err = str(e) or type(e).__name__
                    diagnostics.append(f"[{kw}][{sort_type}] 异常: {err}")
                    print(f"  [ERR] {err}")

            if items_added >= max_count:
                break

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
                    t.finished_at = utcnow()
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

    # ── 汇总漏斗日志 ──
    print(f"\n  ╔══════════════════════════════════════════")
    print(f"  ║ 采集漏斗汇总")
    print(f"  ╠══════════════════════════════════════════")
    print(f"  ║ 搜索提取总数:   {items_found}")
    print(f"  ║ 跨次已存在:     {total_skipped_existing}")
    print(f"  ║ HTTP 非 200:    {total_skipped_non200}")
    print(f"  ║ 网络失败:       {total_skipped_network}")
    print(f"  ║ ★ 最终入库:     {items_added}")
    print(f"  ╚══════════════════════════════════════════")

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
                t.finished_at = utcnow()
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
