"""小红书爬虫：基于 Playwright sync API + asyncio.to_thread 绕过反爬检测。"""

import asyncio
import json
import logging
import os
import time

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
                # 记录失败原因（供 search 明确报错，避免静默走到登录墙）
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

    def list_following_sync(self, max_pages: int = 3) -> list[dict]:
        """拉取「我关注的用户」列表（同步；供专用单线程 executor 调用）。

        为什么是关注列表而不是「用户搜索」：实测小红书用户搜索**无法按「小红书号」定位
        用户**——搜 `1036376990` 返回的是名称相近的无关用户（15 张卡片里没有本人，
        且卡片内没有主页链接可点），因此「按小红书号搜索 → 唯一候选/昵称匹配」这条路
        只会稳定地判成「无法唯一确认」。而关注列表接口一次请求就能拿到全部关注账号的
        uid：素材库的博主正是从这份关注列表导入的，昵称可一一对应
        （实测 281 人里能对上 27/30 个待补全博主，且访问 uid 主页确认是同一个人）。

        Returns:
            [{"nickname", "uid", ...}]（解析复用 scripts/fetch_xhs_following）；
            未加载 Cookie 时抛 RuntimeError，接口异常向上抛（由调用方决定是否降级）。
        """
        self._ensure_browser_sync()
        if not self._load_cookies_sync():
            raise RuntimeError(
                f"小红书 Cookie 加载失败: {self.last_login_error or 'Cookie 文件缺失或为空'}"
            )
        from scripts import fetch_xhs_following as fx

        logger.info("小红书：拉取关注列表（按昵称解析博主 uid）")
        # 关注列表接口在页面上下文里 fetch（复用登录态）：先落到站内页面再取值
        self._page.goto(
            "https://www.xiaohongshu.com/explore",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        time.sleep(2)
        return fx.fetch_following_list(self._page, max_pages=max_pages)

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
