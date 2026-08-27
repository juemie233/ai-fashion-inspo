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
)
from .scraper_download import download_batch, download_videos, _HASHTAG_SAVED_COUNT


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
    """
    result: dict = {"img_urls": [], "video_urls": [], "caption": "", "tags": []}
    try:
        page.goto(note_url, wait_until="domcontentloaded", timeout=30000)
    except Exception:
        return result

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
    """
    note_urls: list[str] = []
    try:
        page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
    except Exception:
        return note_urls
    try:
        page.wait_for_selector(
            "section.note-item, a[href*='/explore/']", timeout=15000
        )
    except Exception:
        pass

    last_count = 0
    no_new = 0
    for _ in range(max_scrolls):
        # 收集当前页全部笔记链接（去重）
        for link in page.query_selector_all("a[href*='/explore/']"):
            href = link.get_attribute("href") or ""
            url = (
                href
                if href.startswith("http")
                else f"https://www.xiaohongshu.com{href}"
            )
            if url not in note_urls:
                note_urls.append(url)
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


def search_xiaohongshu(
    page,
    keyword: str,
    need_count: int,
    sort_type: str = "general",
) -> tuple[list[tuple[str, str]], dict]:
    """在已登录的页面上搜索并提取图片 URL。

    采用触底循环滚动策略，持续滚到懒加载不出新卡片或达到上限为止。
    从每张卡片中提取多张图片（轮播帖），最大化采集数量。

    Args:
        page: Playwright 页面对象。
        keyword: 搜索关键词。
        need_count: 本次搜索还需采集的数量（剩余需求）。
        sort_type: 排序方式 — "general"(综合) / "time_descending"(最新) / "popularity_descending"(最热)。

    Returns:
        (pairs, funnel_dict): 每张图片的 (笔记页面 URL, 图片 CDN URL) 列表和漏斗统计数据
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

    # 等待搜索结果卡片渲染完成
    try:
        page.wait_for_selector("section.note-item", timeout=15000)
        print("  搜索结果已渲染")
    except Exception:
        print("  等待搜索结果超时，尝试继续...")
    # 拟人化：页面加载后随机停顿 + 偶发鼠标移动，模拟真人浏览前先看一页
    _rdsleep(1.5, 3.5)
    if random.random() < 0.7:
        _human_mouse_move(page)

    # ── 触底循环滚动 ──
    MAX_SCROLLS = 10  # 滚动硬上限（真人不会滚 30 次）
    CONSECUTIVE_NO_NEW = 1  # 连续 N 次无新卡片即视为到底
    target_cards = max(10, int(need_count * 1.5))  # 已获取足够卡片即停
    no_new_count = 0
    last_card_count = 0

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
        cards_now = len(page.query_selector_all("section.note-item"))

        if cards_now == last_card_count:
            no_new_count += 1
        else:
            no_new_count = 0
            last_card_count = cards_now

        # 已获取足够卡片，提前停止
        if cards_now >= target_cards:
            print(
                f"  滚动 {scroll_i + 1} 次后已获取 {cards_now} 个卡片"
                f"（目标 {target_cards}），停止滚动"
            )
            break

        # 连续无新内容，页面已到底
        if no_new_count >= CONSECUTIVE_NO_NEW:
            print(
                f"  连续 {CONSECUTIVE_NO_NEW} 次无新内容，页面已到底"
                f"（{cards_now} 卡片）"
            )
            break

    # ── DOM 提取 ──
    cards = page.query_selector_all("section.note-item")
    total_cards = len(cards)
    print(f"  共找到 {total_cards} 个笔记卡片，开始提取图片...")

    pairs: list[tuple[str, str]] = []  # (笔记页面 URL, 图片 CDN URL)
    seen: set[str] = set()
    cards_with_img = 0
    cards_without_img = 0
    skipped_small = 0
    skipped_icon = 0

    for card in cards[: need_count * 2]:
        try:
            # 提取笔记页面链接
            note_href = ""
            link_el = card.query_selector("a")
            if link_el:
                note_href = link_el.get_attribute("href") or ""
            note_url = (
                f"https://www.xiaohongshu.com{note_href}"
                if note_href.startswith("/")
                else note_href
            )

            # 从每张卡片中提取所有图片（轮播帖含多图）
            imgs = card.query_selector_all("img")
            if not imgs:
                cards_without_img += 1
                continue
            cards_with_img += 1

            for img in imgs:
                src = (
                    img.get_attribute("src")
                    or img.get_attribute("data-src")
                    or ""
                )
                if not src or not src.startswith("http"):
                    continue
                # 过滤图标类 URL
                if any(k in src.lower() for k in ["icon", "avatar", "logo", "favicon", "emoji"]):
                    skipped_icon += 1
                    continue
                # 过滤小尺寸（< 100px 任意边）
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
                    pairs.append((note_url, src))
        except Exception:
            continue

    # ── 漏斗日志 ──
    funnel = {
        "cards_total": total_cards,
        "cards_with_img": cards_with_img,
        "cards_without_img": cards_without_img,
        "skipped_small": skipped_small,
        "skipped_icon": skipped_icon,
        "urls_extracted": len(pairs),
        "target": need_count,
    }
    print(f"  ┌─ 提取漏斗 ─────────────────────────────")
    print(f"  │ DOM 卡片总数: {total_cards}")
    print(f"  │ 有图片的卡片: {cards_with_img}")
    print(f"  │ 无图片的卡片: {cards_without_img}")
    print(f"  │ 跳过小尺寸:   {skipped_small}")
    print(f"  │ 跳过图标:     {skipped_icon}")
    print(f"  │ 提取到 URL:   {len(pairs)}")
    print(f"  │ 目标数量:     {need_count}")
    print(f"  └──────────────────────────────────────────")

    return pairs[: need_count * 2], funnel


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
