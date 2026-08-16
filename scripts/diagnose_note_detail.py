"""诊断小红书笔记详情页 DOM 结构：定位轮播图/视频的精确选择器。

用于采集脚本选择器失效时排查：输出详情页里所有 img / video 的实际尺寸、
父容器 class 与 URL 前缀，据此校准 run_scraper.py 中的提取选择器。

用法（需先启动调试 Chrome，端口 9222）:
    python scripts/diagnose_note_detail.py <关键词>
"""

import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).parent.parent))


def main(keyword: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]
        page = context.new_page()

        # 搜索页
        url = (
            f"https://www.xiaohongshu.com/search_result/"
            f"?keyword={quote(keyword)}&source=web_search_result_notes"
        )
        print(f"导航搜索页: {keyword}")
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("section.note-item", timeout=15000)

        # 第一个笔记链接
        link = page.query_selector("section.note-item a")
        if not link:
            print("未找到笔记卡片链接")
            return
        href = link.get_attribute("href") or ""
        if href.startswith("/"):
            href = f"https://www.xiaohongshu.com{href}"
        print(f"笔记链接: {href[:100]}")

        # 打开详情页
        page.goto(href, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("img, video", timeout=10000)
        page.wait_for_timeout(2500)

        print(f"\n页面 title: {page.title()!r}")
        print(f"页面 url:   {page.url[:90]}")

        # 提取所有 img 的尺寸 + 父容器 class + src
        imgs = page.query_selector_all("img")
        print(f"\n共 {len(imgs)} 个 <img>:")
        for i, img in enumerate(imgs):
            src = (img.get_attribute("src") or img.get_attribute("data-src") or "")[:90]
            try:
                info = img.evaluate(
                    "el => { const p = el.parentElement; "
                    "return {w: el.naturalWidth || 0, h: el.naturalHeight || 0, "
                    "parent: p ? (p.className || p.tagName) : ''} }"
                )
            except Exception:
                info = {"w": 0, "h": 0, "parent": ""}
            print(f"  [{i:>2}] {info['w']}x{info['h']} parent=({str(info['parent'])[:60]}) {src}")

        # 提取所有 video
        videos = page.query_selector_all("video")
        print(f"\n共 {len(videos)} 个 <video>:")
        for i, v in enumerate(videos):
            vsrc = (v.get_attribute("src") or "")[:110]
            poster = (v.get_attribute("poster") or "")[:80]
            print(f"  [{i}] src={vsrc}")
            print(f"       poster={poster}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python scripts/diagnose_note_detail.py <关键词>")
        sys.exit(1)
    main(sys.argv[1])
