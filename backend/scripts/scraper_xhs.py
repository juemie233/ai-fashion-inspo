"""小红书模块 — 搜索 / 博主采集 / 详情页提取。

负责：
  - 关键词搜索模式：滚动加载搜索结果卡片 → DOM 提取图片 URL
  - 按博主采集模式：打开博主主页收集笔记 → 逐个详情页提取全量内容并入库
  - 笔记详情页内容提取：轮播图 / 视频 / 正文 / 话题标签

依赖：
  - scraper_common 中的通用工具与常量
  - scraper_download 中的话题存档与下载函数
"""

import random
import re
import time
from urllib.parse import quote

from app.config import settings

from .scraper_common import (
    SORT_LABELS,
    utcnow,
    _rdsleep,
    _human_mouse_move,
    human_scroll,
    clean_media_url,
    is_content_image,
    raise_if_xhs_blocked,
    ScraperBlockedError,
)
from .scraper_download import download_batch, download_videos, _HASHTAG_SAVED_COUNT


# ═══════════════════════════════════════════════════════════════
#  小红书 URL 归一化（分页去重的基础）
# ═══════════════════════════════════════════════════════════════

"""笔记详情路径特征：只认这两类；用户主页 / 搜索词 / 话题页都不是笔记。

对应 MediaCrawler 里按 model_type 过滤「相关搜索 / 热搜」卡片的做法：
DOM 侧的等价物就是「链接不是笔记详情路径」一律不作为笔记处理。
"""
XHS_NOTE_PATH_HINTS = ("/explore/", "/discovery/item/")


def canonical_note_url(href: str) -> str:
    """卡片 href → 笔记详情 URL；非笔记链接返回空串。

    保留 query：详情页必须带 xsec_token / xsec_source 才能打开，
    因此这里只补全协议与域名，是否重复交给 :func:`note_id_of` 判定。

    Args:
        href: 卡片内 <a> 的 href（可能是相对路径）。

    Returns:
        绝对 URL；非笔记链接返回空串。
    """
    href = clean_media_url(href)
    if not href:
        return ""
    url = (
        href
        if href.startswith("http")
        else f"https://www.xiaohongshu.com{href}"
    )
    path = url.split("?", 1)[0]
    if not any(hint in path for hint in XHS_NOTE_PATH_HINTS):
        return ""
    return url


def note_id_of(note_url: str) -> str:
    """从笔记 URL 取平台笔记 ID（去 query 后的末段）。

    卡片 href 带 xsec_token，同一篇笔记在不同轮次 / 不同位置拿到的 token
    可能不同——按原始 URL 串去重会把同一篇笔记重复采集、重复下载
    （这正是 MediaCrawler 用 note_id + cursor 做分页幂等的理由）。

    Args:
        note_url: 笔记 URL（可带 query）。

    Returns:
        笔记 ID；无法解析时返回空串。
    """
    path = (note_url or "").split("?", 1)[0].rstrip("/")
    return path.split("/")[-1] if path else ""


# ═══════════════════════════════════════════════════════════════
#  小红书详情页内容提取
# ═══════════════════════════════════════════════════════════════


def extract_note_detail(page, note_url: str) -> dict:
    """打开单个小红书笔记详情页，提取全部内容。

    搜索结果/主页卡片通常只渲染 1 张封面；轮播图（多图）、视频、正文描述与
    话题标签只在详情页完整加载。本函数逐个打开详情页把「卡片上看不到的
    内容」补齐。

    Args:
        page: Playwright 页面对象（复用当前标签页）。
        note_url: 笔记详情页完整 URL。

    Returns:
        {"img_urls": [...], "video_urls": [...], "caption": str, "tags": [...]}

    Raises:
        ScraperBlockedError: 命中验证码/登录墙/限流等风控页（不重试，交由调用方
            决定停止整轮或跳过本篇）；笔记已删除等非致命类型同样抛出，
            调用方据 ``is_fatal`` 区分。
    """
    result: dict = {"img_urls": [], "video_urls": [], "caption": "", "tags": []}
    try:
        page.goto(note_url, wait_until="domcontentloaded", timeout=30000)
    except Exception:
        return result

    # 风控门检：详情页被验证码/登录墙拦截时内容区永远是空的，先判再等，
    # 避免白等 10s 后拿到空结果被误当成「这篇笔记没图片」
    raise_if_xhs_blocked(page, note_url)

    # 等待详情页主体渲染（轮播图或视频）
    try:
        page.wait_for_selector(
            "div.swiper-slide img, video, div[class*=swiper] img, div[class*='note-content']",
            timeout=10000,
        )
    except Exception:
        pass

    # 拟人化：加载后随机停顿 + 偶发鼠标移动，触发轮播懒加载
    _rdsleep(1.0, 2.5)
    if random.random() < 0.6:
        _human_mouse_move(page)

    # ── 轮播图：优先 swiper 轮播容器（精确），回退到全页 img（宽松）──
    img_elements = []
    for sel in ("div.swiper-slide img", "div[class*=swiper] img"):
        img_elements = page.query_selector_all(sel)
        if img_elements:
            break
    if not img_elements:
        img_elements = page.query_selector_all("img")

    seen_imgs: set[str] = set()
    for img in img_elements:
        src = clean_media_url(
            img.get_attribute("src") or img.get_attribute("data-src") or ""
        )
        if not src or not is_content_image(src) or src in seen_imgs:
            continue
        seen_imgs.add(src)
        result["img_urls"].append(src)

    # ── 视频：<video> 的 src（或 <source> 子标签），封面 poster 一并作为图片采集 ──
    for video in page.query_selector_all("video"):
        vsrc = clean_media_url(video.get_attribute("src") or "")
        if not vsrc:
            source_el = video.query_selector("source")
            if source_el:
                vsrc = clean_media_url(source_el.get_attribute("src") or "")
        if vsrc and vsrc not in result["video_urls"]:
            result["video_urls"].append(vsrc)
        poster = clean_media_url(video.get_attribute("poster") or "")
        if poster and is_content_image(poster) and poster not in seen_imgs:
            seen_imgs.add(poster)
            result["img_urls"].append(poster)

    # ── 正文描述（多候选选择器 + 兜底）──
    try:
        for sel in (
            "div.note-content span, div[class*='note-content'] span",
            "div[class*='desc']",
            "div#detail-desc",
            "div[class*='title']",
        ):
            el = page.query_selector(sel)
            if el:
                text = (el.inner_text() or "").strip()
                if text:
                    result["caption"] = text[:2000]  # 上限 2000 字
                    break
    except Exception:
        pass

    # ── 话题标签：从正文中的 #话题 提取 ──
    if result["caption"]:
        tags = re.findall(r"#([^\s#，,。！？.!?]{1,30})", result["caption"])
        result["tags"] = [t.strip() for t in tags if t.strip()]

    return result


# ═══════════════════════════════════════════════════════════════
#  小红书博主模式
# ═══════════════════════════════════════════════════════════════


def collect_blogger_note_urls(
    page,
    profile_url: str,
    max_notes: int,
    max_scrolls: int = 15,
) -> list[str]:
    """打开博主主页，滚动加载笔记卡片，收集笔记详情链接（去重，上限 max_notes）。

    Args:
        page: Playwright 页面对象。
        profile_url: 博主主页 URL（含 /user/profile/{uid}）。
        max_notes: 收集笔记数上限。
        max_scrolls: 滚动加载次数上限。

    Returns:
        笔记详情 URL 列表（顺序按页面出现顺序）。

    Raises:
        ScraperBlockedError: 主页被风控/登录墙拦截（不重试）。
    """
    note_urls: list[str] = []
    try:
        page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
    except Exception:
        return note_urls

    # 风控门检：主页被拦截时笔记网格为空，先判再等（避免白等 15s）
    raise_if_xhs_blocked(page, profile_url)

    try:
        page.wait_for_selector(
            "section.note-item, a[href*='/explore/']", timeout=15000
        )
    except Exception:
        pass

    last_count = 0
    no_new = 0
    seen_note_ids: set[str] = set()
    for _ in range(max_scrolls):
        # 收集当前页全部笔记链接（按笔记 ID 去重：主页卡片 href 带
        # xsec_token，同一篇笔记不同轮次 token 可能不同，按 URL 串去重
        # 会重复打开同一篇详情页并重复下载）
        for link in page.query_selector_all("a[href*='/explore/']"):
            note_url = canonical_note_url(link.get_attribute("href") or "")
            if not note_url:
                continue
            note_id = note_id_of(note_url)
            if note_id in seen_note_ids:
                continue
            seen_note_ids.add(note_id)
            note_urls.append(note_url)
            if len(note_urls) >= max_notes:
                return note_urls
        if len(note_urls) == last_count:
            no_new += 1
        else:
            no_new = 0
            last_count = len(note_urls)
        if no_new >= 2:
            break
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass
        _rdsleep(1.0, 2.0)
    return note_urls


# ═══════════════════════════════════════════════════════════════
#  小红书搜索模式
# ═══════════════════════════════════════════════════════════════


def extract_image_pairs_from_cards(
    cards,
    need_pairs: int,
    seen_urls: set[str],
    seen_note_ids: set[str],
    counters: dict,
) -> list[tuple[str, str]]:
    """从笔记卡片提取 (笔记 URL, 图片 CDN URL)，带分页幂等与超额截断。

    对齐 MediaCrawler 的分页状态机实践——分页/滚动过程中重叠部分必须对
    「同一实体的不同表示」做归一化去重（其 API 侧是 note_id + cursor，
    DOM 侧则是去 xsec_token 后的笔记 ID）：

    - 非笔记卡片过滤：相关搜索 / 热搜 / 用户卡片没有笔记详情路径，直接跳过
    - note_id 幂等：同一篇笔记不同 token 只处理一次（seen_note_ids）
    - 图片 URL 幂等：滚动重叠区域的同图只取一次（seen_urls）
    - 超额截断：够 need_pairs 张即停，不把整页图片全拉下来

    counters 为调用方传入的计数器字典（原地累加，用于漏斗日志）：
    non_note / dup_note / without_img / with_img / small / icon / dup_img。

    Args:
        cards: 卡片元素列表。
        need_pairs: 需要的图片数量上限（超额截断）。
        seen_urls: 已采集的图片 URL 集合（跨轮次复用）。
        seen_note_ids: 已采集的笔记 ID 集合（跨轮次复用）。
        counters: 漏斗计数器（原地修改）。

    Returns:
        (笔记 URL, 图片 URL) 列表。
    """
    pairs: list[tuple[str, str]] = []

    def _bump(key: str) -> None:
        counters[key] = counters.get(key, 0) + 1

    for card in cards:
        if len(pairs) >= need_pairs:
            break
        try:
            link_el = card.query_selector("a")
            href = (link_el.get_attribute("href") if link_el else "") or ""
            note_url = canonical_note_url(href)
            if not note_url:
                _bump("non_note")
                continue
            note_id = note_id_of(note_url)
            if note_id in seen_note_ids:
                _bump("dup_note")
                continue
            # 立即登记已见（无图片的卡片同样登记）：滚动到新内容时会重扫整页
            # 卡片，不立刻登记会让「无图卡片」在每轮被重复计数
            seen_note_ids.add(note_id)

            # 从每张卡片中提取所有图片（轮播帖含多图）
            imgs = card.query_selector_all("img")
            if not imgs:
                _bump("without_img")
                continue
            _bump("with_img")

            for img in imgs:
                src = (
                    img.get_attribute("src")
                    or img.get_attribute("data-src")
                    or ""
                )
                if not src or not src.startswith("http"):
                    continue
                # 过滤图标类 URL
                if any(
                    k in src.lower()
                    for k in ("icon", "avatar", "logo", "favicon", "emoji")
                ):
                    _bump("icon")
                    continue
                # 过滤小尺寸（< 100px 任意边）
                w = img.get_attribute("width") or ""
                h = img.get_attribute("height") or ""
                try:
                    if w and h and (int(w) < 100 or int(h) < 100):
                        _bump("small")
                        continue
                except ValueError:
                    pass
                if src in seen_urls:
                    _bump("dup_img")
                    continue
                seen_urls.add(src)
                pairs.append((note_url, src))
        except Exception:
            continue
    return pairs


def search_xiaohongshu(
    page,
    keyword: str,
    need_count: int,
    sort_type: str = "general",
    on_batch=None,
) -> tuple[list[tuple[str, str]], dict]:
    """在已登录的页面上搜索并提取图片 URL。

    采用触底循环滚动策略，持续滚到懒加载不出新卡片或达到上限为止。
    从每张卡片中提取多张图片（轮播帖），最大化采集数量。

    Args:
        page: Playwright 页面对象。
        keyword: 搜索关键词。
        need_count: 本次搜索还需采集的数量（剩余需求）。
        sort_type: 排序方式 — "general"(综合) / "time_descending"(最新) / "popularity_descending"(最热)。
        on_batch: 可选回调 ``on_batch(pairs) -> int``，每滚动一轮把**本轮新增**
            的 (笔记 URL, 图片 URL) 交给调用方立即落库，返回值是实际入库数。
            对应 MediaCrawler 的「分页回调落库」：任务中途被风控打断或进程
            被杀时，已抓到的部分不会白丢（否则整批只在最后一次性下载，
            半途失败则该关键词颗粒无收）。传了回调时函数返回空 pairs，
            并在累计入库数达到 need_count 时提前停止滚动（少滚少触风控）。

    Returns:
        (pairs, funnel_dict): 未传回调时为每张图片的 (笔记页面 URL, 图片 CDN URL)
        列表；传了回调时为空列表（内容已逐批落库）。funnel 为漏斗统计
        （含 batches / batch_added 两个分批落库计数）。

    Raises:
        ScraperBlockedError: 搜索页被风控/登录墙拦截（不重试，交由调用方停止整轮）。
    """
    if page.is_closed():
        raise RuntimeError("页面已关闭")

    sort_label = SORT_LABELS.get(sort_type, sort_type)

    url = (
        f"https://www.xiaohongshu.com/search_result/"
        f"?keyword={quote(keyword)}&source=web_search_result_notes&sort={sort_type}"
    )
    print(f"  导航到搜索页 [{sort_label}]: {keyword}")
    page.goto(url, wait_until="domcontentloaded", timeout=30000)

    # 风控门检（第一道）：验证码/限流页当场可见，先判再等，避免白等 15s
    raise_if_xhs_blocked(page, f"关键词={keyword}")

    # 等待搜索结果卡片渲染完成
    try:
        page.wait_for_selector("section.note-item", timeout=15000)
        print("  搜索结果已渲染")
    except Exception:
        print("  等待搜索结果超时，尝试继续...")
        # 风控门检（第二道）：卡片始终不出且页面转成登录墙/限流文案
        # （静默风控）——此时按风控处理，而不是当作「该关键词无结果」
        raise_if_xhs_blocked(page, f"关键词={keyword}（无结果卡片）")
    # 拟人化：页面加载后随机停顿 + 偶发鼠标移动，模拟真人浏览前先看一页
    _rdsleep(1.5, 3.5)
    if random.random() < 0.7:
        _human_mouse_move(page)

    # ── 触底循环滚动（每轮增量提取 + 立即落库）──
    MAX_SCROLLS = 10  # 滚动硬上限（真人不会滚 30 次）
    CONSECUTIVE_NO_NEW = 1  # 连续 N 次无新卡片即视为到底
    target_cards = max(10, int(need_count * 1.5))  # 已获取足够卡片即停
    no_new_count = 0
    last_card_count = 0

    # 幂等状态跨轮复用：滚动重叠区域不重复解析、不重复入库
    seen: set[str] = set()
    seen_note_ids: set[str] = set()
    counters: dict = {}
    collected: list[tuple[str, str]] = []  # 无回调模式的累积结果
    extracted_total = 0  # 本轮次累计提取到的图片对（漏斗用）
    batch_added = 0  # 回调模式累计入库数
    batch_count = 0  # 已落库批次数
    total_cards = 0

    for scroll_i in range(MAX_SCROLLS):
        # 拟人化滚动：偶发鼠标移动 + 分步随机滚到底 + 随机停顿
        if random.random() < 0.6:
            _human_mouse_move(page)
        human_scroll(page)
        _rdsleep(1.0, 2.5)

        # 偶发回滚一点，模拟真人来回浏览
        if random.random() < 0.15:
            page.evaluate(f"window.scrollBy(0, -{random.randint(100, 300)})")
            _rdsleep(0.5, 1.2)

        # 检查是否有新内容加载
        cards = page.query_selector_all("section.note-item")
        cards_now = len(cards)
        total_cards = max(total_cards, cards_now)

        if cards_now == last_card_count:
            no_new_count += 1
        else:
            no_new_count = 0
            last_card_count = cards_now

        # 卡片数未增长（懒加载到底）→ 不再重复解析整页，直接收尾。
        # 判断前置到解析之前：否则每轮重扫都会把已处理的卡片再计一次数
        if no_new_count >= CONSECUTIVE_NO_NEW:
            print(
                f"  连续 {CONSECUTIVE_NO_NEW} 次无新内容，页面已到底"
                f"（{cards_now} 卡片）"
            )
            break

        # 本轮增量提取（只取尚未处理过的笔记/图片）
        new_pairs = extract_image_pairs_from_cards(
            cards, need_count * 2, seen, seen_note_ids, counters
        )
        if new_pairs:
            extracted_total += len(new_pairs)
            batch_count += 1
            if on_batch is not None:
                # 立即落库：任务中途被风控打断/进程被杀时，已抓到的部分不白丢
                batch_added += on_batch(new_pairs)
            else:
                collected.extend(new_pairs)

        # 回调模式：已入库足够即停，少滚少触风控
        if on_batch is not None and batch_added >= need_count:
            print(
                f"  滚动 {scroll_i + 1} 次后已入库 {batch_added} 张"
                f"（目标 {need_count}），停止滚动"
            )
            break

        # 无回调模式沿用原策略：卡片够用即停
        if on_batch is None and cards_now >= target_cards:
            print(
                f"  滚动 {scroll_i + 1} 次后已获取 {cards_now} 个卡片"
                f"（目标 {target_cards}），停止滚动"
            )
            break

    # ── 漏斗日志 ──
    funnel = {
        "cards_total": total_cards,
        "cards_with_img": counters.get("with_img", 0),
        "cards_without_img": counters.get("without_img", 0),
        "skipped_non_note": counters.get("non_note", 0),
        "skipped_dup_note": counters.get("dup_note", 0),
        "skipped_small": counters.get("small", 0),
        "skipped_icon": counters.get("icon", 0),
        "skipped_dup_img": counters.get("dup_img", 0),
        "urls_extracted": extracted_total,
        "target": need_count,
        "batches": batch_count,
        "batch_added": batch_added,
    }
    print(f"  ┌─ 提取漏斗 ─────────────────────────────")
    print(f"  │ DOM 卡片总数: {total_cards}")
    print(f"  │ 有图片的卡片: {funnel['cards_with_img']}")
    print(f"  │ 无图片的卡片: {funnel['cards_without_img']}")
    print(f"  │ 跳过非笔记:   {funnel['skipped_non_note']}")
    print(f"  │ 跳过重复笔记: {funnel['skipped_dup_note']}")
    print(f"  │ 跳过小尺寸:   {funnel['skipped_small']}")
    print(f"  │ 跳过图标:     {funnel['skipped_icon']}")
    print(f"  │ 跳过重复图:   {funnel['skipped_dup_img']}")
    print(f"  │ 提取到 URL:   {extracted_total}")
    if on_batch is not None:
        print(f"  │ 已分批入库:   {batch_added}（{batch_count} 批）")
    print(f"  │ 目标数量:     {need_count}")
    print(f"  └──────────────────────────────────────────")

    # 回调模式：图片已逐批落库，返回空列表避免调用方重复下载
    if on_batch is not None:
        return [], funnel
    # 超额截断：多取一倍作为下载失败缓冲，由调用方按剩余需求截断
    return collected[: need_count * 2], funnel


# ═══════════════════════════════════════════════════════════════
#  小红书博主采集管线
# ═══════════════════════════════════════════════════════════════


def run_blogger_mode(
    page,
    task_id: int,
    blogger_id: int,
    config: dict,
    img_dir,
    videos_dir,
    today: str,
    httpx_module,
    browser_cookies: dict,
    existing_url_set: set[str],
    content_hash_set: set[str],
) -> tuple[int, int, list[dict]]:
    """按博主采集：打开博主主页收集笔记 → 逐个详情页提取全量内容并入库。

    每个笔记提取：轮播图全部图片 + 视频（含封面 poster）+ 正文 caption；
    入库时通过 meta_map 同步写入 caption 并建立 inspiration_bloggers 博主关联。

    风控缓解：详情页访问间隔 2~4s（可经 config["detail_delay"] 调整）；
    单个详情页提取失败跳过并记录，不影响其余笔记。

    Args:
        page: Playwright 页面对象。
        task_id: 采集任务 ID。
        blogger_id: 博主 ID。
        config: 任务配置字典。
        img_dir: 图片存储目录。
        videos_dir: 视频存储目录。
        today: 日期字符串。
        httpx_module: httpx 模块。
        browser_cookies: 浏览器 Cookie 字典。
        existing_url_set: 已存在 URL 集合。
        content_hash_set: 内容 MD5 集合。

    Returns:
        (items_found, items_added, notes_log) — 提取数 / 入库数 / 每篇笔记漏斗。
    """
    # 博主主页 URL：显式 profile_url 优先，否则用 platform_user_id 拼
    profile_url = config.get("profile_url")
    if not profile_url:
        puid = config.get("platform_user_id")
        if puid:
            profile_url = f"https://www.xiaohongshu.com/user/profile/{puid}"
    if not profile_url:
        raise RuntimeError(
            "按博主采集缺少 profile_url / platform_user_id"
        )

    max_notes = int(config.get("max_notes", 50))
    max_scrolls = int(config.get("max_scrolls", 15))
    detail_delay = float(config.get("detail_delay", 3.0))  # 详情页间隔（秒）

    print(f"按博主采集：blogger_id={blogger_id}，目标 {max_notes} 篇笔记")
    note_urls = collect_blogger_note_urls(
        page, profile_url, max_notes, max_scrolls
    )
    print(f"  收集到 {len(note_urls)} 篇笔记")

    items_found = 0
    items_added = 0
    notes_log: list[dict] = []
    # 笔记 URL → {caption, blogger_id}：图片/视频入库共用
    meta_map: dict[str, dict] = {}

    for i, note_url in enumerate(note_urls, 1):
        try:
            detail = extract_note_detail(page, note_url)
        except ScraperBlockedError as e:
            notes_log.append({"note": note_url, "error": str(e)[:200]})
            if e.is_fatal:
                # 致命风控（验证码/登录墙/限流/账号异常）：继续访问只会加重风控，
                # 抛给调用方停止整轮（风控类错误不重试）
                print(f"  ⛔ {e.message}，停止本轮采集")
                raise
            # 非致命（笔记已删除等）：跳过本篇，继续其余笔记
            print(f"  [{i}/{len(note_urls)}] 跳过：{e.message}")
            time.sleep(detail_delay)
            continue
        except Exception as e:
            print(
                f"  [{i}/{len(note_urls)}] 详情页提取失败:"
                f" {str(e)[:80]}"
            )
            notes_log.append({"note": note_url, "error": str(e)[:200]})
            time.sleep(detail_delay)
            continue

        caption = detail.get("caption") or ""
        meta_map[note_url] = {
            "caption": caption,
            "blogger_id": blogger_id,
            "tags": detail.get("tags") or [],  # 话题存档（scraper_hashtags）
            "hashtags_saved": False,
        }

        img_pairs = [(note_url, u) for u in detail.get("img_urls") or []]
        video_pairs = [(note_url, u) for u in detail.get("video_urls") or []]
        items_found += len(img_pairs) + len(video_pairs)

        # 图片下载入库（多图逐张；同笔记共享 caption + 博主关联）
        added = 0
        if img_pairs:
            remaining = max(50, len(img_pairs) * 2)  # 博主模式单笔记不设严格上限
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
            )
            print(
                f"    图片 +{added}（跳过 已存在{sk_ex} MD5{sk_dup}"
                f" HTTP{sk_h} 网络{sk_n}）"
            )
        # 视频下载入库（mp4 + ffmpeg 缩略图 + caption + 博主关联）
        v_added = 0
        if video_pairs:
            v_added, v_skipped = download_videos(
                video_pairs,
                task_id,
                existing_url_set,
                max(20, len(video_pairs) * 2),
                videos_dir,
                today,
                httpx_module,
                browser_cookies,
                meta_map,
            )
            print(f"    视频 +{v_added}（跳过 {v_skipped}）")
        items_added += added + v_added

        note_id = note_url.rstrip("/").split("/")[-1][:12]
        print(
            f"  [{i}/{len(note_urls)}] 笔记 {note_id}：图 {len(img_pairs)}"
            f" 视频 {len(video_pairs)} 正文 {len(caption)} 字"
            f"（入库 {added + v_added}）"
        )
        notes_log.append(
            {
                "note": note_url,
                "img_count": len(img_pairs),
                "video_count": len(video_pairs),
                "caption_len": len(caption),
                "added": added + v_added,
            }
        )

        # 详情页访问间隔（防风控，默认 2~4s 随机）
        time.sleep(
            random.uniform(
                max(1.0, detail_delay - 1.0), detail_delay + 1.0
            )
        )

    if _HASHTAG_SAVED_COUNT[0] > 0:
        print(
            f"本次采集命中话题 {_HASHTAG_SAVED_COUNT[0]} 个"
            f"（已存档 scraper_hashtags，可用于定时采集关键词）"
        )
    return items_found, items_added, notes_log
