"""独立爬虫执行脚本 — CDP 连接用户真实 Chrome，零检测采集。

调用方式:
  python scripts/run_scraper.py <task_id>
"""

import json
import os as _os
import random
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
import urllib.parse

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings
from app.database import async_session, init_db
from app.db_migrations import ensure_schema
from app.models.inspiration import Inspiration
from app.models.scraper import ScraperTask

# ── UTF-8 输出 ──
# 注意：必须开启 line_buffering，否则 stdout 被重新包装成带缓冲的 TextIOWrapper，
# print 进度日志会一直积压在缓冲区，直到进程退出才落盘，导致日志看起来「卡住不动」。
# 仅在作为主脚本执行时包装：本文件会被测试通过 importlib 导入以验证纯函数，
# 此时不能触碰宿主进程的标准流（否则破坏 pytest 捕获）。
import io
if __name__ == "__main__":
    try:
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
        )
        sys.stderr = io.TextIOWrapper(
            sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True
        )
    except Exception:
        pass


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# 搜索排序方式（固定顺序，作为断点续采执行计划的基础）
SORT_TYPES = ["general", "popularity_descending", "time_descending"]


def _rdsleep(lo=0.5, hi=2.0):
    time.sleep(random.uniform(lo, hi))


def _human_mouse_move(page):
    """随机移动鼠标到页面某处，模拟真人浏览时的无意识动作。"""
    try:
        vp = page.viewport_size or {"width": 1920, "height": 1080}
        w, h = vp.get("width", 1920), vp.get("height", 1080)
        page.mouse.move(
            random.randint(int(w * 0.15), int(w * 0.85)),
            random.randint(int(h * 0.15), int(h * 0.85)),
        )
    except Exception:
        pass  # 鼠标移动失败不影响主流程


def _human_scroll(page, steps=None):
    """分步随机滚动到底部：随机步长 + 随机停顿，避免一步到底的机器特征。

    最终仍滚动到底以触发懒加载，但过程更接近真人浏览。
    分步数控制在 1~2 步，避免滚动事件被过度放大（每步都是一次可见滚动）。
    """
    steps = steps or random.randint(1, 2)
    for _ in range(steps):
        page.evaluate(f"window.scrollBy(0, {random.randint(400, 900)})")
        _rdsleep(0.6, 1.5)
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")


# ═══════════════════════════════════════════════════════════════
#  平台通用：下载请求头 / 登录检测（小红书与抖音共用）
# ═══════════════════════════════════════════════════════════════


# 各平台媒体 CDN 下载所需的 Referer（缺失或填错平台域名会被 CDN 拒绝）
_DOWNLOAD_REFERERS = {
    "xiaohongshu": "https://www.xiaohongshu.com/",
    "douyin": "https://www.douyin.com/",
}

# 登录检测用的会话 Cookie 名（命中任一即视为已登录）
_LOGIN_COOKIE_NAMES = {
    "xiaohongshu": ("web_session", "a1"),  # 历史版本登录态为 a1
    "douyin": ("SESSDATA",),
}

# 未登录时引导用户扫码的平台首页
_PLATFORM_HOME_URLS = {
    "xiaohongshu": "https://www.xiaohongshu.com/explore",
    "douyin": "https://www.douyin.com/",
}

_PLATFORM_NAMES = {"xiaohongshu": "小红书", "douyin": "抖音"}

_DOWNLOAD_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
)

# 单视频下载大小上限（字节）：抖音短视频普遍更小，50MB 控磁盘；
# 小红书沿用历史 100MB。超限视频流式中断丢弃并在漏斗记 skip。
_DEFAULT_VIDEO_MAX_BYTES = 100 * 1024 * 1024
_MAX_VIDEO_BYTES = {
    "xiaohongshu": 100 * 1024 * 1024,
    "douyin": 50 * 1024 * 1024,
}


def _build_download_headers(platform: str, cookies: dict | None = None) -> dict:
    """构建平台匹配的媒体下载请求头。

    此前此处硬编码小红书 Referer，抖音 CDN 下载一律被拒；
    现按平台取对应 Referer，并附带浏览器 Cookie 以通过鉴权。
    """
    req_headers = {
        "Referer": _DOWNLOAD_REFERERS.get(platform, "https://www.xiaohongshu.com/"),
        "User-Agent": _DOWNLOAD_UA,
    }
    if cookies:
        req_headers["Cookie"] = "; ".join(
            f"{c['name']}={c['value']}" for c in cookies.values()
        )
    return req_headers


def _platform_has_login(cookies: list[dict], platform: str) -> bool:
    """根据浏览器 Cookie 判断平台是否处于登录状态。"""
    names = _LOGIN_COOKIE_NAMES.get(platform, ())
    domain_hint = _PLATFORM_NAMES.get(platform, "")
    matched_domains: set[str] = set()
    for c in cookies:
        dom = c.get("domain", "")
        if domain_hint == "小红书" and "xiaohongshu" in dom:
            matched_domains.add(dom)
            if c.get("name") in names:
                return True
        elif domain_hint == "抖音" and "douyin" in dom:
            matched_domains.add(dom)
            if c.get("name") in names:
                return True
    # 无任何平台域名的 Cookie 视为未登录
    return False


def _ensure_platform_login(context, page, platform: str, timeout: int = 180) -> bool:
    """确保平台已登录：未登录则导航到首页并轮询等待用户扫码。

    小红书与抖音共用一套逻辑（差异仅在首页 URL 与会话 Cookie 名）。
    超时后不强制失败——沿用原行为，以当前状态尝试采集，
    由漏斗统计自然暴露「无结果」问题。

    Returns:
        最终是否检测到登录状态。
    """
    name = _PLATFORM_NAMES.get(platform, platform)

    def _check() -> bool:
        return _platform_has_login(context.cookies(), platform)

    if _check():
        print(f"{name}已在登录状态，直接开始采集")
        return True

    print(f"\n{'='*50}")
    print(f" >>> 请在 Chrome 中登录{name} <<<")
    print(" 已自动打开平台首页，请扫码登录")
    print(f" 登录完成后脚本自动检测并继续（{timeout}s 超时）")
    print(f"{'='*50}")

    # 将空白标签页导航到平台首页（未登录时展示扫码入口），
    # 避免停留在 about:blank 让用户误以为卡死
    try:
        page.goto(
            _PLATFORM_HOME_URLS[platform],
            wait_until="domcontentloaded",
            timeout=30000,
        )
    except Exception as e:
        print(f"自动跳转{name}首页失败（可手动在地址栏输入）: {e}")

    for waited in range(0, timeout, 5):
        time.sleep(5)
        if _check():
            print(f"检测到登录 ({waited + 5}s)")
            time.sleep(1)
            return True
        if (waited + 5) % 30 == 0:
            print(f"  等待登录... ({waited + 5}s / {timeout}s)")
    print("登录超时，将尝试当前状态")
    return False


def _clean_media_url(src: str) -> str:
    """清洗媒体 URL：去空白、补全协议头。"""
    src = (src or "").strip()
    if src.startswith("//"):
        src = "https:" + src
    return src


def _is_content_image(src: str) -> bool:
    """判断 URL 是否为「内容图」（排除头像/图标/logo/角标等非素材图）。"""
    if not src.startswith("http"):
        return False
    low = src.lower()
    skip_kw = ("avatar", "icon", "logo", "emoji", "favicon", "qrcode", "qr_code", "verified")
    return not any(k in low for k in skip_kw)


def _extract_video_thumbnail_sync(video_path: Path, today: str) -> str | None:
    """用 ffmpeg 提取视频首帧缩略图（同步 subprocess），失败返回 None。"""
    import subprocess

    thumb_dir = settings.thumbnails_dir / today
    thumb_dir.mkdir(parents=True, exist_ok=True)
    thumb_name = f"thumb_{video_path.stem}.jpg"
    thumb_path = thumb_dir / thumb_name
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-ss", "1", "-i", str(video_path),
             "-frames:v", "1", "-vf", "scale=400:-2", str(thumb_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except Exception:
        return None
    if thumb_path.exists() and thumb_path.stat().st_size > 0:
        return f"thumbnails/{today}/{thumb_name}"
    return None


def _extract_note_detail(page, note_url: str) -> dict:
    """打开单个笔记详情页，提取全部内容。

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
        src = _clean_media_url(img.get_attribute("src") or img.get_attribute("data-src") or "")
        if not src or not _is_content_image(src) or src in seen_imgs:
            continue
        seen_imgs.add(src)
        result["img_urls"].append(src)

    # ── 视频：<video> 的 src（或 <source> 子标签），封面 poster 一并作为图片采集 ──
    for video in page.query_selector_all("video"):
        vsrc = _clean_media_url(video.get_attribute("src") or "")
        if not vsrc:
            source_el = video.query_selector("source")
            if source_el:
                vsrc = _clean_media_url(source_el.get_attribute("src") or "")
        if vsrc and vsrc not in result["video_urls"]:
            result["video_urls"].append(vsrc)
        poster = _clean_media_url(video.get_attribute("poster") or "")
        if poster and _is_content_image(poster) and poster not in seen_imgs:
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
    import re as _re

    if result["caption"]:
        tags = _re.findall(r"#([^\s#，,。！？.!?]{1,30})", result["caption"])
        result["tags"] = [t.strip() for t in tags if t.strip()]

    return result


def _collect_blogger_note_urls(
    page, profile_url: str, max_notes: int, max_scrolls: int = 15
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
        page.wait_for_selector("section.note-item, a[href*='/explore/']", timeout=15000)
    except Exception:
        pass

    last_count = 0
    no_new = 0
    for _ in range(max_scrolls):
        # 收集当前页全部笔记链接（去重）
        for link in page.query_selector_all("a[href*='/explore/']"):
            href = link.get_attribute("href") or ""
            url = href if href.startswith("http") else f"https://www.xiaohongshu.com{href}"
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
#  搜索与提取
# ═══════════════════════════════════════════════════════════════

def _search_xiaohongshu(page, keyword: str, need_count: int, sort_type: str = "general") -> tuple[list[tuple[str, str]], dict]:
    """在已登录的页面上搜索并提取图片 URL。

    采用触底循环滚动策略，持续滚到懒加载不出新卡片或达到上限为止。
    从每张卡片中提取多张图片（轮播帖），最大化采集数量。

    Args:
        need_count: 本次搜索还需采集的数量（剩余需求），用于「够用即停」判断
        sort_type: 排序方式 — "general"(综合) / "time_descending"(最新) / "popularity_descending"(最热)

    Returns:
        (pairs, funnel_dict): 每张图片的 (笔记页面 URL, 图片 CDN URL) 列表和该次搜索的漏斗统计数据
    """
    if page.is_closed():
        raise RuntimeError("页面已关闭")

    sort_labels = {
        "general": "综合",
        "time_descending": "最新",
        "popularity_descending": "最热",
    }
    sort_label = sort_labels.get(sort_type, sort_type)

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
    CONSECUTIVE_NO_NEW = 1  # 连续 N 次无新卡片即视为到底（一次确认足够，减少白滚）
    # 已获取足够卡片即停（实测每卡片≈1张图，1.5倍余量足够）
    target_cards = max(10, int(need_count * 1.5))
    no_new_count = 0
    last_card_count = 0

    for scroll_i in range(MAX_SCROLLS):
        # 拟人化滚动：偶发鼠标移动 + 分步随机滚到底 + 随机停顿
        if random.random() < 0.6:
            _human_mouse_move(page)
        _human_scroll(page)
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
            print(f"  滚动 {scroll_i + 1} 次后已获取 {cards_now} 个卡片（目标 {target_cards}），停止滚动")
            break

        # 连续无新内容，页面已到底
        if no_new_count >= CONSECUTIVE_NO_NEW:
            print(f"  连续 {CONSECUTIVE_NO_NEW} 次无新内容，页面已到底（{cards_now} 卡片）")
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
            # 提取笔记页面链接（作为「原始链接」，而非图片 CDN 直链）
            note_href = ""
            link_el = card.query_selector("a")
            if link_el:
                note_href = link_el.get_attribute("href") or ""
            note_url = (
                f"https://www.xiaohongshu.com{note_href}"
                if note_href.startswith("/") else note_href
            )

            # 从每张卡片中提取所有图片（轮播帖含多图）
            imgs = card.query_selector_all("img")
            if not imgs:
                cards_without_img += 1
                continue
            cards_with_img += 1

            for img in imgs:
                src = img.get_attribute("src") or img.get_attribute("data-src") or ""
                if not src or not src.startswith("http"):
                    continue
                # 过滤图标类 URL
                if any(k in src.lower() for k in ["icon", "avatar", "logo", "favicon", "emoji"]):
                    skipped_icon += 1
                    continue
                # 过滤小尺寸（< 200px 任意边，比之前的 100px 更严格但不影响数量）
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

    return pairs[:need_count * 2], funnel  # 多返回一些，下载阶段有重试和丢弃


# ═══════════════════════════════════════════════════════════════
#  下载
# ═══════════════════════════════════════════════════════════════

# 单条话题的来源明细保留条数（防 source_meta 无限膨胀）
_HASHTAG_META_MAX = 10
# 每篇笔记提取的话题数上限（防脏数据）
_HASHTAG_PER_NOTE_MAX = 20
# 本次脚本会话累计处理的话题数（含重复命中，供任务完成日志统计）
_HASHTAG_SAVED_COUNT = [0]


def _ensure_hashtag_table(conn) -> None:
    """确保话题存档表存在（脚本独立进程兜底；主库由 Alembic 迁移建表）。"""
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS scraper_hashtags ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name VARCHAR(64) NOT NULL UNIQUE, "
            "seen_count INTEGER NOT NULL DEFAULT 1, "
            "first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "source_kind VARCHAR(16) NOT NULL DEFAULT 'blogger', "
            "source_id INTEGER, "
            "note_url TEXT, "
            "source_meta TEXT)"
        )
        conn.commit()
    except Exception:
        pass  # 建表失败不阻塞采集主流程（后续写入同样静默跳过）


def _save_hashtags(conn, meta: dict | None, note_url: str) -> int:
    """把一篇笔记的话题 upsert 进 scraper_hashtags（同笔记只处理一次）。

    幂等：meta["hashtags_saved"] 标记——同一笔记的图片/视频多次入库时
    只累计一次计数。返回本次处理的话题数（0 = 已处理/无话题）。

    Args:
        conn: 攒批提交用的 sqlite 连接（与素材入库同连接，事务一致）。
        meta: meta_map 中该笔记的元数据（含 caption/blogger_id/tags）。
        note_url: 笔记页面 URL（作为来源明细记录）。
    """
    if not meta or meta.get("hashtags_saved"):
        return 0
    tags = meta.get("tags") or []
    meta["hashtags_saved"] = True  # 先标记：即使后续失败也不再重复处理
    if not tags:
        return 0
    now_str = utcnow().strftime("%Y-%m-%d %H:%M:%S")
    kind = meta.get("source_kind", "blogger")
    source_id = meta.get("blogger_id")
    saved = 0
    for tag in tags[:_HASHTAG_PER_NOTE_MAX]:
        name = (tag or "").strip().lstrip("#").strip()
        if not name or len(name) > 64:
            continue
        try:
            row = conn.execute(
                "SELECT seen_count, source_meta FROM scraper_hashtags WHERE name = ?",
                (name,),
            ).fetchone()
            item = {
                "kind": kind,
                "id": source_id,
                "note_url": note_url,
                "at": now_str,
            }
            if row is None:
                conn.execute(
                    "INSERT INTO scraper_hashtags "
                    "(name, seen_count, last_seen_at, source_kind, source_id, "
                    " note_url, source_meta) "
                    "VALUES (?, 1, ?, ?, ?, ?, ?)",
                    (name, now_str, kind, source_id, note_url,
                     json.dumps([item], ensure_ascii=False)),
                )
            else:
                try:
                    items = json.loads(row[1]) if row[1] else []
                except Exception:
                    items = []
                items = (items + [item])[-_HASHTAG_META_MAX:]
                conn.execute(
                    "UPDATE scraper_hashtags SET seen_count = seen_count + 1, "
                    "last_seen_at = ?, source_kind = ?, source_id = ?, "
                    "note_url = ?, source_meta = ? WHERE name = ?",
                    (now_str, kind, source_id, note_url,
                     json.dumps(items, ensure_ascii=False), name),
                )
            saved += 1
            _HASHTAG_SAVED_COUNT[0] += 1
        except Exception:
            pass  # 话题写入失败不影响采集主流程
    return saved


def _download_batch(
    urls: list[tuple[str, str]],
    task_id: int,
    existing_url_set: set[str],
    remaining: int,
    img_dir: Path,
    today: str,
    httpx_module,
    cookies: dict | None = None,
    content_hash_set: set[str] | None = None,
    meta_map: dict[str, dict] | None = None,
    platform: str = "xiaohongshu",
) -> tuple[int, int, int, int, int]:
    """下载一批图片，立即入库。使用同步 sqlite3 避免 event loop 冲突。

    urls 中每项为 (笔记页面 URL, 图片 CDN URL)：笔记页面 URL 存入 source_url 作为
    「原始链接」，图片 CDN URL 用于下载与去重。

    meta_map（可选）：笔记页面 URL → {"caption": str, "blogger_id": int}
    —— 按博主采集时传入，图片入库同步写入笔记正文 caption，并建立
    inspiration_bloggers 关联（博主标记）。

    platform：下载请求头按平台取 Referer（xiaohongshu | douyin），CDN 鉴权必需。

    去重策略（三层）：
    1. 图片 URL 内存去重 — 同次运行内相同图片不重复下载
    2. DB 墓碑表去重 — 跨次采集相同图片 URL 不重复入库
    3. 内容 MD5 去重 — 同一图片不同 URL（CDN 多节点）不重复入库
    """
    import hashlib
    import sqlite3 as _sqlite3

    # 构建平台匹配的请求头（带浏览器 Cookie 以通过 CDN 鉴权）
    req_headers = _build_download_headers(platform, cookies)

    db_path = settings.storage_root.parent / "fashion_inspo.db"

    # 按图片 URL 去重（同一图片可能在不同卡片/搜索中重复出现）
    unique: list[tuple[str, str]] = []
    _seen_url: set[str] = set()
    for note_url, img_url in urls:
        if img_url not in _seen_url:
            _seen_url.add(img_url)
            unique.append((note_url, img_url))

    # 查询这批图片 URL 及笔记 URL 中已在墓碑表中的（同步查询，包括已删除的素材 URL）。
    # 删除素材时写入的是素材的 source_url（笔记页地址），采集成功时写入的是图片 CDN
    # 地址，两者都需匹配：任一命中即视为「已删除/已采集」，跳过入库。
    if unique:
        img_urls = [img_url for _, img_url in unique]
        note_urls = [note_url for note_url, _ in unique if note_url]
        conn = None
        try:
            conn = _sqlite3.connect(str(db_path))
            # 确保墓碑表存在
            conn.execute(
                "CREATE TABLE IF NOT EXISTS scraper_seen_urls "
                "(source_url TEXT PRIMARY KEY, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
            conn.commit()
            placeholders = ",".join("?" * (len(img_urls) + len(note_urls)))
            cur = conn.execute(
                f"SELECT source_url FROM scraper_seen_urls WHERE source_url IN ({placeholders})",
                img_urls + note_urls,
            )
            db_existing = {r[0] for r in cur.fetchall()}
            existing_url_set.update(db_existing)
        except Exception:
            pass
        finally:
            if conn is not None:
                conn.close()

    added = 0
    skipped_existing = 0
    skipped_content_dup = 0
    skipped_non200 = 0
    skipped_network = 0

    # 单连接贯穿整批写入：攒批提交（每 20 条一次），避免每张图开连接
    # 频繁抢占 SQLite 写锁，与 API 服务/worker 并发时显著降低锁冲突。
    _BATCH_COMMIT = 20
    batch_conn = None
    pending_in_batch = 0
    # 本批待回填向量的素材 ID：攒批后合并为一个向量回填任务，
    # 避免每张图各建一个任务导致任务队列膨胀（此前 75 张图=75 个任务）。
    backfill_ids: list[str] = []
    try:
        batch_conn = _sqlite3.connect(str(db_path))
        _ensure_hashtag_table(batch_conn)  # 话题存档表兜底（独立进程）
    except Exception:
        batch_conn = None

    def _commit_batch():
        """提交当前攒批的写入（无写入或连接不可用时跳过）。

        提交前把本批素材合并为一个向量回填任务入队，保证素材行与
        回填任务在同一事务内提交（要么都写，要么都回滚）。
        """
        nonlocal pending_in_batch
        if batch_conn is not None and pending_in_batch > 0:
            if backfill_ids:
                now_str = utcnow().strftime("%Y-%m-%d %H:%M:%S")
                batch_conn.execute(
                    "INSERT INTO task_queue (type, status, progress, total, done, result, "
                    "max_retries, retry_count, created_at, updated_at) "
                    "VALUES ('vector_backfill', 'pending', 0, ?, 0, ?, 2, 0, ?, ?)",
                    (len(backfill_ids),
                     json.dumps({"inspiration_ids": list(backfill_ids)}),
                     now_str, now_str),
                )
                backfill_ids.clear()
            batch_conn.commit()
            pending_in_batch = 0

    for note_url, img_url in unique:
        if added >= remaining:
            break
        # 图片 URL 或笔记 URL 命中墓碑/已见集合：删除过的素材不再重复采集
        if img_url in existing_url_set or note_url in existing_url_set:
            skipped_existing += 1
            continue

        for attempt in range(1, 4):
            try:
                resp = httpx_module.get(img_url, headers=req_headers,
                                         timeout=30, follow_redirects=True)
                if resp.status_code != 200:
                    skipped_non200 += 1
                    break
                ext = ".jpg"
                ct = resp.headers.get("content-type", "")
                if "png" in ct: ext = ".png"
                elif "webp" in ct: ext = ".webp"
                fname = f"{str(uuid.uuid4()).replace('-', '')[:16]}{ext}"
                fpath = img_dir / fname
                content = resp.content
                fpath.write_bytes(content)

                # 内容 MD5 去重：相同图片不同 URL 不重复入库
                if content_hash_set is not None:
                    content_md5 = hashlib.md5(content).hexdigest()
                    if content_md5 in content_hash_set:
                        fpath.unlink()  # 删除刚下载的重复文件
                        skipped_content_dup += 1
                        # 将重复 URL 写入墓碑表，避免下次采集重复下载
                        try:
                            _conn = _sqlite3.connect(str(db_path))
                            _conn.execute(
                                "INSERT OR IGNORE INTO scraper_seen_urls (source_url) VALUES (?)",
                                (img_url,),
                            )
                            _conn.commit()
                            _conn.close()
                        except Exception:
                            pass
                        existing_url_set.add(img_url)
                        break
                    content_hash_set.add(content_md5)

                # 同步写入数据库（同一事务：素材行 + 墓碑 + 向量回填任务）。
                # 失败时回滚本批未提交部分并删除已下载文件，避免孤儿文件/孤儿行。
                if batch_conn is None:
                    raise RuntimeError("数据库连接不可用")
                try:
                    insp_id = str(uuid.uuid4())
                    rel_path = f"images/{today}/{fname}"
                    now_str = utcnow().strftime("%Y-%m-%d %H:%M:%S")
                    # 与主库 content_hash 列一致（SHA-256），供上传/管理页索引查重
                    content_sha256 = hashlib.sha256(content).hexdigest()
                    # 按博主采集：同笔记的图片共享正文 caption，并关联博主
                    meta = meta_map.get(note_url) if meta_map else None
                    caption_val = (meta or {}).get("caption")
                    blogger_id = (meta or {}).get("blogger_id")
                    _save_hashtags(batch_conn, meta, note_url)  # 话题存档（幂等，每笔记一次）
                    batch_conn.execute(
                        "INSERT INTO inspirations (id, source_type, source_url, file_path, "
                        "thumbnail_path, media_type, dominant_colors, is_favorite, "
                        "quality_status, content_hash, caption, scraper_task_id, "
                        "created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, NULL, ?, NULL, 0, 'pending', ?, ?, ?, ?, ?)",
                        (insp_id, "scraper", note_url or img_url, rel_path, "image",
                         content_sha256, caption_val, task_id, now_str, now_str),
                    )
                    if blogger_id:
                        batch_conn.execute(
                            "INSERT OR IGNORE INTO inspiration_bloggers "
                            "(inspiration_id, blogger_id, confidence) VALUES (?, ?, 1.0)",
                            (insp_id, blogger_id),
                        )
                    batch_conn.execute(
                        "INSERT OR IGNORE INTO scraper_seen_urls (source_url) VALUES (?)",
                        (img_url,),
                    )
                    # 向量回填任务攒批：本批素材合并为一个任务，在 _commit_batch 时入队
                    backfill_ids.append(insp_id)
                    pending_in_batch += 1
                    if pending_in_batch >= _BATCH_COMMIT:
                        _commit_batch()
                except Exception:
                    try:
                        batch_conn.rollback()
                        pending_in_batch = 0
                    except Exception:
                        pass
                    try:
                        fpath.unlink()
                    except Exception:
                        pass
                    raise  # 重新抛出，让外层重试逻辑处理

                added += 1
                existing_url_set.add(img_url)

                # 下载间隔：模拟人类逐张保存的行为
                time.sleep(random.uniform(0.3, 1.0))
                break
            except Exception as e:
                err = str(e)[:60]
                if attempt < 3:
                    backoff = 2 ** attempt
                    print(f"    下载重试 ({attempt}/3) {img_url[:40]}... ({err})，{backoff}s 后重试")
                    time.sleep(backoff)
                else:
                    print(f"    下载失败 {img_url[:40]}... ({err})")
                    skipped_network += 1

    # 收尾：提交剩余攒批并关闭连接
    _commit_batch()
    if batch_conn is not None:
        batch_conn.close()

    return added, skipped_existing, skipped_non200, skipped_network, skipped_content_dup


def _download_videos(
    video_pairs: list[tuple[str, str]],
    task_id: int,
    existing_url_set: set[str],
    remaining: int,
    videos_dir: Path,
    today: str,
    httpx_module,
    cookies: dict | None = None,
    meta_map: dict[str, dict] | None = None,
    platform: str = "xiaohongshu",
) -> tuple[int, int]:
    """下载一批短视频并入库为 video 类型（同步 sqlite3 + 同步 ffmpeg 缩略图）。

    video_pairs 每项为 (笔记页面 URL, 视频 CDN URL)。去重策略与图片一致
    （URL 内存去重 + 墓碑表去重）；视频不做内容哈希去重（同一视频多 CDN 节点
    罕见，且逐字节哈希代价高）。

    meta_map（可选）：笔记页面 URL → {"caption": str, "blogger_id": int}
    —— 按博主采集时传入，视频入库同步写入笔记正文 caption 并关联博主。

    platform：下载请求头按平台取 Referer（xiaohongshu | douyin），CDN 鉴权必需。
    大小上限按平台区分（_MAX_VIDEO_BYTES，抖音单条更小以控磁盘占用）。

    Returns:
        (added, skipped): 成功入库数与跳过数（已存在 / 下载失败）。
    """
    import sqlite3 as _sqlite3

    # 构建平台匹配的请求头（带浏览器 Cookie 以通过 CDN 鉴权）
    req_headers = _build_download_headers(platform, cookies)

    # 单个视频下载大小上限：避免超大视频撑爆磁盘
    max_video_bytes = _MAX_VIDEO_BYTES.get(platform, _DEFAULT_VIDEO_MAX_BYTES)

    db_path = settings.storage_root.parent / "fashion_inspo.db"

    # 视频 URL 内存去重
    unique: list[tuple[str, str]] = []
    _seen_url: set[str] = set()
    for note_url, video_url in video_pairs:
        if video_url and video_url not in _seen_url:
            _seen_url.add(video_url)
            unique.append((note_url, video_url))

    # 查询这批视频 URL 中已在墓碑表中的
    if unique:
        video_urls = [u for _, u in unique]
        conn = None
        try:
            conn = _sqlite3.connect(str(db_path))
            conn.execute(
                "CREATE TABLE IF NOT EXISTS scraper_seen_urls "
                "(source_url TEXT PRIMARY KEY, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
            conn.commit()
            placeholders = ",".join("?" * len(video_urls))
            cur = conn.execute(
                f"SELECT source_url FROM scraper_seen_urls WHERE source_url IN ({placeholders})",
                video_urls,
            )
            db_existing = {r[0] for r in cur.fetchall()}
            existing_url_set.update(db_existing)
        except Exception:
            pass
        finally:
            if conn is not None:
                conn.close()

    added = 0
    skipped = 0
    pending_in_batch = 0
    _BATCH_COMMIT = 5  # 视频入库量小，攒批阈值降低
    batch_conn = None
    try:
        batch_conn = _sqlite3.connect(str(db_path))
        _ensure_hashtag_table(batch_conn)  # 话题存档表兜底（独立进程）
    except Exception:
        batch_conn = None

    def _commit_videos():
        """提交当前攒批的视频写入。"""
        nonlocal pending_in_batch
        if batch_conn is not None and pending_in_batch > 0:
            batch_conn.commit()
            pending_in_batch = 0

    for note_url, video_url in unique:
        if added >= remaining:
            break
        if video_url in existing_url_set:
            skipped += 1
            continue

        fpath: Path | None = None
        try:
            # 流式下载视频（边下边写，避免整体驻留内存）
            with httpx_module.stream("GET", video_url, headers=req_headers,
                                     timeout=60, follow_redirects=True) as resp:
                if resp.status_code != 200:
                    skipped += 1
                    continue
                fname = f"{str(uuid.uuid4()).replace('-', '')[:16]}.mp4"
                fpath = videos_dir / fname
                total = 0
                with open(fpath, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                        total += len(chunk)
                        if total > max_video_bytes:
                            raise RuntimeError(
                                f"视频超过大小上限（{max_video_bytes // (1024*1024)}MB），跳过"
                            )
                        f.write(chunk)
                if total == 0:
                    fpath.unlink(missing_ok=True)
                    fpath = None
                    skipped += 1
                    continue

            # ffmpeg 提取首帧缩略图
            thumb_rel = _extract_video_thumbnail_sync(fpath, today)

            if batch_conn is None:
                raise RuntimeError("数据库连接不可用")
            insp_id = str(uuid.uuid4())
            rel_path = f"videos/{today}/{fname}"
            now_str = utcnow().strftime("%Y-%m-%d %H:%M:%S")
            # 按博主采集：视频同步写入笔记正文 caption 并关联博主
            meta = meta_map.get(note_url) if meta_map else None
            caption_val = (meta or {}).get("caption")
            blogger_id = (meta or {}).get("blogger_id")
            _save_hashtags(batch_conn, meta, note_url)  # 话题存档（幂等，每笔记一次）
            batch_conn.execute(
                "INSERT INTO inspirations (id, source_type, source_url, file_path, "
                "thumbnail_path, media_type, dominant_colors, is_favorite, "
                "quality_status, content_hash, caption, scraper_task_id, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, 0, 'pending', NULL, ?, ?, ?, ?)",
                (insp_id, "scraper", note_url or video_url, rel_path, thumb_rel, "video",
                 caption_val, task_id, now_str, now_str),
            )
            if blogger_id:
                batch_conn.execute(
                    "INSERT OR IGNORE INTO inspiration_bloggers "
                    "(inspiration_id, blogger_id, confidence) VALUES (?, ?, 1.0)",
                    (insp_id, blogger_id),
                )
            batch_conn.execute(
                "INSERT OR IGNORE INTO scraper_seen_urls (source_url) VALUES (?)",
                (video_url,),
            )
            pending_in_batch += 1
            if pending_in_batch >= _BATCH_COMMIT:
                _commit_videos()

            added += 1
            existing_url_set.add(video_url)
            # 视频较大，下载间隔稍长
            time.sleep(random.uniform(1.0, 2.0))
        except Exception as e:
            try:
                if batch_conn is not None:
                    batch_conn.rollback()
                pending_in_batch = 0
            except Exception:
                pass
            if fpath is not None:
                try:
                    fpath.unlink(missing_ok=True)
                except Exception:
                    pass
            print(f"    视频下载失败 {video_url[:40]}... ({str(e)[:60]})")
            skipped += 1

    _commit_videos()
    if batch_conn is not None:
        batch_conn.close()

    return added, skipped


def _run_blogger_mode(
    page,
    task_id: int,
    blogger_id: int,
    config: dict,
    img_dir: Path,
    videos_dir: Path,
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
        raise RuntimeError("按博主采集缺少 profile_url / platform_user_id")

    max_notes = int(config.get("max_notes", 50))
    max_scrolls = int(config.get("max_scrolls", 15))
    detail_delay = float(config.get("detail_delay", 3.0))  # 详情页间隔（秒）

    print(f"按博主采集：blogger_id={blogger_id}，目标 {max_notes} 篇笔记")
    note_urls = _collect_blogger_note_urls(page, profile_url, max_notes, max_scrolls)
    print(f"  收集到 {len(note_urls)} 篇笔记")

    items_found = 0
    items_added = 0
    notes_log: list[dict] = []
    # 笔记 URL → {caption, blogger_id}：图片/视频入库共用
    meta_map: dict[str, dict] = {}

    for i, note_url in enumerate(note_urls, 1):
        try:
            detail = _extract_note_detail(page, note_url)
        except Exception as e:
            print(f"  [{i}/{len(note_urls)}] 详情页提取失败: {str(e)[:80]}")
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
            added, sk_ex, sk_h, sk_n, sk_dup = _download_batch(
                img_pairs, task_id, existing_url_set,
                remaining, img_dir, today,
                httpx_module, browser_cookies,
                content_hash_set, meta_map,
            )
            print(f"    图片 +{added}（跳过 已存在{sk_ex} MD5{sk_dup} HTTP{sk_h} 网络{sk_n}）")
        # 视频下载入库（mp4 + ffmpeg 缩略图 + caption + 博主关联）
        v_added = 0
        if video_pairs:
            v_added, v_skipped = _download_videos(
                video_pairs, task_id,
                existing_url_set, max(20, len(video_pairs) * 2),
                videos_dir, today,
                httpx_module, browser_cookies, meta_map,
            )
            print(f"    视频 +{v_added}（跳过 {v_skipped}）")
        items_added += added + v_added

        note_id = note_url.rstrip("/").split("/")[-1][:12]
        print(
            f"  [{i}/{len(note_urls)}] 笔记 {note_id}：图 {len(img_pairs)} 视频 {len(video_pairs)} "
            f"正文 {len(caption)} 字（入库 {added + v_added}）"
        )
        notes_log.append({
            "note": note_url,
            "img_count": len(img_pairs),
            "video_count": len(video_pairs),
            "caption_len": len(caption),
            "added": added + v_added,
        })

        # 详情页访问间隔（防风控，默认 2~4s 随机）
        time.sleep(random.uniform(max(1.0, detail_delay - 1.0), detail_delay + 1.0))

    if _HASHTAG_SAVED_COUNT[0] > 0:
        print(f"本次采集命中话题 {_HASHTAG_SAVED_COUNT[0]} 个（已存档 scraper_hashtags，可用于定时采集关键词）")
    return items_found, items_added, notes_log


def _update_task_sync(task_id: int, fields: dict) -> None:
    """用同步 sqlite3 更新采集任务字段，规避与 Playwright 同步 API 的事件循环冲突。

    小红书采集阶段，Playwright 的 sync API 在后台 greenlet 中运行自己的事件循环，
    此时在主线程调用 ``loop.run_until_complete`` 会抛
    "Cannot run the event loop while another loop is running"。
    因此任务进度（断点 / 状态 / 错误 / 完成标记）统一走同步 sqlite3，
    与 :func:`_download_batch` 的写库思路保持一致。

    Args:
        task_id: 采集任务主键。
        fields: 需要更新的列名到新值的映射（列名仅由调用方常量传入，无注入风险）。
    """
    import sqlite3 as _sqlite3

    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    values = [*fields.values(), task_id]
    db_path = settings.storage_root.parent / "fashion_inspo.db"
    conn = _sqlite3.connect(str(db_path))
    try:
        conn.execute(f"UPDATE scraper_tasks SET {sets} WHERE id = ?", values)
        conn.commit()
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  抖音采集：URL 解析 / 详情提取 / 卡片收集 / 双模式执行管线
# ═══════════════════════════════════════════════════════════════
#
# 说明：
# - 抖音网页版 DOM 与内嵌数据漂移频繁，所有选择器集中在下方常量区，
#   页面结构变化时优先修改常量而非散落各处的硬编码。
# - 内容分三层提取并互相兜底：RENDER_DATA 内嵌 JSON（最完整）→
#   网络响应捕获（视频 CDN 直链）→ DOM 解析（图集容器 / 全页过滤）。
# - 视频播放地址时效性强且签名随时失效，采取「抓到详情立即下载」，
#   不跨批次缓存 URL。

# 抖音内容页链接锚点（搜索结果卡与博主页作品网格共用）
_DOUYIN_DETAIL_ANCHOR = "a[href*='/video/'], a[href*='/note/']"
# 图集容器（最多图选择器，命中即不必全页过滤）
_DOUYIN_SLIDE_IMG_SELECTOR = 'div[data-e2e="slide-list"] img, div[class*="slide"] img'
# 详情页主体就绪信号（标题或描述任一出现即认为渲染完成）
_DOUYIN_DETAIL_READY = "[data-e2e='video-desc'], [data-e2e='slide-list'], h1, video"
# 正文描述候选（按优先级取第一个非空）
_DOUYIN_DESC_SELECTORS = (
    "[data-e2e='video-desc']",
    ".video-info-detail .title",
    "h1[data-e2e='video-title']",
    "span[class*='desc']",
)
# 话题标签锚点（点击进入话题聚合页的 <a>）
_DOUYIN_HASHTAG_ANCHOR = "a[href*='/hashtag/']"

# 视频 CDN 特征：网络响应 URL 命中任一即视为真实视频直链
_DOUYIN_VIDEO_URL_HINTS = ("douyinvod.com", "/aweme/v1/play/")

# 抖音媒体 URL 归一化时补充的主机前缀
_DOUYIN_MEDIA_HOST = "https://www.douyin.com"


def _parse_douyin_aweme_id(url: str) -> str | None:
    """从抖音内容页 URL 提取作品 ID（纯函数，供单测）。

    支持 https://www.douyin.com/video/{id}、/note/{id} 以及带查询串的变体；
    分享短链（v.douyin.com/xxx）无法在客户端解析出 ID，返回 None 由调用方跳过。
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
    """
    clean = _clean_media_url(href)
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
    return f"{_DOUYIN_MEDIA_HOST}{kind_marker}/{aid}"


def _normalize_douyin_media_url(src: str) -> str | None:
    """归一化抖音媒体 URL：补协议头 / 补主机前缀；blob 等不可下载地址返回 None。"""
    s = _clean_media_url(src)
    if not s or s.startswith("blob:") or s.startswith("javascript"):
        return None
    if s.startswith("/"):
        return _DOUYIN_MEDIA_HOST + s
    if not s.startswith("http"):
        return None
    return s


def _find_douyin_aweme_data(node, depth: int = 0) -> dict | None:
    """在任意嵌套 JSON 中递归寻找抖音作品数据对象（depth 防御病态深嵌套）。"""
    if depth > 12:
        return None
    if isinstance(node, dict):
        candidate = node.get("videoData") or node.get("aweme_detail") or node.get("awemeDetail")
        if isinstance(candidate, dict) and ("desc" in candidate):
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
            if normalized and _is_content_image(normalized):
                out["img_urls"].append(normalized)
                break

    # 视频真实播放地址（play_addr 的 url_list 可能需要登录 Cookie 才可访问，
    # 与「抓到立即下载 + 浏览器 Cookie 鉴权」策略配套）
    for u in ((vid.get("play_addr") or {}).get("url_list")) or []:
        normalized = _normalize_douyin_media_url(u)
        if normalized:
            out["video_urls"].append(normalized)

    # 图集多图（仅图文笔记有 images 数组）
    for im in (aweme.get("images") or [])[:20]:
        for u in ((im or {}).get("url_list")) or []:
            normalized = _normalize_douyin_media_url(u)
            if normalized and _is_content_image(normalized):
                out["img_urls"].append(normalized)
                break  # 每张图取第一个可用 CDN 地址即可
    return out


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
            if any(h in u for h in _DOUYIN_VIDEO_URL_HINTS) and u not in captured_media:
                captured_media.append(u)
        except Exception:
            pass

    page.on("response", _on_response)
    try:
        page.goto(note_url, wait_until="domcontentloaded", timeout=30000)
    except Exception:
        return result
    finally:
        try:
            page.remove_listener("response", _on_response)
        except Exception:
            pass

    # 等待详情主体渲染（超时不阻断，靠后续随机停顿再等等懒加载）
    try:
        page.wait_for_selector(_DOUYIN_DETAIL_READY, timeout=12000)
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

    # ── 图片：图集容器优先，回退全页过滤 ──
    dom_imgs: list[str] = []
    img_elements = page.query_selector_all(_DOUYIN_SLIDE_IMG_SELECTOR)
    if not img_elements:
        img_elements = page.query_selector_all("img")
    for img in img_elements:
        src = _clean_media_url(img.get_attribute("src") or img.get_attribute("data-src") or "")
        if src and _is_content_image(src) and src not in dom_imgs:
            dom_imgs.append(src)

    # 合并渲染层与 DOM 层候选并统一去重（渲染层在前保证顺序稳定）
    seen_imgs: set[str] = set()
    merged_imgs: list[str] = []
    for src in [*(rendered.get("img_urls") or []), *dom_imgs]:
        normalized = _normalize_douyin_media_url(src)
        if normalized and _is_content_image(normalized) and normalized not in seen_imgs:
            seen_imgs.add(normalized)
            merged_imgs.append(normalized)
    result["img_urls"] = merged_imgs

    # ── 视频：网络捕获 → RENDER_DATA → DOM <video>（跳过 blob）──
    seen_vids: set[str] = set()
    merged_vids: list[str] = []
    for src in ([captured_media[0]] if captured_media else []) + \
               (rendered.get("video_urls") or []):
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
        for sel in _DOUYIN_DESC_SELECTORS:
            el = page.query_selector(sel)
            if el:
                text = (el.inner_text() or "").strip()
                if text:
                    result["caption"] = text[:2000]
                    break

    # ── 话题标签：正文正则 + 话题锚点文本双路 ──
    import re as _re2
    tags: list[str] = []
    if result["caption"]:
        tags.extend(_re2.findall(r"#([^\s#，,。！？.!?]{1,30})", result["caption"]))
    for anchor in page.query_selector_all(_DOUYIN_HASHTAG_ANCHOR)[:20]:
        t = (anchor.inner_text() or "").strip().lstrip("#").strip()
        if t:
            tags.append(t)
    result["tags"] = list(dict.fromkeys(t.strip() for t in tags if t.strip()))
    return result


def _collect_douyin_detail_urls(page, base_url: str, max_items: int,
                                max_scrolls: int = 10) -> tuple[list[str], dict]:
    """滚动收集当前页面中的作品详情链接（搜索页与博主页共用）。

    Returns:
        (detail_urls, funnel)：规范化后的详情页 URL 列表与滚动统计。
    """
    detail_urls: list[str] = []
    cards_seen = 0
    last_count = 0
    no_new = 0
    try:
        page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
    except Exception:
        return detail_urls, {"cards_seen": 0, "urls_extracted": 0, "error": "导航失败"}
    try:
        page.wait_for_selector(_DOUYIN_DETAIL_ANCHOR, timeout=15000)
    except Exception:
        pass
    _rdsleep(1.5, 3.0)
    if random.random() < 0.7:
        _human_mouse_move(page)

    for _ in range(max_scrolls):
        cards_seen += len(page.query_selector_all(_DOUYIN_DETAIL_ANCHOR))
        for link in page.query_selector_all(_DOUYIN_DETAIL_ANCHOR):
            canonical = _canonical_douyin_url(link.get_attribute("href") or "")
            if canonical and canonical not in detail_urls:
                detail_urls.append(canonical)
                if len(detail_urls) >= max_items:
                    return detail_urls, {"cards_seen": cards_seen, "urls_extracted": len(detail_urls)}
        if len(detail_urls) == last_count:
            no_new += 1
        else:
            no_new = 0
            last_count = len(detail_urls)
        if no_new >= 2:
            break
        # 拟人化滚动：偶发鼠标移动 + 分步滚到底
        if random.random() < 0.5:
            _human_mouse_move(page)
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass
        _rdsleep(1.0, 2.0)
    return detail_urls, {"cards_seen": cards_seen, "urls_extracted": len(detail_urls)}


def _run_douyin_notes_pipeline(
    page,
    task_id: int,
    note_urls: list[str],
    budget: int | None,
    img_dir: Path,
    videos_dir: Path,
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
        budget: 本次管线的入库预算（够了提前停止打开后续详情页）；
                None 表示不设素材级预算（博主模式以作品数上限为准）。
        blogger_id: 按博主模式传入，图片/视频同步建立博主关联。
        download_video: False 时跳过视频下载（省磁盘开关）。
        source_kind: 话题存档来源标记（search | blogger）。

    Returns:
        (items_found, items_added, notes_log)
    """
    import sqlite3 as _sqlite3

    items_found = 0
    items_added = 0
    notes_log: list[dict] = []

    conn = None
    try:
        conn = _sqlite3.connect(str(settings.storage_root.parent / "fashion_inspo.db"))
        _ensure_hashtag_table(conn)
    except Exception:
        conn = None

    for i, note_url in enumerate(note_urls, 1):
        if budget is not None and items_added >= budget:
            print(f"  已入库 {items_added} 个 → 达到本轮预算，停止打开详情页")
            break
        detail_delay = random.uniform(2.0, 4.0)
        try:
            detail = _extract_douyin_detail(page, note_url)
        except Exception as e:
            err = str(e) or type(e).__name__
            print(f"  [{i}/{len(note_urls)}] 详情提取异常: {err[:80]}")
            notes_log.append({"note": note_url, "error": err[:200]})
            time.sleep(detail_delay)
            continue

        if not detail["img_urls"] and not detail["video_urls"]:
            print(f"  [{i}/{len(note_urls)}] 详情页无内容（可能触发验证码或已删除）")
            notes_log.append({"note": note_url, "error": "详情页无内容"})
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
            remaining = (budget - items_added) if budget is not None else 200
            remaining = max(remaining, 1)
            added, sk_ex, sk_h, sk_n, sk_dup = _download_batch(
                img_pairs, task_id, existing_url_set, remaining,
                img_dir, today, httpx_module, browser_cookies,
                content_hash_set, meta_map, platform="douyin",
            )

        v_added = 0
        if video_pairs and download_video:
            v_budget = (budget - items_added) if budget is not None else 50
            v_added, v_skipped = _download_videos(
                video_pairs, task_id, existing_url_set,
                max(v_budget, 1),
                videos_dir, today, httpx_module, browser_cookies,
                meta_map, platform="douyin",
            )
            added += v_added
        elif video_pairs:
            notes_log.append({
                "note": note_url, "videos_skipped": len(video_pairs),
                "reason": "视频下载开关关闭",
            })

        items_added += added
        tag_saved = None
        if conn is not None:
            try:
                tag_saved = _save_hashtags(conn, meta_map[note_url], note_url)
                conn.commit()
            except Exception:
                pass

        notes_log.append({
            "note": note_url,
            "imgs": len(img_pairs), "videos": len(video_pairs),
            "added": added,
            "has_tags": bool(meta["tags"]),
            "hashtags_saved": tag_saved,
        })
        print(f"  [{i}/{len(note_urls)}] 图 {len(img_pairs)} / 视频 {len(video_pairs)}"
              f" → 入库 {added}")
        time.sleep(random.uniform(2.0, 4.0))  # 详情页间隔风控节奏

    return items_found, items_added, notes_log


def _resolve_douyin_profile_url(config: dict) -> str:
    """从任务配置解析抖音博主主页 URL（显式 profile_url 优先）。"""
    profile_url = config.get("profile_url")
    if profile_url:
        return profile_url
    puid = config.get("platform_user_id")
    if puid:
        return f"{_DOUYIN_MEDIA_HOST}/user/{puid}"
    raise RuntimeError("按博主采集缺少 profile_url / platform_user_id")




def run_scraper_sync(task_id: int):
    from playwright.sync_api import sync_playwright
    import asyncio

    # 单事件循环贯穿整个脚本：SQLAlchemy 连接池中的连接绑定创建它们的 loop，
    # 若反复 asyncio.run() 新建/关闭 loop，跨 loop 复用连接会间歇性报
    # "attached to a different loop"。子进程生命周期内只建一个 loop，
    # 进程退出时由操作系统回收。
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # ── 确保表结构与字段最新（独立脚本不经过服务端 lifespan）──
    loop.run_until_complete(init_db())
    loop.run_until_complete(ensure_schema())

    # ── 加载任务 ──
    async def _load():
        async with async_session() as db:
            return await db.get(ScraperTask, task_id)
    task = loop.run_until_complete(_load())
    if not task or task.status in ("completed", "cancelled"):
        print(f"任务 {task_id} 已完结，跳过")
        return

    # ── 设为运行中 ──
    async def _run():
        async with async_session() as db:
            t = await db.get(ScraperTask, task_id)
            if t:
                t.status = "running"
                t.started_at = utcnow()
                await db.commit()
    loop.run_until_complete(_run())

    # ── 标记任务失败（复用：配置异常与采集异常都会调用）──
    def _fail(reason: str):
        """标记任务失败（同步写库，规避与 Playwright 同步 API 的事件循环冲突）。"""
        _update_task_sync(task_id, {
            "status": "failed",
            "error": reason[:500],
            "finished_at": utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        })

    # ── 解析配置（异常时标记失败，避免任务卡在 running）──
    try:
        config = json.loads(task.config) if isinstance(task.config, str) else task.config or {}
        keywords = [k.strip() for k in config.get("keywords", []) if k.strip()]
        max_count = config.get("max_count", 50)
        platform = task.platform
        # 采集模式：search 关键词搜索（默认）| user 按博主采集（小红书）
        mode = config.get("collect_mode") or config.get("mode") or "search"
        is_blogger = mode == "user"
        blogger_id: int | None = None
        if is_blogger:
            raw_bid = config.get("blogger_id")
            if not raw_bid:
                print("按博主采集缺少 blogger_id，退出")
                _fail("按博主采集缺少 blogger_id")
                return
            blogger_id = int(raw_bid)

        if not keywords and not is_blogger:
            print("无关键词，退出")
            _fail("无关键词")
            return

        # 准备下载目录（图片与视频目录统一创建：抖音搜索模式同样会下载视频）
        today = utcnow().strftime("%Y-%m")
        img_dir = settings.images_dir / today
        img_dir.mkdir(parents=True, exist_ok=True)
        videos_dir = settings.videos_dir / today
        videos_dir.mkdir(parents=True, exist_ok=True)
        import httpx
    except Exception as e:
        err = str(e) or type(e).__name__
        print(f"配置解析失败: {err}")
        _fail(f"配置解析失败: {err}")
        return

    # ── 断点续采：构建或恢复执行计划（关键词 × 排序） ──
    # 首次运行：随机打乱关键词一次后展开 ×3 排序，计划随 resume_token 持久化，保证跨重启顺序确定。
    # 按博主采集不走关键词计划（一轮完成，见下方独立执行分支）。
    resume = None
    if task.resume_token:
        try:
            resume = json.loads(task.resume_token)
        except Exception:
            resume = None

    if is_blogger:
        plan: list = []
        done = 0
        items_found = 0
        items_added = 0
    elif resume and isinstance(resume.get("plan"), list) and resume["plan"]:
        plan = resume["plan"]
        done = int(resume.get("done", 0))
        items_found = int(resume.get("items_found", 0))
        items_added = int(resume.get("items_added", 0))
        print(f"断点续采：从第 {done}/{len(plan)} 个组合继续（已入库 {items_added}）")
    else:
        shuffled = list(keywords)
        random.shuffle(shuffled)
        # 排序方式映射：用户选择的排序 → 执行计划中的排序类型（默认仅综合）
        # 抖音网页版不支持排序切换，固定综合排序单组合
        if platform == "douyin":
            plan = [{"k": kw, "s": "general"} for kw in shuffled]
        else:
            sort_mode = config.get("sort_mode") or "general"
            sorts = {
                "latest": ["time_descending"],
                "popular": ["popularity_descending"],
                "general": ["general"],
            }.get(sort_mode, SORT_TYPES)
            plan = [{"k": kw, "s": s} for kw in shuffled for s in sorts]
        done = 0
        items_found = 0
        items_added = 0

    existing_url_set: set[str] = set()  # 跨批次 URL 去重
    content_hash_set: set[str] = set()  # 跨批次内容 MD5 去重
    total_skipped_existing = 0
    total_skipped_content_dup = 0
    total_skipped_non200 = 0
    total_skipped_network = 0
    per_search: list[dict] = []  # 每次搜索的漏斗明细

    def _save_resume(done_idx: int):
        """持久化断点进度（计划 / 已完成数 / 累计计数）— 同步写库，避免事件循环冲突。"""
        token = json.dumps({
            "plan": plan,
            "done": done_idx,
            "items_found": items_found,
            "items_added": items_added,
        }, ensure_ascii=False)
        _update_task_sync(task_id, {"resume_token": token})

    # 平台执行器：小红书走 CDP 真实 Chrome；抖音走独立 Playwright 浏览器（网页版无需 CDP）
    pw = None
    dy = None
    page = None

    def _search_douyin(keyword: str, need_count: int) -> tuple[list[tuple[str, str]], dict]:
        """使用 DouyinScraper 在独立浏览器中搜索抖音网页版并提取图片 URL。

        Returns:
            (pairs, funnel_dict): (笔记页面 URL, 图片 CDN URL) 列表与该次搜索的漏斗统计
        """
        raw = loop.run_until_complete(dy.search(keyword, max(10, need_count * 2)))
        pairs: list[tuple[str, str]] = []
        seen: set[str] = set()
        for item in raw:
            for img in item.image_urls or []:
                if img and img not in seen:
                    seen.add(img)
                    pairs.append((item.url or "", img))
        funnel: dict = {
            "cards_total": len(raw),
            "urls_extracted": len(pairs),
            "target": need_count,
        }
        if not raw:
            funnel["error"] = "抖音搜索无结果（网页版可能未登录或页面结构变化）"
        return pairs[: need_count * 2], funnel

    try:
        # ── CDP 通道判定 ──
        # 小红书固定走 CDP 真实 Chrome；抖音显式提供 cdp_port 时同样走 CDP
        # （完整采集：图集/视频/正文），未提供则回退独立 Playwright 浏览器旧路径。
        use_cdp = (
            platform == "xiaohongshu"
            or (platform == "douyin" and bool(config.get("cdp_port")))
        )
        if use_cdp:
            pw = sync_playwright().start()

            # ── 连接 CDP Chrome ──
            CDP_PORT = config.get("cdp_port") or 9222
            cdp_url = f"http://localhost:{CDP_PORT}"
            print(f"连接 CDP Chrome: {cdp_url}")

            try:
                browser = pw.chromium.connect_over_cdp(cdp_url)
            except Exception as e:
                chrome_exe = settings.chrome_executable
                data_dir = settings.chrome_user_data_dir
                raise RuntimeError(
                    f"无法连接 CDP Chrome (端口 {CDP_PORT})。\n"
                    f"请先用调试模式启动 Chrome:\n"
                    f'"{chrome_exe}" '
                    f"--remote-debugging-port={CDP_PORT} "
                    f'--user-data-dir="{data_dir}"'
                ) from e

            print(f"已连接 Chrome {browser.version}")
            context = browser.contexts[0]

            # 创建新标签页用于采集
            page = context.new_page()

            # ── 登录检查（小红书/抖音共用逻辑，差异在首页 URL 与会话 Cookie 名）──
            _ensure_platform_login(context, page, platform, timeout=180)

            # 提取浏览器 Cookie 用于 httpx 下载鉴权
            browser_cookies = {c["name"]: c for c in context.cookies()}
        else:
            from app.scrapers.douyin import DouyinScraper

            dy = DouyinScraper(headless=config.get("headless", True))
            browser_cookies: dict = {}
            print("抖音平台：未配置 CDP 端口，使用独立 Playwright 浏览器降级采集（仅封面图，建议开启 CDP 走完整通道）")

        # ── 按博主采集：主页作品 → 详情页全量提取（多图/视频/正文/博主标记）──
        if is_blogger:
            if platform == "douyin":
                if page is None:
                    # 未走 CDP 时无法进博主主页逐篇采集，提前失败并给明确指引
                    _fail("抖音按博主采集需要 CDP Chrome：请在创建任务时开启 CDP 并登录抖音")
                    return
                profile_url = _resolve_douyin_profile_url(config)
                max_notes_cfg = int(config.get("max_notes", 50) or 50)
                note_urls, scroll_funnel = _collect_douyin_detail_urls(
                    page, profile_url, max_notes_cfg,
                    max_scrolls=int(config.get("max_scrolls", 15) or 15),
                )
                print(f"抖音按博主采集：收集到 {len(note_urls)} 个作品")
                per_search.append({"keyword": profile_url, **scroll_funnel})
                items_found, items_added, blogger_notes = _run_douyin_notes_pipeline(
                    page, task_id, note_urls,
                    budget=None,  # 博主模式以作品数为上限，不设素材级预算
                    img_dir=img_dir, videos_dir=videos_dir, today=today,
                    httpx_module=httpx, browser_cookies=browser_cookies,
                    existing_url_set=existing_url_set,
                    content_hash_set=content_hash_set,
                    blogger_id=blogger_id,
                    download_video=bool(config.get("download_video", True)),
                    source_kind="blogger",
                )
            else:
                # 小红书按博主：主页笔记 → 详情页全量提取
                items_found, items_added, blogger_notes = _run_blogger_mode(
                    page, task_id, blogger_id, config,
                    img_dir, videos_dir, today, httpx,
                    browser_cookies, existing_url_set, content_hash_set,
                )
            for n in blogger_notes:
                per_search.append(n)
            print(f"按博主采集完成：作品 {len(blogger_notes)} 条，提取 {items_found}，入库 {items_added}")
        else:
            # ── 搜索 + 即时下载：按执行计划（关键词 × 排序）逐项推进，支持断点续采 ──
            total_searches = len(plan)

        for plan_idx in range(done, len(plan)):
            entry = plan[plan_idx]
            kw = entry["k"]
            sort_type = entry["s"]
            search_count = plan_idx + 1

            if items_added >= max_count:
                print(f"\n  已入库 {items_added} 张 → 达到目标 {max_count}，停止搜索")
                done = len(plan)
                _save_resume(done)
                break

            print(f"\n{'='*50}")
            print(f"[搜索 {search_count}/{total_searches}] {kw} [{sort_type}]")
            print(f"{'='*50}")

            try:
                # 按剩余需求采集：够用即停，避免滚动浏览远超所需的内容
                remaining = max_count - items_added
                if platform == "xiaohongshu":
                    urls, inner_funnel = _search_xiaohongshu(page, kw, remaining, sort_type)
                elif platform == "douyin" and page is not None:
                    # 抖音 CDP 完整通道：搜索卡片收集 → 逐详情页提取
                    # （图集多图/视频/正文 caption/#话题#），与小红书同管线质量
                    note_urls, card_funnel = _collect_douyin_detail_urls(
                        page,
                        f"https://www.douyin.com/search/{quote(kw)}?type=general",
                        max_items=max(10, int(remaining * 2)),
                    )
                    n_found, n_added, note_logs = _run_douyin_notes_pipeline(
                        page, task_id, note_urls, budget=remaining,
                        img_dir=img_dir, videos_dir=videos_dir, today=today,
                        httpx_module=httpx, browser_cookies=browser_cookies,
                        existing_url_set=existing_url_set,
                        content_hash_set=content_hash_set,
                        blogger_id=None,
                        download_video=bool(config.get("download_video", True)),
                        source_kind="search",
                    )
                    items_found += n_found
                    items_added += n_added
                    # 记录本次搜索的完整漏斗（明细截断最近 20 条防 diagnostics 膨胀）
                    per_search.append({
                        "keyword": kw,
                        "sort_type": sort_type,
                        **card_funnel,
                        "notes_opened": len(note_urls),
                        "batch_added": n_added,
                        "details": note_logs[-20:],
                    })
                    print(f"  打开 {len(note_urls)} 个详情 → 入库 {n_added}")
                    print(f"  累计入库: {items_added}/{max_count}")

                    done = plan_idx + 1
                    _save_resume(done)

                    # 搜索间冷却（CDP 保活）
                    if items_added < max_count and plan_idx < len(plan) - 1:
                        cool = random.randint(6, 12)
                        print(f"  ⏸ 冷却 {cool}s...")
                        for _ in range(cool):
                            try:
                                if random.random() < 0.5:
                                    _human_mouse_move(page)
                                page.evaluate("1")
                            except Exception:
                                pass
                            _rdsleep(0.8, 1.5)
                    continue
                elif platform == "douyin":
                    urls, inner_funnel = _search_douyin(kw, remaining)
                else:
                    per_search.append({
                        "keyword": kw, "sort_type": sort_type,
                        "error": f"不支持的平台: {platform}",
                    })
                    done = plan_idx + 1
                    _save_resume(done)
                    continue

                items_found += len(urls)
                print(f"  提取 {len(urls)} 个 URL")

                # 抖音降级通道：每次搜索后同步其浏览器 Cookie（用于 CDN 下载鉴权）
                if platform == "douyin" and dy is not None:
                    browser_cookies = dy.cookies()

                # 立即下载本批（带浏览器 Cookie；请求头按平台取 Referer）
                added, sk_ex, sk_h, sk_n, sk_dup = _download_batch(
                    urls, task_id, existing_url_set, remaining,
                    img_dir, today, httpx, browser_cookies,
                    content_hash_set,
                    platform=platform,
                )
                items_added += added
                total_skipped_existing += sk_ex
                total_skipped_content_dup += sk_dup
                total_skipped_non200 += sk_h
                total_skipped_network += sk_n

                # 记录本次搜索的完整漏斗
                per_search.append({
                    "keyword": kw,
                    "sort_type": sort_type,
                    **inner_funnel,
                    "batch_added": added,
                    "batch_skipped_existing": sk_ex,
                    "batch_skipped_content_dup": sk_dup,
                    "batch_skipped_http": sk_h,
                    "batch_skipped_network": sk_n,
                })

                print(f"  本批入库: {added} (跳过: 已存在{sk_ex}, MD5重复{sk_dup}, HTTP{sk_h}, 网络{sk_n})")
                print(f"  累计入库: {items_added}/{max_count}")

                # 搜索间冷却 + CDP 保活（仅小红书有 CDP 页面）
                if items_added < max_count and plan_idx < len(plan) - 1:
                    cool = random.randint(6, 12)
                    print(f"  ⏸ 冷却 {cool}s...")
                    # 轻量页面交互保持 CDP 连接活跃（随机间隔 + 偶发鼠标移动）
                    for _ in range(cool):
                        if page is not None:
                            try:
                                if random.random() < 0.5:
                                    _human_mouse_move(page)
                                page.evaluate("1")  # no-op，单纯保持连接
                            except Exception:
                                pass
                        _rdsleep(0.8, 1.5)

            except Exception as e:
                err = str(e) or type(e).__name__
                per_search.append({
                    "keyword": kw, "sort_type": sort_type,
                    "error": err,
                })
                print(f"  [ERR] {err}")

            # 每完成一个组合即持久化进度（成功或失败都推进，避免重复执行同一组合）
            done = plan_idx + 1
            _save_resume(done)

    except Exception as e:
        import traceback
        err = str(e) or type(e).__name__
        print(f"采集失败: {err}")
        traceback.print_exc()
        _fail(err)
        return

    finally:
        # CDP 模式不关 Chrome；Playwright 客户端与抖音独立浏览器正常回收
        try:
            if pw:
                pw.stop()
        except Exception:
            pass
        if dy is not None:
            try:
                loop.run_until_complete(dy.close())
            except Exception:
                pass

    # ── 组装持久化漏斗数据 ──
    funnel_diagnostics = json.dumps({
        "per_search": per_search,
        "summary": {
            "total_found": items_found,
            "skipped_url_seen": total_skipped_existing,
            "skipped_content_dup": total_skipped_content_dup,
            "skipped_http_error": total_skipped_non200,
            "skipped_network_error": total_skipped_network,
            "total_added": items_added,
        },
    }, ensure_ascii=False)

    # ── 汇总漏斗日志 ──
    print(f"\n  ╔══════════════════════════════════════════")
    print(f"  ║ 采集漏斗汇总")
    print(f"  ╠══════════════════════════════════════════")
    print(f"  ║ 搜索提取总数:   {items_found}")
    print(f"  ║ 跨次已存在:     {total_skipped_existing}")
    print(f"  ║ 内容MD5重复:    {total_skipped_content_dup}")
    print(f"  ║ HTTP 非 200:    {total_skipped_non200}")
    print(f"  ║ 网络失败:       {total_skipped_network}")
    print(f"  ║ ★ 最终入库:     {items_added}")
    print(f"  ╚══════════════════════════════════════════")

    # ── 完成任务 ──
    error_msg = None
    if items_found == 0 and per_search:
        errors = [s.get("error", "") for s in per_search if s.get("error")]
        if errors:
            error_msg = " | ".join(errors)[:500]

    def _done():
        """标记任务完成并写入漏斗诊断（同步写库，规避事件循环冲突）。"""
        fields = {
            "status": "completed",
            "items_found": items_found,
            "items_added": items_added,
            "diagnostics": funnel_diagnostics,
            "resume_token": None,  # 任务完结，清除断点进度
            "finished_at": utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        }
        if error_msg:
            fields["error"] = error_msg
        _update_task_sync(task_id, fields)

    _done()

    print(f"\n任务 {task_id} 完成: found={items_found}, added={items_added}")
    if error_msg:
        print(f"诊断: {error_msg}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/run_scraper.py <task_id>")
        sys.exit(1)
    run_scraper_sync(int(sys.argv[1]))
