"""抖音模块 — RENDER_DATA 提取 / DOM 解析 / URL 收集 / 搜索管线。

负责：
  - 作品 URL 解析与规范化
  - RENDER_DATA 内嵌 JSON 解析（最完整数据源）
  - 详情页内容提取（网络捕获 + RENDER_DATA + DOM 三层合并）
  - 搜索页 / 博主页 URL 收集（滚动加载）
  - 抖音笔记管线：逐篇打开详情页 → 内容提取 → 即时下载入库

依赖：
  - scraper_common 中的通用工具与抖音常量
  - scraper_download 中的话题存档与下载函数
"""

import json
import random
import re
import time
import urllib.parse

from app.config import settings

from .scraper_common import (
    DOUYIN_DETAIL_ANCHOR,
    DOUYIN_DETAIL_READY,
    DOUYIN_DESC_SELECTORS,
    DOUYIN_HASHTAG_ANCHOR,
    DOUYIN_SLIDE_IMG_SELECTOR,
    DOUYIN_VIDEO_URL_HINTS,
    DOUYIN_MEDIA_HOST,
    DOUYIN_VERIFY_SELECTORS,
    DOUYIN_HOME_URL,
    DOUYIN_SEARCH_INPUT_SELECTOR,
    DOUYIN_SEARCH_RENDER_WAIT,
    goto_with_retry,
    utcnow,
    _rdsleep,
    _human_mouse_move,
    clean_media_url,
    is_content_image,
)
from .scraper_download import (
    download_batch,
    download_videos,
    save_hashtags,
    ensure_hashtag_table,
)


# ═══════════════════════════════════════════════════════════════
#  URL 解析工具（纯函数）
# ═══════════════════════════════════════════════════════════════


def _parse_douyin_aweme_id(url: str) -> str | None:
    """从抖音内容页 URL 提取作品 ID（纯函数，供单测）。

    支持 https://www.douyin.com/video/{id}、/note/{id} 以及带查询串的变体；
    分享短链（v.douyin.com/xxx）无法在客户端解析出 ID，返回 None 由调用方跳过。

    Args:
        url: 抖音内容页 URL。

    Returns:
        作品 ID 字符串，无法解析时返回 None。
    """
    if not url:
        return None
    low = url.split("?")[0].rstrip("/")
    for kind in ("video", "note"):
        marker = f"/{kind}/"
        idx = low.find(marker)
        if idx >= 0:
            tail = low[idx + len(marker):]
            seg = tail.split("/")[0] if "/" in tail else tail
            return seg or None
    return None


def _canonical_douyin_url(href: str) -> str | None:
    """把页面上的作品 href 归一化为规范详情页 URL（纯函数，供单测）。

    卡片 href 通常带 previous_item_id 等长查询串，规范化后才能跨次运行
    稳定去重。无法解析出作品 ID 时返回 None。

    Args:
        href: 页面上的作品 href。

    Returns:
        规范化后的详情页 URL，无法解析时返回 None。
    """
    clean = clean_media_url(href)
    if not clean or clean.startswith("blob:") or clean.startswith("javascript"):
        return None
    if "douyin.com" not in clean:
        return None
    kind_marker = ""
    for marker in ("/video/", "/note/"):
        if marker in clean:
            kind_marker = marker.rstrip("/")
            break
    if not kind_marker:
        return None
    aid = _parse_douyin_aweme_id(clean)
    if not aid:
        return None
    return f"{DOUYIN_MEDIA_HOST}{kind_marker}/{aid}"


def _normalize_douyin_media_url(src: str) -> str | None:
    """归一化抖音媒体 URL：补协议头 / 补主机前缀；blob 等不可下载地址返回 None。

    Args:
        src: 原始媒体 URL 字符串。

    Returns:
        归一化后的 URL，不可下载时返回 None。
    """
    s = clean_media_url(src)
    if not s or s.startswith("blob:") or s.startswith("javascript"):
        return None
    if s.startswith("/"):
        return DOUYIN_MEDIA_HOST + s
    if not s.startswith("http"):
        return None
    return s


# ═══════════════════════════════════════════════════════════════
#  RENDER_DATA 内嵌 JSON 提取
# ═══════════════════════════════════════════════════════════════


def _find_douyin_aweme_data(node, depth: int = 0) -> dict | None:
    """在任意嵌套 JSON 中递归寻找抖音作品数据对象（depth 防御病态深嵌套）。

    Args:
        node: JSON 节点（dict / list）。
        depth: 当前递归深度（防御上限 12）。

    Returns:
        找到的作品数据对象（包含 "desc" 字段的 dict），未找到返回 None。
    """
    if depth > 12:
        return None
    if isinstance(node, dict):
        candidate = (
            node.get("videoData")
            or node.get("aweme_detail")
            or node.get("awemeDetail")
        )
        if isinstance(candidate, dict) and "desc" in candidate:
            return candidate
        for v in node.values():
            hit = _find_douyin_aweme_data(v, depth + 1)
            if hit:
                return hit
    elif isinstance(node, list):
        for v in node:
            hit = _find_douyin_aweme_data(v, depth + 1)
            if hit:
                return hit
    return None


def _extract_douyin_render_data(page) -> dict:
    """解析详情页内嵌的 RENDER_DATA SSR JSON，返回提取到的字段集合。

    RENDER_DATA 是 URL 编码的 JSON 文本节点，包含当前作品的完整数据：
    desc（正文）、video.play_addr（视频播放地址）、images（图集）、封面。
    解析失败静默返回空 dict，由后续 DOM 层兜底。

    Args:
        page: Playwright 页面对象。

    Returns:
        {"img_urls": [...], "video_urls": [...], "caption": str}
    """
    out: dict = {"img_urls": [], "video_urls": [], "caption": ""}
    try:
        raw = page.evaluate(
            "() => { const el = document.getElementById('RENDER_DATA');"
            " return el ? el.textContent : ''; }"
        )
        if not raw:
            return out
        data = json.loads(urllib.parse.unquote(raw))
    except Exception:
        return out

    aweme = _find_douyin_aweme_data(data)
    if not aweme:
        return out

    out["caption"] = (aweme.get("desc") or "").strip()[:2000]

    vid = aweme.get("video") or {}
    # 封面图作为图片兜底（部分纯视频笔记只有这一张可用图）
    for cover_key in ("origin_cover", "cover"):
        url_list = ((vid.get(cover_key) or {}).get("url_list")) or []
        for u in url_list[:1]:
            normalized = _normalize_douyin_media_url(u)
            if normalized and is_content_image(normalized):
                out["img_urls"].append(normalized)
                break

    # 视频真实播放地址（play_addr 的 url_list 可能需要登录 Cookie 才可访问，
    # 与「抓到详情立即下载 + 浏览器 Cookie 鉴权」策略配套）
    for u in ((vid.get("play_addr") or {}).get("url_list")) or []:
        normalized = _normalize_douyin_media_url(u)
        if normalized:
            out["video_urls"].append(normalized)

    # 图集多图（仅图文笔记有 images 数组）
    for im in (aweme.get("images") or [])[:20]:
        for u in ((im or {}).get("url_list")) or []:
            normalized = _normalize_douyin_media_url(u)
            if normalized and is_content_image(normalized):
                out["img_urls"].append(normalized)
                break  # 每张图取第一个可用 CDN 地址即可
    return out


# ═══════════════════════════════════════════════════════════════
#  详情页内容提取（三层合并去重）
# ═══════════════════════════════════════════════════════════════


def _extract_douyin_detail(page, note_url: str) -> dict:
    """打开单个抖音作品详情页，分层提取全部内容。

    Args:
        page: Playwright 页面对象（复用当前标签页）。
        note_url: 作品规范详情页 URL。

    Returns:
        {"img_urls": [...], "video_urls": [...], "caption": str, "tags": [...]}
        三层来源合并去重；全部失败时返回空结构，由调用方记入漏斗后跳过。
    """
    result: dict = {"img_urls": [], "video_urls": [], "caption": "", "tags": []}

    # 导航期间挂响应监听：捕获浏览器自身发起的视频 CDN 直链请求
    # （比解析 DOM 更可靠——拿到的是浏览器实际会下载的同一地址）
    captured_media: list[str] = []

    def _on_response(resp):
        try:
            u = resp.url or ""
            if any(h in u for h in DOUYIN_VIDEO_URL_HINTS) and u not in captured_media:
                captured_media.append(u)
        except Exception:
            pass

    page.on("response", _on_response)
    try:
        nav_error = goto_with_retry(page, note_url, retries=1)
    finally:
        try:
            page.remove_listener("response", _on_response)
        except Exception:
            pass
    if nav_error:
        # 详情页重试后仍失败：跳过该篇（管线不中断），留痕任务日志
        print(f"  详情页导航失败，跳过 {note_url[:80]}: {nav_error}")
        return result

    # 详情页被机器人验证拦截：等人工完成验证，超时放弃该篇（留痕）
    if _is_verify_page(page):
        if _wait_verify_resolved(page):
            print(f"  详情页验证超时，跳过 {note_url[:80]}")
            return result

    # 等待详情主体渲染（超时不阻断，靠后续随机停顿再等等懒加载）
    try:
        page.wait_for_selector(DOUYIN_DETAIL_READY, timeout=12000)
    except Exception:
        pass
    _rdsleep(1.5, 2.5)
    if random.random() < 0.6:
        _human_mouse_move(page)

    # ── 层 1：RENDER_DATA 内嵌 JSON ──
    try:
        rendered = _extract_douyin_render_data(page)
    except Exception:
        rendered = {}
    result["caption"] = rendered.get("caption") or ""

    # ── 层 2：网络捕获的视频直链（优先于 play_addr——浏览器真实消费的地址）──
    for u in captured_media[:1]:
        normalized = _normalize_douyin_media_url(u)
        if normalized:
            result["video_urls"].append(normalized)

    # ── 图片提取策略：先判笔记类型，再选来源，禁止无脑全页兜底 ──
    # 教训（任务 #47「白色系穿搭」）：视频详情页没有图集容器，旧逻辑回退
    # 「全页 img」把相关推荐封面、AI 生成内容全部当素材采回（4 篇视频页
    # 提出 73 张图，用户手动删 69/73）。策略：
    #   1) 图集容器命中 → 只取容器内图片；
    #   2) 无容器但确认是视频笔记（捕获/渲染层视频直链或 <video> 播放器）
    #      → 图片只信 RENDER_DATA 封面/图集（天然笔记隔离），不扫 DOM；
    #   3) 二者皆无（页面结构未知）→ 才允许全页过滤兜底。
    slide_imgs = page.query_selector_all(DOUYIN_SLIDE_IMG_SELECTOR)
    is_video_note = False
    if not slide_imgs:
        if captured_media or rendered.get("video_urls"):
            is_video_note = True
        else:
            try:
                is_video_note = page.query_selector("video") is not None
            except Exception:
                is_video_note = False

    dom_imgs: list[str] = []
    if slide_imgs or not is_video_note:
        img_elements = slide_imgs or page.query_selector_all("img")
        for img in img_elements:
            src = clean_media_url(
                img.get_attribute("src") or img.get_attribute("data-src") or ""
            )
            if src and is_content_image(src) and src not in dom_imgs:
                dom_imgs.append(src)

    # 合并渲染层与 DOM 层候选并统一去重（渲染层在前保证顺序稳定）
    seen_imgs: set[str] = set()
    merged_imgs: list[str] = []
    for src in [*(rendered.get("img_urls") or []), *dom_imgs]:
        normalized = _normalize_douyin_media_url(src)
        if normalized and is_content_image(normalized) and normalized not in seen_imgs:
            seen_imgs.add(normalized)
            merged_imgs.append(normalized)
    result["img_urls"] = merged_imgs

    # ── 视频：网络捕获 → RENDER_DATA → DOM <video>（跳过 blob）──
    seen_vids: set[str] = set()
    merged_vids: list[str] = []
    for src in (
        ([captured_media[0]] if captured_media else [])
        + (rendered.get("video_urls") or [])
    ):
        normalized = _normalize_douyin_media_url(src)
        if normalized and normalized not in seen_vids:
            seen_vids.add(normalized)
            merged_vids.append(normalized)
    for video_el in page.query_selector_all("video"):
        raw_vsrc = video_el.get_attribute("src") or ""
        src_el = video_el.query_selector("source")
        if src_el and not raw_vsrc:
            raw_vsrc = src_el.get_attribute("src") or ""
        if raw_vsrc.startswith("blob:"):
            continue  # blob 地址无法在浏览器外下载
        normalized = _normalize_douyin_media_url(raw_vsrc)
        if normalized and normalized not in seen_vids:
            seen_vids.add(normalized)
            merged_vids.append(normalized)
    result["video_urls"] = merged_vids

    # ── caption 兜底：DOM 选择器逐个尝试 ──
    if not result["caption"]:
        for sel in DOUYIN_DESC_SELECTORS:
            el = page.query_selector(sel)
            if el:
                text = (el.inner_text() or "").strip()
                if text:
                    result["caption"] = text[:2000]
                    break

    # ── 话题标签：正文正则 + 话题锚点文本双路 ──
    tags: list[str] = []
    if result["caption"]:
        tags.extend(
            re.findall(
                r"#([^\s#，,。！？.!?]{1,30})", result["caption"]
            )
        )
    for anchor in page.query_selector_all(DOUYIN_HASHTAG_ANCHOR)[:20]:
        t = (anchor.inner_text() or "").strip().lstrip("#").strip()
        if t:
            tags.append(t)
    result["tags"] = list(
        dict.fromkeys(t.strip() for t in tags if t.strip())
    )
    return result


# ═══════════════════════════════════════════════════════════════
#  URL 收集（搜索页与博主页共用）
# ═══════════════════════════════════════════════════════════════


def _is_verify_page(page) -> bool:
    """检测当前页面是否处于机器人验证状态（滑块/验证码）。

    只认**可见**的验证元素：抖音会在每个页面预注入隐藏的验证容器模板，
    query_selector 连隐藏元素也会命中——不加可见性过滤会把正常搜索页
    误判成验证态，空等 180s「等待人工验证」而页面毫无验证内容（真实案例）。
    另：调用方须以「页面无作品卡片/提取为空」为前置条件双重防误判。
    """
    for sel in DOUYIN_VERIFY_SELECTORS:
        try:
            el = page.query_selector(sel)
        except Exception:
            continue
        try:
            if el and el.is_visible():
                return True
        except Exception:
            continue
    return False


def _wait_verify_resolved(page, timeout: int = 180) -> bool:
    """等待人工在调试 Chrome 中完成机器人验证（仿登录等待模式）。

    Args:
        page: Playwright 页面对象。
        timeout: 人工解决验证的等待上限（秒）。

    Returns:
        True 表示等待超时仍在验证态；False 表示验证已通过。
    """
    print("  ⚠️ 检测到抖音机器人验证（滑块/验证码）")
    print("  请在调试 Chrome 中手动完成验证，脚本将自动继续…")
    for waited in range(0, timeout, 5):
        time.sleep(5)
        if not _is_verify_page(page):
            print(f"  验证已通过（{waited + 5}s），继续采集")
            _rdsleep(1.0, 2.0)
            return False
        if (waited + 5) % 30 == 0:
            print(f"  等待人工验证... ({waited + 5}s / {timeout}s)")
    print("  等待人工验证超时")
    return True


def _scroll_collect_cards(
    page, detail_urls: list[str], max_items: int, max_scrolls: int
) -> int:
    """拟人化滚动收集当前页面作品卡片链接（搜索页与博主页共用）。

    滚动加载 + 提取 /video/、/note/ 锚点规范化后追加进 detail_urls；
    连续 2 轮无新增或达到 max_items 提前停止。

    Returns:
        cards_seen：滚动过程中见过的卡片总数（漏斗统计用）。
    """
    cards_seen = 0
    last_count = 0
    no_new = 0
    for _ in range(max_scrolls):
        cards_seen += len(page.query_selector_all(DOUYIN_DETAIL_ANCHOR))
        for link in page.query_selector_all(DOUYIN_DETAIL_ANCHOR):
            canonical = _canonical_douyin_url(
                link.get_attribute("href") or ""
            )
            if canonical and canonical not in detail_urls:
                detail_urls.append(canonical)
                if len(detail_urls) >= max_items:
                    return cards_seen
        if len(detail_urls) == last_count:
            no_new += 1
        else:
            no_new = 0
            last_count = len(detail_urls)
        if no_new >= 3:
            # 连续 3 轮无新增才收手：精选搜索结果流式渲染慢，过早停止
            # 会漏掉大批未渲染卡片（任务 #47 仅收 4 条）
            return cards_seen
        # 拟人化滚动：偶发鼠标移动 + 分步滚到底
        if random.random() < 0.5:
            _human_mouse_move(page)
        try:
            page.evaluate(
                "window.scrollTo(0, document.body.scrollHeight)"
            )
        except Exception:
            pass
        _rdsleep(1.0, 2.0)
    return cards_seen


def collect_douyin_detail_urls(
    page,
    base_url: str,
    max_items: int,
    max_scrolls: int = 10,
) -> tuple[list[str], dict]:
    """滚动收集直连列表页（博主页等）中的作品详情链接。

    被机器人验证拦截时页面没有任何作品卡片：检测到验证态会提示并等待
    人工在调试 Chrome 中完成验证（登录等待同款模式），超时才判死。

    注意：搜索页不走本函数——直连 /search/ URL 只渲染导航壳（2026-08
    实测），搜索用 ``collect_douyin_search_urls``（首页搜索框 → 回车）。

    Args:
        page: Playwright 页面对象。
        base_url: 初始页面 URL。
        max_items: 收集作品链接数上限。
        max_scrolls: 滚动加载次数上限。

    Returns:
        (detail_urls, funnel)：规范化后的详情页 URL 列表与滚动统计。
    """
    detail_urls: list[str] = []
    nav_error = goto_with_retry(page, base_url, retries=2)
    if nav_error:
        # 列表页导航是硬前提：重试仍失败则本轮判死，异常摘要写入漏斗
        print(f"  列表页导航最终失败: {base_url[:80]} → {nav_error}")
        return detail_urls, {
            "cards_seen": 0,
            "urls_extracted": 0,
            "error": f"导航失败: {nav_error}",
        }
    try:
        page.wait_for_selector(DOUYIN_DETAIL_ANCHOR, timeout=15000)
    except Exception:
        pass

    # ── 机器人验证门检：被风控时页面只有滑块验证，没有任何作品卡片 ──
    if (
        not page.query_selector_all(DOUYIN_DETAIL_ANCHOR)
        and _is_verify_page(page)
        and _wait_verify_resolved(page)
    ):
        return detail_urls, {
            "cards_seen": 0,
            "urls_extracted": 0,
            "error": "触发机器人验证，等待人工解决超时"
                     "（请在调试 Chrome 完成验证后重跑任务）",
        }
    _rdsleep(1.5, 3.0)
    if random.random() < 0.7:
        _human_mouse_move(page)

    cards_seen = _scroll_collect_cards(
        page, detail_urls, max_items, max_scrolls
    )

    # 滚动一轮颗粒无收且页面转为验证态（风控中途触发）：等人工解决后补滚一轮
    if not detail_urls and _is_verify_page(page):
        if _wait_verify_resolved(page):
            return detail_urls, {
                "cards_seen": cards_seen,
                "urls_extracted": len(detail_urls),
                "error": "采集过程中触发机器人验证，等待人工解决超时",
            }
        cards_seen += _scroll_collect_cards(
            page, detail_urls, max_items, max_scrolls
        )
    return detail_urls, {
        "cards_seen": cards_seen,
        "urls_extracted": len(detail_urls),
    }


def collect_douyin_search_urls(
    page, keyword: str, max_items: int, max_scrolls: int = 12
) -> tuple[list[str], dict]:
    """抖音搜索专用 URL 收集：首页搜索框 → 回车 → 等结果渲染 → 滚动收集。

    为什么不走直连 /search/ URL（2026-08 实测，任务 #43~#45 全 0 条）：
    直连搜索 URL 只渲染导航壳（0 作品卡片、无验证码，疑似静默风控）；
    首页搜索框输入回车落到 /jingxuan/search/ 页，结果正常渲染
    （实测 67 个 /video/ 锚点），但首屏渲染慢（>15s），故轮询等待。

    Args:
        page: Playwright 页面对象。
        keyword: 搜索关键词。
        max_items: 收集作品链接数上限。
        max_scrolls: 滚动加载次数上限。

    Returns:
        (detail_urls, funnel)：规范化后的详情页 URL 列表与滚动统计。
    """
    detail_urls: list[str] = []
    funnel_base: dict = {"cards_seen": 0, "urls_extracted": 0}

    nav_error = goto_with_retry(page, DOUYIN_HOME_URL, retries=1)
    if nav_error:
        print(f"  首页导航最终失败: {nav_error}")
        return detail_urls, {**funnel_base, "error": f"导航失败: {nav_error}"}
    try:
        page.wait_for_selector(DOUYIN_SEARCH_INPUT_SELECTOR, timeout=15000)
    except Exception:
        pass
    inp = page.query_selector(DOUYIN_SEARCH_INPUT_SELECTOR)
    if inp is None:
        return detail_urls, {
            **funnel_base,
            "error": "未找到抖音首页搜索框（页面结构变化，请检查选择器）",
        }
    try:
        inp.click()
        _rdsleep(0.5, 1.0)
        inp.fill(keyword)
        _rdsleep(0.5, 1.0)
        inp.press("Enter")
    except Exception as e:
        return detail_urls, {
            **funnel_base,
            "error": f"搜索框操作失败: {type(e).__name__}: {str(e)[:120]}",
        }
    print(f"  已在首页搜索框输入「{keyword}」并回车，等待结果渲染…")

    # 精选搜索结果首屏渲染慢（实测 >15s）：轮询等待首批卡片出现
    cards = 0
    for _waited in range(0, DOUYIN_SEARCH_RENDER_WAIT, 5):
        time.sleep(5)
        cards = len(page.query_selector_all(DOUYIN_DETAIL_ANCHOR))
        if cards:
            break
    if not cards:
        # 可见验证门检（隐藏模板不算）：等人工解决后再给一次机会
        if _is_verify_page(page) and _wait_verify_resolved(page):
            return detail_urls, {
                **funnel_base,
                "error": "触发机器人验证，等待人工解决超时"
                         "（请在调试 Chrome 完成验证后重跑任务）",
            }
        time.sleep(5)
        cards = len(page.query_selector_all(DOUYIN_DETAIL_ANCHOR))
        if not cards:
            return detail_urls, {
                **funnel_base,
                "error": f"搜索结果 {DOUYIN_SEARCH_RENDER_WAIT}s 内未渲染"
                         "（可能被静默风控；可在调试 Chrome 手动搜索验证）",
            }

    # 结果流式渲染：等卡片数量趋稳（≥10 张或连续 15s 无增长）再开滚。
    # 实测该页面静置 1~2 分钟内从个位数涨到 60+，过早开滚只收得到零星
    # 几条（任务 #47：cards_seen=12、urls_extracted=4）。
    stable_rounds = 0
    last_cards = cards
    for _ in range(12):  # 最多再等 ~60s
        time.sleep(5)
        cards_now = len(page.query_selector_all(DOUYIN_DETAIL_ANCHOR))
        if cards_now == last_cards:
            stable_rounds += 1
        else:
            stable_rounds = 0
            last_cards = cards_now
        if cards_now >= 10 or stable_rounds >= 3:
            break

    _rdsleep(1.5, 3.0)
    if random.random() < 0.7:
        _human_mouse_move(page)

    cards_seen = _scroll_collect_cards(
        page, detail_urls, max_items, max_scrolls
    )

    # 滚动一轮颗粒无收且页面转为验证态：等人工解决后补滚一轮
    if not detail_urls and _is_verify_page(page):
        if _wait_verify_resolved(page):
            return detail_urls, {
                "cards_seen": cards_seen,
                "urls_extracted": len(detail_urls),
                "error": "采集过程中触发机器人验证，等待人工解决超时",
            }
        cards_seen += _scroll_collect_cards(
            page, detail_urls, max_items, max_scrolls
        )
    return detail_urls, {
        "cards_seen": cards_seen,
        "urls_extracted": len(detail_urls),
    }


# ═══════════════════════════════════════════════════════════════
#  抖音笔记管线（搜索模式与博主模式共用）
# ═══════════════════════════════════════════════════════════════


def run_douyin_notes_pipeline(
    page,
    task_id: int,
    note_urls: list[str],
    budget: int | None,
    img_dir,
    videos_dir,
    today: str,
    httpx_module,
    browser_cookies: dict,
    existing_url_set: set[str],
    content_hash_set: set[str],
    blogger_id: int | None,
    download_video: bool,
    source_kind: str,
) -> tuple[int, int, list[dict]]:
    """抖音笔记管线：逐个打开详情页提取 → 图片/视频即时下载入库。

    搜索模式与按博主模式共用的内部循环；差异仅在于 URL 来源与 meta 构造
    （blogger_id 是否填充）。单个作品失败跳过不中断，漏斗逐条留痕。

    Args:
        page: Playwright 页面对象。
        task_id: 采集任务 ID。
        note_urls: 作品详情页 URL 列表。
        budget: 本次管线的入库预算（够了提前停止）；None 表示不设素材级预算。
        img_dir: 图片存储目录。
        videos_dir: 视频存储目录。
        today: 日期字符串。
        httpx_module: httpx 模块。
        browser_cookies: 浏览器 Cookie 字典。
        existing_url_set: 已存在 URL 集合。
        content_hash_set: 内容 MD5 集合。
        blogger_id: 按博主模式传入，图片/视频同步建立博主关联。
        download_video: False 时跳过视频下载（省磁盘开关）。
        source_kind: 话题存档来源标记（search | blogger）。

    Returns:
        (items_found, items_added, notes_log)
    """
    items_found = 0
    items_added = 0
    notes_log: list[dict] = []

    conn = None
    try:
        conn = _sqlite3.connect(
            str(settings.storage_root.parent / "fashion_inspo.db")
        )
        ensure_hashtag_table(conn)
    except Exception:
        conn = None

    for i, note_url in enumerate(note_urls, 1):
        if budget is not None and items_added >= budget:
            print(
                f"  已入库 {items_added} 个 → 达到本轮预算，停止打开详情页"
            )
            break
        detail_delay = random.uniform(2.0, 4.0)
        try:
            detail = _extract_douyin_detail(page, note_url)
        except Exception as e:
            err = str(e) or type(e).__name__
            print(
                f"  [{i}/{len(note_urls)}] 详情提取异常:"
                f" {err[:80]}"
            )
            notes_log.append({"note": note_url, "error": err[:200]})
            time.sleep(detail_delay)
            continue

        if not detail["img_urls"] and not detail["video_urls"]:
            print(
                f"  [{i}/{len(note_urls)}] 详情页无内容"
                f"（可能触发验证码或已删除）"
            )
            notes_log.append(
                {"note": note_url, "error": "详情页无内容"}
            )
            time.sleep(detail_delay)
            continue

        meta: dict = {
            "caption": detail.get("caption") or "",
            "tags": detail.get("tags") or [],
            "source_kind": source_kind,
            "hashtags_saved": False,
        }
        if blogger_id is not None:
            meta["blogger_id"] = blogger_id
        meta_map = {note_url: meta}

        img_pairs = [(note_url, u) for u in detail["img_urls"]]
        video_pairs = [(note_url, u) for u in detail["video_urls"]]
        items_found += len(img_pairs) + len(video_pairs)

        added = 0
        sk_ex = sk_h = sk_n = sk_dup = 0
        if img_pairs:
            # 预算剩余量；无预算（博主模式）时给单笔记宽松上限
            remaining = (
                (budget - items_added) if budget is not None
                else 200
            )
            remaining = max(remaining, 1)
            added, sk_ex, sk_h, sk_n, sk_dup = download_batch(
                img_pairs,
                task_id,
                existing_url_set,
                remaining,
                img_dir,
                today,
                httpx_module,
                browser_cookies,
                content_hash_set,
                meta_map,
                platform="douyin",
            )

        v_added = 0
        if video_pairs and download_video:
            v_budget = (
                (budget - items_added) if budget is not None
                else 50
            )
            v_added, v_skipped = download_videos(
                video_pairs,
                task_id,
                existing_url_set,
                max(v_budget, 1),
                videos_dir,
                today,
                httpx_module,
                browser_cookies,
                meta_map,
                platform="douyin",
            )
            added += v_added
        elif video_pairs:
            notes_log.append(
                {
                    "note": note_url,
                    "videos_skipped": len(video_pairs),
                    "reason": "视频下载开关关闭",
                }
            )

        items_added += added
        tag_saved = None
        if conn is not None:
            try:
                tag_saved = save_hashtags(
                    conn, meta_map[note_url], note_url
                )
                conn.commit()
            except Exception:
                pass

        notes_log.append(
            {
                "note": note_url,
                "imgs": len(img_pairs),
                "videos": len(video_pairs),
                "added": added,
                "has_tags": bool(meta["tags"]),
                "hashtags_saved": tag_saved,
            }
        )
        print(
            f"  [{i}/{len(note_urls)}] 图 {len(img_pairs)} /"
            f" 视频 {len(video_pairs)}"
            f" → 入库 {added}"
        )
        time.sleep(random.uniform(2.0, 4.0))  # 详情页间隔风控节奏

    return items_found, items_added, notes_log


# ═══════════════════════════════════════════════════════════════
#  配置解析辅助
# ═══════════════════════════════════════════════════════════════


def resolve_douyin_profile_url(config: dict) -> str:
    """从任务配置解析抖音博主主页 URL（显式 profile_url 优先）。

    Args:
        config: 任务配置字典。

    Returns:
        博主主页 URL。

    Raises:
        RuntimeError: 缺少 profile_url 或 platform_user_id。
    """
    profile_url = config.get("profile_url")
    if profile_url:
        return profile_url
    puid = config.get("platform_user_id")
    if puid:
        return f"{DOUYIN_MEDIA_HOST}/user/{puid}"
    raise RuntimeError(
        "按博主采集缺少 profile_url / platform_user_id"
    )
