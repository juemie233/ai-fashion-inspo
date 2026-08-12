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
        text = page.evaluate("() => document.body.innerText")
        return len(text) > 100
    except Exception:
        return False


def _extract_xiaohongshu_state(page) -> list[str]:
    """从 Vue SSR 的 window.__INITIAL_STATE__ 提取笔记封面图 URL。

    小红书是 Vue 服务端渲染，搜索/发现页面的笔记数据都在 __INITIAL_STATE__ 中。
    这比 DOM 解析可靠得多，不受选择器变化影响。
    """
    try:
        state = page.evaluate("""
            () => {
                const s = window.__INITIAL_STATE__;
                if (!s) return [];
                const images = [];
                // 搜索结果的笔记列表
                const notes = s?.search?.notes || s?.note?.notes || [];
                notes.forEach(n => {
                    const cover = n?.cover?.url || n?.cover?.url_default || '';
                    if (cover) images.push(cover);
                    // 多图笔记
                    (n?.images_list || []).forEach(img => {
                        if (img?.url) images.push(img.url);
                        if (img?.url_size_large) images.push(img.url_size_large);
                    });
                });
                // 瀑布流推荐
                (s?.feed?.items || []).forEach(item => {
                    const cover = item?.note_card?.cover?.url || item?.cover?.url || '';
                    if (cover) images.push(cover);
                });
                // 水合后的 note 数据
                Object.values(s?.note?.noteDetailMap || {}).forEach((n) => {
                    const cover = n?.note?.cover?.url || '';
                    if (cover) images.push(cover);
                    (n?.note?.image_list || []).forEach(img => {
                        if (img?.url) images.push(img.url);
                        if (img?.url_size_large) images.push(img.url_size_large);
                        if (img?.info_list) {
                            img.info_list.forEach(info => {
                                if (info?.url) images.push(info.url);
                            });
                        }
                    });
                });
                return [...new Set(images)];
            }
        """)
        if state and isinstance(state, list) and len(state) > 0:
            # 处理协议相对 URL
            fixed = []
            for u in state:
                if u.startswith("//"):
                    u = "https:" + u
                if u.startswith("http"):
                    fixed.append(u)
            return fixed
    except Exception as e:
        print(f"  __INITIAL_STATE__ 提取失败: {e}")
    return []


def _extract_images_from_page(page, max_count: int) -> list[str]:
    """从当前页面提取穿搭相关图片链接。优先使用 __INITIAL_STATE__。"""
    image_urls: list[str] = []
    seen_urls: set[str] = set()

    # ── 优先：从 Vue SSR 状态提取 ──
    state_images = _extract_xiaohongshu_state(page)
    if state_images:
        print(f"  [INITIAL_STATE] 提取到 {len(state_images)} 张图片")
        # 过滤低质量 URL
        skip_kw = ["icon", "avatar", "logo", "emoji", "favicon", "qr_code", "trace", "ad"]
        for u in state_images:
            if not any(k in u.lower() for k in skip_kw):
                # 优先保留高清大图
                if u not in seen_urls:
                    seen_urls.add(u)
                    image_urls.append(u)
        if len(image_urls) >= max_count:
            return image_urls[:max_count]

    # ── 回退：DOM 选择器提取 ──
    print(f"  INITIAL_STATE 仅获取 {len(image_urls)} 张，使用 DOM 补充...")
    for i in range(4):
        page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {(i + 1) / 4})")
        _random_delay(0.8, 1.5)

    cards: list = []
    selectors = [
        "section.note-item",
        "div.note-item",
        "[class*='note-item']",
        "a[href*='/explore/']",
        "a[href*='/search_result/']",
    ]
    for sel in selectors:
        cards = page.query_selector_all(sel)
        if cards:
            print(f"  DOM 选择器 '{sel}' 找到 {len(cards)} 个元素")
            break
    else:
        cards = page.query_selector_all("img")
        print(f"  DOM 兜底: 找到 {len(cards)} 个 img")

    for card in cards[: max_count * 2]:
        try:
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
            if not src or not src.startswith("http"):
                continue
            skip_keywords = ["icon", "avatar", "logo", "emoji", "favicon", "qr_code"]
            if any(k in src.lower() for k in skip_keywords):
                continue
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

    print(f"  总共提取到 {len(image_urls)} 张有效图片")
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

    # 直接提取（不再用 _has_content 提前判断，因为 __INITIAL_STATE__ 可能无数据但 DOM 有）
    return _extract_images_from_page(page, max_count)


def _douyin_search_from_page(page, keyword: str, max_count: int) -> list[str]:
    """从当前页面提取抖音搜索结果。"""
    if page.is_closed():
        raise RuntimeError("浏览器页面已关闭，无法继续搜索")
    search_url = f"https://www.douyin.com/search/{keyword}?type=general"
    print(f"  [抖音] 导航: {keyword}")
    page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
    _random_delay(2, 4)

    # 直接提取
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
    cdp_port = config.get("cdp_port")            # CDP 端口，连接真实 Chrome
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

    cdp_mode = cdp_port is not None

    try:
        pw = sync_playwright().start()

        # ═══════════════════════════════════════════════════════
        #  方式 A: CDP 连接真实 Chrome（终极反检测方案）
        # ═══════════════════════════════════════════════════════
        if cdp_mode:
            cdp_url = f"http://localhost:{cdp_port}"
            print(f"通过 CDP 连接到真实 Chrome: {cdp_url}")
            browser = pw.chromium.connect_over_cdp(cdp_url)
            # 使用 Chrome 的默认上下文（已包含用户登录态和真实指纹）
            contexts = browser.contexts
            if not contexts:
                raise RuntimeError("CDP 连接成功但无浏览器上下文，请确保 Chrome 已打开页面")
            context = contexts[0]
            # 创建新页面用于采集（不干扰用户现有页面）
            page = context.new_page()
            print(f"CDP 连接成功，创建新标签页用于采集")

        # ═══════════════════════════════════════════════════════
        #  方式 B: 启动新浏览器（原有逻辑）
        # ═══════════════════════════════════════════════════════
        else:
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-infobars",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ]
            if headless:
                launch_args.append("--window-size=1280,800")

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
                extra_http_headers={
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not=A?Brand";v="24"',
                    "Sec-Ch-Ua-Platform": '"Windows"',
                    "Sec-Ch-Ua-Mobile": "?0",
                },
            )

            page = context.new_page()

        # ── 反检测脚本（CDP 模式跳过，真实浏览器无需伪装）──
        if not cdp_mode:
            context.add_init_script(
            """
            // === 基础自动化标记 ===
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            delete window.__proto__.__proto__.callPhantom;
            delete window.__proto__.__proto__._phantom;

            // === navigator 属性 ===
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
            Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
            Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
            Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
            Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0 });

            // === chrome 对象 ===
            window.chrome = {
                runtime: {},
                loadTimes: function() {},
                csi: function() {},
                app: {}
            };

            // === permissions ===
            const origQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (params) => (
                params.name === 'notifications'
                    ? Promise.resolve({ state: Notification.permission })
                    : origQuery(params)
            );

            // === media devices ===
            if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {
                const origEnum = navigator.mediaDevices.enumerateDevices;
                navigator.mediaDevices.enumerateDevices = () =>
                    origEnum.call(navigator.mediaDevices).then(list =>
                        list.map(d => Object.assign({}, d, { label: '', deviceId: '' }))
                    );
            }

            // === WebGL vendor 伪装 ===
            const getParam = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(p) {
                if (p === 37445) return 'Intel Inc.';
                if (p === 37446) return 'Intel Iris OpenGL Engine';
                return getParam.call(this, p);
            };

            // === 覆盖 iframe contentWindow（防止嵌套检测） ===
            try {
                const origDefine = Object.defineProperty;
                Object.defineProperty = function(obj, prop, desc) {
                    if (prop === 'contentWindow' && desc && desc.get && desc.get.toString().includes('native')) {
                        return obj;
                    }
                    return origDefine.call(Object, obj, prop, desc);
                };
            } catch(e) {}
        """
        )

        # ── 加载已有 Cookie（CDP 模式跳过，真实 Chrome 已有 Cookie）──
        if not cdp_mode and _os.path.exists(cookie_file):
            try:
                with open(cookie_file, encoding="utf-8") as f:
                    cookies = json.load(f)
                context.add_cookies(cookies)
                print(f"已加载 {len(cookies)} 个 Cookie")
            except Exception as e:
                print(f"Cookie 加载失败: {e}")

        # ═══════════════════════════════════════════════════
        #  等待登录：CDP 模式或有头模式都需要确保已登录
        #  只有无头模式（已有 Cookie）跳过
        # ═══════════════════════════════════════════════════
        first_kw = keywords[0]
        LOGIN_TIMEOUT = 180

        # 判断是否需要登录检测：
        # CDP 模式：连接到真实 Chrome，用户可能未登录 → 需要检查
        # 有头模式：新浏览器窗口，用户需要登录 → 需要检查
        # 无头模式：后台运行，依赖已有 Cookie → 跳过
        need_login_check = (cdp_mode or not headless)

        if need_login_check:
            # 先检查当前是否已登录
            cookies = context.cookies()
            xhs_cookies = [c for c in cookies if c.get("domain", "").endswith("xiaohongshu.com")]
            dy_cookies = [c for c in cookies if c.get("domain", "").endswith("douyin.com")]
            has_xhs_session = any(c.get("name") in ("web_session", "a1", "acw_tc") for c in xhs_cookies)
            has_dy_session = any(c.get("name") in ("sso", "passport", "sessionid") for c in dy_cookies)
            already_logged_in = (platform == "xiaohongshu" and has_xhs_session) or (platform == "douyin" and has_dy_session)

            if not already_logged_in:
                print(f"\n{'='*50}")
                if cdp_mode:
                    print(f" >>> 请在 Chrome 中手动打开小红书并登录 <<<")
                    print(f" 在地址栏输入 xiaohongshu.com 并扫码登录")
                    print(f" 登录后不要关闭页面，脚本自动检测继续")
                else:
                    print(f" >>> 浏览器窗口已打开，请勿关闭 <<<")
                    print(f" 1. 点击页面右上角「登录」扫码登录")
                    print(f" 2. 登录成功后等待自动采集")
                print(f"(等待 {LOGIN_TIMEOUT}s 超时)")
                print(f"{'='*50}")

                logged_in = False
                for waited in range(0, LOGIN_TIMEOUT, 5):
                    time.sleep(5)
                    cookies = context.cookies()
                    xhs_cookies = [c for c in cookies if c.get("domain", "").endswith("xiaohongshu.com")]
                    has_xhs_session = any(c.get("name") in ("web_session", "a1", "acw_tc") for c in xhs_cookies)
                    dy_cookies = [c for c in cookies if c.get("domain", "").endswith("douyin.com")]
                    has_dy_session = any(c.get("name") in ("sso", "passport", "sessionid") for c in dy_cookies)

                    if (platform == "xiaohongshu" and has_xhs_session) or (platform == "douyin" and has_dy_session):
                        print(f"检测到登录会话 ({waited + 5}s)")
                        logged_in = True
                        time.sleep(1)
                        break

                    if (waited + 5) % 30 == 0:
                        print(f"  等待登录... ({waited + 5}s / {LOGIN_TIMEOUT}s)")

                if not logged_in:
                    print("登录等待超时，将尝试当前状态采集")
            else:
                print("已检测到登录会话，直接开始采集")

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
        # CDP 模式不关闭浏览器（是用户的真实 Chrome）
        if not cdp_mode:
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
