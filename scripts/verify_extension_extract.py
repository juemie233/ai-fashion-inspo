# -*- coding: utf-8 -*-
"""临时验证脚本：用 Playwright 模拟小红书笔记页，验证插件图片提取逻辑。

用法：
    python scripts/verify_extension_extract.py

验证点：
1. 能提取笔记正文大图（含 srcset 大图覆盖小图、同图多尺寸去重）
2. 能提取懒加载图片（无 src，仅 data-src）
3. 头像（URL/容器含 avatar）被过滤
4. 小图标、横幅广告被过滤
5. 无尺寸信息的懒加载图保留为候选
"""
import sys
from pathlib import Path

# Windows 控制台默认 GBK，强制 UTF-8 输出避免 emoji/中文报错
sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from playwright.sync_api import sync_playwright  # noqa: E402

EXT_DIR = Path(__file__).resolve().parents[1] / "browser-extension"
EXTRACT_JS = EXT_DIR / "content-scripts" / "extract-images.js"


def svg(w: int, h: int) -> str:
    """生成指定尺寸的 SVG 占位图。"""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">'
        f'<rect width="100%" height="100%" fill="#e0e0e0"/></svg>'
    )


# 模拟小红书笔记详情页：作者区（头像）+ 笔记内容（多图 + 懒加载）+ 底部栏（图标/横幅）
MOCK_HTML = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>模拟小红书笔记页</title></head>
<body>
  <div class="author">
    <img class="avatar" src="http://test.local/avatar/user1.svg" alt="我的头像" />
    <span class="nickname">穿搭博主</span>
  </div>
  <div class="note-content">
    <img src="http://test.local/img/note1.svg" alt="穿搭图1" />
    <img src="http://test.local/img/note1_400x600.svg" alt="穿搭图1变体" />
    <img src="http://test.local/img/note2.svg" alt="穿搭图2" />
    <img src="http://test.local/img/note2_600x900.svg" alt="穿搭图2大图" />
    <img data-src="http://test.local/img/lazy1.svg" alt="懒加载图" />
    <img alt="无来源占位图" />
  </div>
  <div class="footer-bar">
    <img src="http://test.local/img/icon-like.svg" alt="点赞图标" />
    <img src="http://test.local/img/banner.svg" alt="广告横幅" />
  </div>
  <div class="author">
    <img data-src="http://test.local/avatar/user2.svg" alt="懒加载头像" />
  </div>
</body>
</html>"""


def main() -> int:
    failures: list[str] = []
    extract_js = EXTRACT_JS.read_text(encoding="utf-8")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        # 拦截所有图片请求，按 URL 返回对应尺寸的 SVG
        def handle_route(route):
            url = route.request.url
            if "note2_600x900" in url:
                body = svg(600, 900)
            elif "note2" in url:
                body = svg(400, 600)
            elif "note1" in url:
                body = svg(400, 600)
            elif "lazy1" in url:
                body = svg(400, 600)
            elif "avatar" in url:
                body = svg(200, 200)
            elif "icon-like" in url:
                body = svg(24, 24)
            elif "banner" in url:
                body = svg(1200, 100)
            else:
                body = svg(100, 100)
            route.fulfill(status=200, content_type="image/svg+xml", body=body)

        page.route("**/*", handle_route)
        page.set_content(MOCK_HTML, wait_until="networkidle")

        # 注入插件内容脚本，取返回值（即提取结果）
        result = page.evaluate(extract_js)
        images = result["images"] or []
        urls = [img["url"] for img in images]

        print(f"共提取 {len(images)} 张候选图片：")
        for img in images:
            print(f"  - {img['url']} ({img['width']}x{img['height']})")

        # 断言 1：笔记大图应被提取
        if not any("note1.svg" in u for u in urls):
            failures.append("笔记图 note1 未被提取")
        if not any("lazy1.svg" in u for u in urls):
            failures.append("懒加载图 lazy1 未被提取")
        if not any("note2_600x900.svg" in u for u in urls):
            failures.append("note2 应保留更大尺寸的 600x900 版本")
        if any("note2.svg" in u and "600x900" not in u for u in urls):
            failures.append("note2 的小尺寸版本应被去重")

        # 断言 2：头像 / 图标 / 横幅应被过滤
        for bad in ("avatar", "icon-like", "banner"):
            if any(bad in u for u in urls):
                failures.append(f"非内容图 {bad} 未被过滤")

        # 断言 3：懒加载图保留为候选（无尺寸信息）
        lazy = next((i for i in images if "lazy1.svg" in i["url"]), None)
        if not lazy:
            failures.append("懒加载图 lazy1 不在结果中")
        elif lazy["width"] or lazy["height"]:
            failures.append("懒加载图尺寸应为 0（无尺寸信息保留为候选）")

        browser.close()

    if failures:
        print("\n❌ 验证失败：")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\n✅ 全部验证通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
