"""小红书爬虫：基于 Playwright sync API + asyncio.to_thread 绕过反爬检测。"""

import asyncio
import json
import logging
import os

from app.scrapers.base import BaseScraper, RawContent

logger = logging.getLogger(__name__)

# Chrome 扩展导出的 sameSite 取值 → Playwright 期望值
_SAME_SITE_MAP = {
    "no_restriction": "None",
    "none": "None",
    "unspecified": "Lax",
    "lax": "Lax",
    "strict": "Strict",
    "": "Lax",
}


def normalize_cookies(raw: list[dict]) -> list[dict]:
    """Chrome 扩展导出格式 → Playwright 兼容格式。

    扩展导出（Cookie-Editor 等）字段与 Playwright 不兼容：
    - sameSite 为 null / no_restriction / unspecified（Playwright 仅接受 Strict/Lax/None）
    - 过期时间字段名 expirationDate（Playwright 为 expires）
    - 携带 hostOnly / session / storeId 等 Playwright 不认识的字段（add_cookies 会报错）

    转换规则：
    - sameSite：no_restriction→None；unspecified/缺失/null→Lax（宽容默认）；
      lax/strict 大小写归一化
    - expirationDate → expires（秒时间戳）
    - 只保留 Playwright 认识的字段，其余丢弃
    """
    normalized: list[dict] = []
    for cookie in raw:
        if not isinstance(cookie, dict) or not cookie.get("name"):
            continue
        item: dict = {
            "name": cookie["name"],
            "value": cookie.get("value") or "",
            "domain": cookie.get("domain") or "",
            "path": cookie.get("path") or "/",
        }
        same_site = cookie.get("sameSite")
        item["sameSite"] = _SAME_SITE_MAP.get(
            str(same_site).lower() if same_site is not None else "", "Lax"
        )
        if cookie.get("expirationDate"):
            item["expires"] = float(cookie["expirationDate"])
        if cookie.get("httpOnly"):
            item["httpOnly"] = True
        if cookie.get("secure"):
            item["secure"] = True
        normalized.append(item)
    return normalized


class XiaohongshuScraper(BaseScraper):
    """小红书平台爬虫 — 使用 sync Playwright 在 threadpool 中运行。"""

    platform = "xiaohongshu"

    def __init__(self, headless: bool = True, cookie_file: str | None = None) -> None:
        self.headless = headless
        self.cookie_file = cookie_file
        self._browser = None
        self._context = None
        self._page = None
        self._pw = None
        self.last_login_error = ""  # 最近一次 Cookie 加载失败原因（无则空串）

    def _ensure_browser_sync(self) -> None:
        """同步初始化浏览器。"""
        if self._browser is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError(
                "未安装 Playwright: pip install playwright && playwright install chromium"
            )
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        self._context = self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
        )
        self._context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
        """)
        self._page = self._context.new_page()

    async def _ensure_browser(self) -> None:
        if self._browser is not None:
            return
        await asyncio.to_thread(self._ensure_browser_sync)

    def _load_cookies_sync(self) -> bool:
        """同步加载 Cookie（Playwright sync API 需与浏览器操作同线程）。"""
        if self.cookie_file and os.path.exists(self.cookie_file):
            try:
                with open(self.cookie_file, encoding="utf-8") as f:
                    cookies = normalize_cookies(json.load(f))
                self._context.add_cookies(cookies)
                logger.info(f"已加载 {len(cookies)} 个 Cookie")
                self.last_login_error = ""
                return True
            except Exception as e:
                # 记录失败原因（供 search/search_users 明确报错，避免静默走到登录墙）
                self.last_login_error = str(e)
                logger.warning(f"Cookie 加载失败: {e}")
        return False

    async def login(self) -> bool:
        await self._ensure_browser()
        return await asyncio.to_thread(self._load_cookies_sync)

    async def search(
        self, keyword: str, count: int = 20
    ) -> list[RawContent]:
        await self._ensure_browser()
        await self.login()

        def _search() -> list[RawContent]:
            results: list[RawContent] = []
            # 关键词 URL 编码：中文/空格/特殊字符直接拼进 URL 会导致请求异常
            from urllib.parse import quote

            search_url = (
                f"https://www.xiaohongshu.com/search_result/"
                f"?keyword={quote(keyword)}&source=web_search_result_notes"
            )
            try:
                logger.info(f"小红书搜索: {keyword}")
                self._page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                import time
                time.sleep(3)
                # 小红书页面结构：使用多种选择器容错
                selectors = [
                    "section.note-item", "div.note-item", ".note-item",
                    "a[href*='explore']", "a[href*='discovery']",
                    "div[class*='note']", "div[class*='card']",
                ]
                cards = []
                for sel in selectors:
                    try:
                        self._page.wait_for_selector(sel, timeout=3000)
                        cards = self._page.query_selector_all(sel)
                        if cards:
                            logger.info(f"使用选择器 '{sel}' 找到 {len(cards)} 条笔记")
                            break
                    except Exception:
                        continue

                if not cards:
                    # 兜底：查找所有包含图片链接的元素
                    cards = self._page.query_selector_all("a[href*='/explore/']")
                    if not cards:
                        cards = self._page.query_selector_all("a[href*='/search_result/']")
                    logger.info(f"兜底选择器找到 {len(cards)} 条")

                for card in cards[:count]:
                    try:
                        # 获取链接
                        href = ""
                        if card.evaluate("el => el.tagName") == "A":
                            href = card.get_attribute("href") or ""
                        else:
                            link_el = card.query_selector("a")
                            if link_el:
                                href = link_el.get_attribute("href") or ""

                        if not href:
                            continue
                        url = f"https://www.xiaohongshu.com{href}" if href.startswith("/") else href

                        # 获取图片
                        img_el = card.query_selector("img")
                        image_urls = []
                        if img_el:
                            src = img_el.get_attribute("src") or img_el.get_attribute("data-src") or ""
                            if src:
                                image_urls.append(src)

                        # 获取文本
                        caption = card.inner_text()[:200] if hasattr(card, 'inner_text') else None

                        # 提取平台 ID
                        platform_id = url.rstrip("/").split("/")[-1].split("?")[0]
                        results.append(RawContent(
                            platform=self.platform,
                            platform_id=platform_id,
                            url=url,
                            image_urls=image_urls,
                            caption=caption,
                        ))
                    except Exception as e:
                        continue
            except Exception as e:
                logger.error(f"小红书搜索失败: {e}")
            return results

        return await asyncio.to_thread(_search)

    async def search_users(self, keyword: str, limit: int = 10) -> list[dict]:
        """按关键词搜索小红书用户（博主主页信息补全用）。

        与 search()（搜索笔记）不同：走搜索页「用户」结果（source=web_search_result_users），
        解析用户卡片主页链接。页面结构变化时容错返回空列表（调用方记录失败原因）。

        返回候选用户列表：[{"name", "profile_url", "platform_user_id"}]。

        注意：Playwright sync API 的 greenlet 绑定创建线程，浏览器初始化/Cookie/
        页面操作必须同一线程执行——本方法整体委托 search_users_sync 在单个
        to_thread 中完成；批量调用方（任务执行器）应使用专用单线程执行器直接
        调用 search_users_sync，避免多次 to_thread 落不同线程触发
        「Cannot switch to a different thread」。
        """
        return await asyncio.to_thread(self.search_users_sync, keyword, limit)

    def search_users_sync(self, keyword: str, limit: int = 10) -> list[dict]:
        """search_users 的同步实现（单线程内完成 浏览器初始化+Cookie+搜索）。

        供任务执行器在专用单线程 executor 中调用，规避 Playwright sync API 的
        线程切换限制（greenlet 绑定创建线程）。
        """
        self._ensure_browser_sync()
        if not self._load_cookies_sync():
            raise RuntimeError(
                f"小红书 Cookie 加载失败: {self.last_login_error or 'Cookie 文件缺失或为空'}"
            )

        results: list[dict] = []
        from urllib.parse import quote

        search_url = (
            "https://www.xiaohongshu.com/search_result/"
            f"?keyword={quote(keyword)}&source=web_search_result_users"
        )
        try:
            logger.info(f"小红书用户搜索: {keyword}")
            self._page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            # 自适应等待（替代固定 sleep）：轮询直到「用户卡片出现 / 结果区渲染
            # 但无卡片（无结果，提前退出）/ 登录墙 / 超时」。结果快时不等满固定时长
            import time

            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                body_text = (self._page.inner_text("body") or "")[:500]
                if "登录后查看搜索结果" in body_text or "手机号登录" in body_text:
                    raise RuntimeError(
                        "小红书未登录（搜索页登录墙拦截），请确认已导入有效 Cookie"
                    )
                if self._page.query_selector("div.user-item-box"):
                    break  # 结果已渲染，立即继续
                if self._page.query_selector("div.search-layout"):
                    # 结果区域已渲染但尚无用户卡片：短暂确认后仍无则判定「无结果」提前退出
                    time.sleep(0.5)
                    if not self._page.query_selector("div.user-item-box"):
                        break
                time.sleep(0.4)
            # 用户卡片容错选择器（按命中率依次尝试）：
            # 优先搜索页「用户」卡片区（user-item-box，卡片内链接才是搜索结果用户），
            # 全局 user/profile 链接兜底（注意会包含笔记卡片的作者链接，需配合
            # 「唯一候选/昵称精确匹配」策略过滤）
            selectors = [
                "div.user-item-box a[href*='/user/profile/']",
                "a[href*='/user/profile/']",
                "a[href^='/user/profile/']",
            ]
            links = []
            for idx, sel in enumerate(selectors):
                try:
                    # 首个选择器（用户卡片区）无结果时必超时，缩短等待减少无效耗时
                    timeout_ms = 1500 if idx == 0 else 3000
                    self._page.wait_for_selector(sel, timeout=timeout_ms)
                    links = self._page.query_selector_all(sel)
                    if links:
                        logger.info(f"用户搜索选择器 '{sel}' 命中 {len(links)} 个")
                        break
                except Exception:
                    continue

            seen: set[str] = set()
            for el in links[:limit]:
                try:
                    href = el.get_attribute("href") or ""
                    if "/user/profile/" not in href:
                        continue
                    user_id = href.rstrip("/").split("/")[-1].split("?")[0]
                    if not user_id or user_id in seen:
                        continue
                    seen.add(user_id)
                    # 卡片文本取昵称（首个非空行）；昵称可能带「小红书号：xxx」
                    # 后缀（同一行或相邻行），截断至「小红书号」前避免噪声混入
                    card_text = (el.inner_text() or "").strip()
                    text = card_text.splitlines()
                    name = next((ln.strip() for ln in text if ln.strip()), "")[:64]
                    name = name.split("小红书号")[0].strip()[:64]
                    # 尝试提取卡片内的小红书号（补全「号匹配」判据用）
                    xhs_id = None
                    import re as _re

                    m = _re.search(r"小红书号[:：]\s*([0-9a-zA-Z_-]+)", card_text)
                    if m:
                        xhs_id = m.group(1)
                    url = (
                        f"https://www.xiaohongshu.com{href}"
                        if href.startswith("/")
                        else href
                    )
                    results.append(
                        {
                            "name": name,
                            "profile_url": url,
                            "platform_user_id": user_id,
                            "xhs_id": xhs_id,
                        }
                    )
                except Exception:
                    continue
        except Exception as e:
            logger.error(f"小红书用户搜索失败: {e}")
            raise
        return results

    async def get_feed(self, count: int = 20) -> list[RawContent]:
        await self._ensure_browser()

        def _get_feed() -> list[RawContent]:
            results: list[RawContent] = []
            import time
            try:
                self._page.goto("https://www.xiaohongshu.com/explore",
                                wait_until="domcontentloaded", timeout=30000)
                time.sleep(3)
                for _ in range(min(count // 10, 5)):
                    self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    time.sleep(2)

                cards = self._page.query_selector_all("section.note-item")
                for card in cards[:count]:
                    try:
                        link_el = card.query_selector("a.cover")
                        if not link_el:
                            continue
                        href = link_el.get_attribute("href")
                        img_el = card.query_selector("img")
                        image_urls = [img_el.get_attribute("src")] if img_el else []
                        platform_id = href.rstrip("/").split("/")[-1] if href else ""
                        url = f"https://www.xiaohongshu.com{href}" if href and href.startswith("/") else href
                        results.append(RawContent(
                            platform=self.platform, platform_id=platform_id,
                            url=url or "", image_urls=image_urls,
                        ))
                    except Exception:
                        continue
            except Exception as e:
                logger.error(f"小红书发现页失败: {e}")
            return results

        return await asyncio.to_thread(_get_feed)

    def close_sync(self) -> None:
        """同步关闭浏览器（供专用单线程 executor 调用，与页面操作同线程）。"""
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass

    async def close(self) -> None:
        await asyncio.to_thread(self.close_sync)
