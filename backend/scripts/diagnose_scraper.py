"""诊断脚本：直接测试 CDP + 小红书搜索，输出页面详情。"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.sync_api import sync_playwright


def main():
    CDP_PORT = 9222
    KEYWORD = "JK"
    SEARCH_URL = f"https://www.xiaohongshu.com/search_result/?keyword={KEYWORD}&source=web_search_result_notes"

    print(f"=== CDP 诊断 ===")
    print(f"连接到 Chrome CDP 端口: {CDP_PORT}")

    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
    print(f"连接成功！浏览器版本: {browser.version}")

    contexts = browser.contexts
    print(f"浏览器上下文数: {len(contexts)}")
    context = contexts[0]

    pages = context.pages
    print(f"已有页面数: {len(pages)}")

    # 创建新页面
    page = context.new_page()
    print(f"创建新页面 OK")

    # 检查登录态
    cookies = context.cookies()
    xhs_cookies = [c for c in cookies if "xiaohongshu" in c.get("domain", "")]
    print(f"\n小红书相关 Cookie: {len(xhs_cookies)} 个")
    for c in xhs_cookies:
        print(f"  {c.get('name')} = {c.get('value', '')[:30]}...  (domain={c.get('domain')})")

    has_session = any(c.get("name") in ("web_session", "a1", "acw_tc") for c in xhs_cookies)
    print(f"\n登录状态: {'已登录' if has_session else '未登录'}")

    # 导航到搜索页
    print(f"\n=== 导航到搜索页 ===")
    print(f"URL: {SEARCH_URL}")
    page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
    time.sleep(3)

    # 检查页面
    print(f"\n当前 URL: {page.url}")
    print(f"页面标题: {page.title()}")

    text = page.inner_text()
    print(f"页面文本长度: {len(text)} 字符")
    print(f"页面文本前 300 字符:\n{text[:300]}")

    # 截图
    from app.config import settings
    debug_dir = Path(settings.images_dir).parent / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = debug_dir / f"cdp_diag_{int(time.time())}.png"
    page.screenshot(path=str(screenshot_path), full_page=True)
    print(f"\n截图已保存: {screenshot_path}")

    # 尝试提取 __INITIAL_STATE__
    print(f"\n=== __INITIAL_STATE__ 检测 ===")
    try:
        has_state = page.evaluate("() => !!window.__INITIAL_STATE__")
        print(f"__INITIAL_STATE__ 存在: {has_state}")
        if has_state:
            keys = page.evaluate("() => Object.keys(window.__INITIAL_STATE__)")
            print(f"顶层 keys: {keys}")
            # 尝试取 notes
            search_notes = page.evaluate("() => window.__INITIAL_STATE__?.search?.notes?.length || 0")
            print(f"search.notes 数量: {search_notes}")
            note_notes = page.evaluate("() => window.__INITIAL_STATE__?.note?.notes?.length || 0")
            print(f"note.notes 数量: {note_notes}")
            # 取第一个 note 看看结构
            sample = page.evaluate("() => JSON.stringify((window.__INITIAL_STATE__?.search?.notes || [])[0])")
            if sample:
                print(f"\n第一个 note 数据:\n{sample[:500]}")
    except Exception as e:
        print(f"__INITIAL_STATE__ 检测失败: {e}")

    # 检查验证码
    print(f"\n=== 验证码检测 ===")
    html = page.content().lower()
    captcha_signals = ["captcha", "验证码", "verify", "slider", "请完成验证"]
    for s in captcha_signals:
        if s in html:
            print(f"  检测到: {s}")

    # 检查 DOM 元素
    print(f"\n=== DOM 元素检测 ===")
    for sel in ["section.note-item", "div.note-item", "[class*='note']", "a[href*='/explore/']", "img"]:
        count = len(page.query_selector_all(sel))
        print(f"  {sel}: {count} 个")

    # 提取图片
    print(f"\n=== 图片提取 ===")
    imgs = page.query_selector_all("img")
    img_urls = []
    for img in imgs[:20]:
        src = img.get_attribute("src") or img.get_attribute("data-src") or ""
        if src.startswith("http") and len(src) > 50:
            img_urls.append(src)
    print(f"有效图片 URL: {len(img_urls)} 个")
    for u in img_urls[:5]:
        print(f"  {u[:120]}")

    # 清理
    page.close()
    pw.stop()
    print(f"\n=== 诊断完成 ===")
    print(f"截图: {screenshot_path}")


if __name__ == "__main__":
    main()
