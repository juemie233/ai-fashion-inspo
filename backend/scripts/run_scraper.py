"""独立爬虫执行脚本 — 通过子进程隔离 Playwright，避免事件循环冲突。

调用方式:
  python scripts/run_scraper.py <task_id>

由 scraper 路由通过 subprocess.Popen 调用。
"""

import json
import os as _os
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


# ── 强制 UTF-8 输出，避免 Windows GBK 编码错误 ──
import io

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _random_delay(min_s: float = 0.5, max_s: float = 2.0):
    """模拟人类操作的随机延迟。"""
    import random
    time.sleep(random.uniform(min_s, max_s))


def download_image(url: str, save_dir: Path, referer: str = "") -> tuple[str | None, str | None]:
    """下载图片并返回 (file_path, thumb_path)。"""
    import httpx

    try:
        resp = httpx.get(
            url,
            headers={
                "Referer": referer or "https://www.xiaohongshu.com/",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            },
            timeout=30,
            follow_redirects=True,
        )
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


def _has_captcha(page) -> bool:
    """检测页面是否出现验证码/滑块。"""
    try:
        html = page.content().lower()
        # 只检测真正的验证码信号，避免误判
        strong_signals = [
            "请完成验证", "拖动下方滑块", "slide to verify",
            "请完成安全验证", "verify you are human",
        ]
        for signal in strong_signals:
            if signal in html:
                return True
        # 检查是否出现了验证码 iframe
        iframe_count = len(page.query_selector_all("iframe[src*='captcha']"))
        if iframe_count > 0:
            return True
        iframe_count = len(page.query_selector_all("iframe[src*='verify']"))
        if iframe_count > 0:
            return True
        return False
    except Exception:
        return False


def _wait_for_captcha_solve(page, timeout: int = 120):
    """等待用户手动完成验证码。"""
    print(f"  [检测到验证码] 请在浏览器中手动完成验证（等待 {timeout} 秒）...")
    for waited in range(0, timeout, 5):
        time.sleep(5)
        if not _has_captcha(page):
            print(f"  [验证码已通过] 继续执行...")
            _random_delay(1, 3)
            return True
    print(f"  [验证码等待超时]")
    return False


def _has_content(page) -> bool:
    """检测页面是否有实际内容（非空白/拦截页）。"""
    try:
        text = page.inner_text().strip()
        # 正常页面应该有足够多的文字
        return len(text) > 100
    except Exception:
        return False


def _extract_images_from_page(page, max_count: int) -> list[str]:
    """从当前页面提取穿搭相关图片链接。"""
    image_urls: list[str] = []
    seen_urls: set[str] = set()

    # 滚动加载更多内容
    for i in range(4):
        page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {(i + 1) / 4})")
        _random_delay(0.8, 1.5)

    # 多策略提取笔记卡片中的封面图
    cards: list = []
    selectors = [
        "section.note-item",
        "div.note-item",
        "[class*='note-item']",
        "a[href*='/explore/']",
        "a[href*='/search_result/']",
        "a[href*='/discovery/item/']",
    ]
    for sel in selectors:
        cards = page.query_selector_all(sel)
        if cards:
            print(f"  使用选择器 '{sel}' 找到 {len(cards)} 个元素")
            break
    else:
        # 兜底：查找所有大尺寸图片
        cards = page.query_selector_all("img")
        print(f"  兜底: 找到 {len(cards)} 个 img 元素")

    for card in cards[: max_count * 2]:
        try:
            # 如果是 <img> 元素直接用，否则查找其内部的 <img>
            tag_name = ""
            try:
                tag_name = card.evaluate("el => el.tagName")
            except Exception:
                pass
            img_el = card if tag_name == "IMG" else card.query_selector("img")
            if not img_el:
                continue

            src = (
                img_el.get_attribute("src")
                or img_el.get_attribute("data-src")
                or img_el.get_attribute("srcset")
                or ""
            )

            # 过滤无效 URL
            if not src or not src.startswith("http"):
                continue

            # 过滤小图标/头像
            skip_keywords = ["icon", "avatar", "logo", "emoji", "favicon", "qr_code"]
            if any(k in src.lower() for k in skip_keywords):
                continue

            # 过滤小尺寸图片
            w = img_el.get_attribute("width") or ""
            h = img_el.get_attribute("height") or ""
            try:
                if w and h and (int(w) < 120 or int(h) < 120):
                    continue
            except ValueError:
                pass

            if src not in seen_urls:
                seen_urls.add(src)
                image_urls.append(src)
        except Exception:
            continue

    print(f"  提取到 {len(image_urls)} 张有效图片")
    return image_urls[:max_count]


def _xiaohongshu_search_from_page(page, keyword: str, max_count: int) -> list[str]:
    """从当前页面提取小红书搜索结果。"""
    if page.is_closed():
        raise RuntimeError("浏览器页面已关闭，无法继续搜索")
    search_url = (
        f"https://www.xiaohongshu.com/search_result/"
        f"?keyword={keyword}&source=web_search_result_notes"
    )
    print(f"  [小红书] 导航: {keyword}")
    page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
    _random_delay(2, 4)

    # 检查验证码
    if _has_captcha(page):
        _wait_for_captcha_solve(page, timeout=120)

    # 检查页面内容
    if not _has_content(page):
        print(f"  [小红书] 页面内容为空，可能被拦截")
        return []

    return _extract_images_from_page(page, max_count)


def _douyin_search_from_page(page, keyword: str, max_count: int) -> list[str]:
    """从当前页面提取抖音搜索结果。"""
    if page.is_closed():
        raise RuntimeError("浏览器页面已关闭，无法继续搜索")
    search_url = f"https://www.douyin.com/search/{keyword}?type=general"
    print(f"  [抖音] 导航: {keyword}")
    page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
    _random_delay(2, 4)

    if not _has_content(page):
        print(f"  [抖音] 页面内容为空，可能被拦截")
        return []

    return _extract_images_from_page(page, max_count)


def run_scraper_sync(task_id: int):
    """同步执行采集任务（在独立进程中运行）。"""
    from playwright.sync_api import sync_playwright

    import asyncio

    # ── 加载任务 ──
    async def _load_task():
        async with async_session() as db:
            return await db.get(ScraperTask, task_id)

    task = asyncio.run(_load_task())
    if not task or task.status in ("completed", "cancelled"):
        print(f"任务 {task_id} 已完成或已取消，跳过")
        return

    # ── 更新状态为运行中 ──
    async def _set_running():
        async with async_session() as db:
            t = await db.get(ScraperTask, task_id)
            if t:
                t.status = "running"
                await db.commit()

    asyncio.run(_set_running())

    config = json.loads(task.config) if isinstance(task.config, str) else task.config or {}
    keywords = [k.strip() for k in config.get("keywords", []) if k.strip()]
    max_count = config.get("max_count", 50)
    headless = config.get("headless", False)
    cookie_file = config.get("cookie_file")
    platform = task.platform

    if not keywords:
        print("没有关键词，退出")
        return

    all_image_urls: list[str] = []
    diagnostics: list[str] = []
    pw = None
    browser = None
    context = None

    # Cookie 路径
    cookies_dir = Path(settings.images_dir).parent / "cookies"
    cookies_dir.mkdir(parents=True, exist_ok=True)
    if not cookie_file:
        cookie_file = str(cookies_dir / f"{platform}_cookies.json")

    try:
        pw = sync_playwright().start()

        # ── 启动浏览器 ──
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-infobars",
            "--disable-dev-shm-usage",
            "--no-sandbox",
        ]
        if headless:
            launch_args.append("--window-size=1280,800")

        # 有头模式尝试用系统 Chrome（指纹更真实），无头模式用 Playwright 自带 Chromium
        if not headless:
            try:
                browser = pw.chromium.launch(channel="chrome", headless=False, args=launch_args)
                print("使用系统 Chrome 浏览器")
            except Exception as e:
                print(f"系统 Chrome 启动失败 ({e})，使用 Playwright Chromium")
                browser = pw.chromium.launch(headless=False, args=launch_args)
        else:
            browser = pw.chromium.launch(headless=True, args=launch_args)

        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            # 模拟真实浏览器的额外 HTTP 头
            extra_http_headers={
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not=A?Brand";v="24"',
                "Sec-Ch-Ua-Platform": '"Windows"',
                "Sec-Ch-Ua-Mobile": "?0",
            },
        )

        # ── 反检测脚本 ──
        context.add_init_script(
            """
            // 隐藏自动化痕迹
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
            Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
            Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
            delete window.__proto__.__proto__.callPhantom;
            window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {} };
            // 覆盖 permissions API
            const origQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (params) => (
                params.name === 'notifications'
                    ? Promise.resolve({ state: Notification.permission })
                    : origQuery(params)
            );
            // 覆盖 chrome.runtime.connect
            if (window.chrome && window.chrome.runtime) {
                window.chrome.runtime.connect = () => ({ disconnect: () => {} });
            }
        """
        )

        # ── 加载已有 Cookie ──
        if _os.path.exists(cookie_file):
            try:
                with open(cookie_file, encoding="utf-8") as f:
                    cookies = json.load(f)
                context.add_cookies(cookies)
                print(f"已加载 {len(cookies)} 个 Cookie")
            except Exception as e:
                print(f"Cookie 加载失败: {e}")

        page = context.new_page()

        # ═══════════════════════════════════════════════════
        #  有头模式：打开首页 → 等待用户登录 → 再开始搜索
        # ═══════════════════════════════════════════════════
        first_kw = keywords[0]
        LOGIN_TIMEOUT = 180

        if not headless:
            # 打开平台首页（非搜索页），让用户自然登录
            if platform == "xiaohongshu":
                home_url = "https://www.xiaohongshu.com/explore"
            else:
                home_url = "https://www.douyin.com"

            print(f"\n{'='*50}")
            print(f" >>> 浏览器窗口已打开，请勿关闭 <<<")
            print(f"1. 点击页面右上角「登录」扫码登录")
            print(f"2. 登录成功后不要做任何操作，等待自动采集")
            print(f"3. 脚本会自动搜索、下载、关闭浏览器")
            print(f"(登录等待 {LOGIN_TIMEOUT}s 超时)")
            print(f"{'='*50}")

            page.goto(home_url, wait_until="domcontentloaded", timeout=30000)
            _random_delay(1, 2)

            # 等待登录
            logged_in = False
            for waited in range(0, LOGIN_TIMEOUT, 8):
                time.sleep(8)
                cookies = context.cookies()
                # 检查真正的登录 Cookie（小红书: web_session / a1, 抖音: sso, passport）
                xhs_cookies = [c for c in cookies if c.get("domain", "").endswith("xiaohongshu.com")]
                dy_cookies = [c for c in cookies if c.get("domain", "").endswith("douyin.com")]

                has_xhs_session = any(c.get("name") in ("web_session", "a1", "acw_tc") for c in xhs_cookies)
                has_dy_session = any(c.get("name") in ("sso", "passport", "sessionid") for c in dy_cookies)

                if (platform == "xiaohongshu" and has_xhs_session) or (platform == "douyin" and has_dy_session):
                    print(f"检测到登录会话 ({waited + 8}s)")
                    logged_in = True
                    time.sleep(2)
                    break

                # 每 40 秒输出一次进度
                if (waited + 8) % 40 == 0:
                    print(f"  等待登录中... ({waited + 8}s / {LOGIN_TIMEOUT}s)")

            if not logged_in:
                print("登录等待超时，将尝试当前状态采集")

        # ═══════════════════════════════════════════════════
        #  逐个关键词搜索
        # ═══════════════════════════════════════════════════
        for idx, keyword in enumerate(keywords):
            print(f"\n{'='*50}")
            print(f"关键词 [{idx + 1}/{len(keywords)}]: {keyword} (平台: {platform})")
            print(f"{'='*50}")

            try:
                # 始终导航，不使用 skip_nav
                if platform == "xiaohongshu":
                    urls = _xiaohongshu_search_from_page(page, keyword, max_count)
                elif platform == "douyin":
                    urls = _douyin_search_from_page(page, keyword, max_count)
                else:
                    diagnostics.append(f"不支持的平台: {platform}")
                    continue

                if urls:
                    all_image_urls.extend(urls)
                    print(f"  [成功] 关键词「{keyword}」获取到 {len(urls)} 张图片")
                else:
                    msg = f"「{keyword}」搜索返回 0 条结果（可能原因: 需要登录 / 验证码拦截 / 页面结构变更）"
                    diagnostics.append(msg)
                    print(f"  [失败] {msg}")

            except Exception as e:
                err = str(e) if str(e) else type(e).__name__
                diagnostics.append(f"「{keyword}」异常: {err}")
                print(f"  [异常] {err}")

    except Exception as e:
        import traceback

        err = str(e) if str(e) else type(e).__name__
        print(f"浏览器启动失败: {err}")
        traceback.print_exc()

        async def _set_failed():
            async with async_session() as db:
                t = await db.get(ScraperTask, task_id)
                if t:
                    t.status = "failed"
                    t.error = f"浏览器启动失败: {err[:400]}"
                    await db.commit()

        asyncio.run(_set_failed())
        return

    finally:
        # 保存 Cookie
        if browser and context:
            try:
                cookies = context.cookies()
                if cookies:
                    with open(cookie_file, "w", encoding="utf-8") as f:
                        json.dump(cookies, f, ensure_ascii=False)
                    print(f"已保存 {len(cookies)} 个 Cookie")
            except Exception:
                pass
        try:
            if browser:
                browser.close()
            if pw:
                pw.stop()
        except Exception:
            pass

    # ── 兜底诊断 ──
    if not all_image_urls and not diagnostics:
        diagnostics.append("所有关键词均未返回结果（页面加载失败或结构变更）")

    # ── 下载图片并入库 ──
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
            print(f"下载失败 {img_url[:80]}...: {e}")

    # ── 更新任务状态 ──
    error_msg = None
    if items_found == 0 and diagnostics:
        error_msg = " | ".join(diagnostics)[:500]

    async def _set_done():
        async with async_session() as db:
            t = await db.get(ScraperTask, task_id)
            if t:
                t.status = "completed"
                t.items_found = items_found
                t.items_added = items_added
                if error_msg:
                    t.error = error_msg
                await db.commit()

    asyncio.run(_set_done())
    print(f"\n任务 {task_id} 完成: found={items_found}, added={items_added}")
    if error_msg:
        print(f"诊断: {error_msg}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/run_scraper.py <task_id>")
        sys.exit(1)
    run_scraper_sync(int(sys.argv[1]))
