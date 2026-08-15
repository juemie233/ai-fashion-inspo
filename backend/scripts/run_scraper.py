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

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings
from app.database import async_session, init_db
from app.db_migrations import ensure_schema
from app.models.inspiration import Inspiration
from app.models.scraper import ScraperTask

# ── UTF-8 输出 ──
# 注意：必须开启 line_buffering，否则 stdout 被重新包装成带缓冲的 TextIOWrapper，
# print 进度日志会一直积压在缓冲区，直到进程退出才落盘，导致日志看起来「卡住不动」。
import io
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
) -> tuple[int, int, int, int, int]:
    """下载一批图片，立即入库。使用同步 sqlite3 避免 event loop 冲突。

    urls 中每项为 (笔记页面 URL, 图片 CDN URL)：笔记页面 URL 存入 source_url 作为
    「原始链接」，图片 CDN URL 用于下载与去重。

    去重策略（三层）：
    1. 图片 URL 内存去重 — 同次运行内相同图片不重复下载
    2. DB 墓碑表去重 — 跨次采集相同图片 URL 不重复入库
    3. 内容 MD5 去重 — 同一图片不同 URL（CDN 多节点）不重复入库
    """
    import hashlib
    import sqlite3 as _sqlite3

    # 构建请求头（带浏览器 Cookie 以通过 CDN 鉴权）
    req_headers = {
        "Referer": "https://www.xiaohongshu.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    if cookies:
        req_headers["Cookie"] = "; ".join(
            f"{c['name']}={c['value']}" for c in cookies.values()
        )

    db_path = settings.storage_root.parent / "fashion_inspo.db"

    # 按图片 URL 去重（同一图片可能在不同卡片/搜索中重复出现）
    unique: list[tuple[str, str]] = []
    _seen_url: set[str] = set()
    for note_url, img_url in urls:
        if img_url not in _seen_url:
            _seen_url.add(img_url)
            unique.append((note_url, img_url))

    # 查询这批图片 URL 中已在墓碑表中的（同步查询，包括已删除的素材 URL）
    if unique:
        img_urls = [img_url for _, img_url in unique]
        conn = None
        try:
            conn = _sqlite3.connect(str(db_path))
            # 确保墓碑表存在
            conn.execute(
                "CREATE TABLE IF NOT EXISTS scraper_seen_urls "
                "(source_url TEXT PRIMARY KEY, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
            conn.commit()
            placeholders = ",".join("?" * len(img_urls))
            cur = conn.execute(
                f"SELECT source_url FROM scraper_seen_urls WHERE source_url IN ({placeholders})",
                img_urls,
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
    try:
        batch_conn = _sqlite3.connect(str(db_path))
    except Exception:
        batch_conn = None

    def _commit_batch():
        """提交当前攒批的写入（无写入或连接不可用时跳过）。"""
        nonlocal pending_in_batch
        if batch_conn is not None and pending_in_batch > 0:
            batch_conn.commit()
            pending_in_batch = 0

    for note_url, img_url in unique:
        if added >= remaining:
            break
        if img_url in existing_url_set:
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
                    batch_conn.execute(
                        "INSERT INTO inspirations (id, source_type, source_url, file_path, "
                        "thumbnail_path, media_type, dominant_colors, is_favorite, "
                        "quality_status, content_hash, scraper_task_id, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, NULL, ?, NULL, 0, 'pending', ?, ?, ?, ?)",
                        (insp_id, "scraper", note_url or img_url, rel_path, "image",
                         content_sha256, task_id, now_str, now_str),
                    )
                    batch_conn.execute(
                        "INSERT OR IGNORE INTO scraper_seen_urls (source_url) VALUES (?)",
                        (img_url,),
                    )
                    # 向量回填任务入队（与素材行同事务，保证一致）
                    batch_conn.execute(
                        "INSERT INTO task_queue (type, status, progress, total, done, result, "
                        "max_retries, retry_count, created_at, updated_at) "
                        "VALUES ('vector_backfill', 'pending', 0, 1, 0, ?, 2, 0, ?, ?)",
                        (json.dumps({"inspiration_ids": [insp_id]}), now_str, now_str),
                    )
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


# ═══════════════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════════════

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
    async def _fail(reason: str):
        async with async_session() as db:
            t = await db.get(ScraperTask, task_id)
            if t:
                t.status = "failed"
                t.error = reason[:500]
                t.finished_at = utcnow()
                await db.commit()

    # ── 解析配置（异常时标记失败，避免任务卡在 running）──
    try:
        config = json.loads(task.config) if isinstance(task.config, str) else task.config or {}
        keywords = [k.strip() for k in config.get("keywords", []) if k.strip()]
        max_count = config.get("max_count", 50)
        platform = task.platform

        if not keywords:
            print("无关键词，退出")
            loop.run_until_complete(_fail("无关键词"))
            return

        # 准备下载目录
        today = utcnow().strftime("%Y-%m")
        img_dir = settings.images_dir / today
        img_dir.mkdir(parents=True, exist_ok=True)
        import httpx
    except Exception as e:
        err = str(e) or type(e).__name__
        print(f"配置解析失败: {err}")
        loop.run_until_complete(_fail(f"配置解析失败: {err}"))
        return

    # ── 断点续采：构建或恢复执行计划（关键词 × 排序） ──
    # 首次运行：随机打乱关键词一次后展开 ×3 排序，计划随 resume_token 持久化，保证跨重启顺序确定。
    resume = None
    if task.resume_token:
        try:
            resume = json.loads(task.resume_token)
        except Exception:
            resume = None

    if resume and isinstance(resume.get("plan"), list) and resume["plan"]:
        plan = resume["plan"]
        done = int(resume.get("done", 0))
        items_found = int(resume.get("items_found", 0))
        items_added = int(resume.get("items_added", 0))
        print(f"断点续采：从第 {done}/{len(plan)} 个组合继续（已入库 {items_added}）")
    else:
        shuffled = list(keywords)
        random.shuffle(shuffled)
        plan = [{"k": kw, "s": s} for kw in shuffled for s in SORT_TYPES]
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
        """持久化断点进度（计划 / 已完成数 / 累计计数）。"""
        token = json.dumps({
            "plan": plan,
            "done": done_idx,
            "items_found": items_found,
            "items_added": items_added,
        }, ensure_ascii=False)

        async def _w():
            async with async_session() as db:
                t = await db.get(ScraperTask, task_id)
                if t:
                    t.resume_token = token
                    await db.commit()
        loop.run_until_complete(_w())

    try:
        pw = sync_playwright().start()

        # ── 唯一路径：连接 CDP Chrome ──
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

        # ── 登录检查 ──
        LOGIN_TIMEOUT = 180

        def _check_login() -> bool:
            cookies = context.cookies()
            xhs = [c for c in cookies if "xiaohongshu" in c.get("domain", "")]
            return any(c.get("name") in ("web_session", "a1") for c in xhs)

        if _check_login():
            print("已在登录状态，直接开始采集")
        else:
            print(f"\n{'='*50}")
            print(" >>> 请在 Chrome 中登录小红书 <<<")
            print(" 在地址栏输入 xiaohongshu.com，扫码登录")
            print(f" 登录完成后脚本自动检测并继续（{LOGIN_TIMEOUT}s 超时）")
            print(f"{'='*50}")

            for waited in range(0, LOGIN_TIMEOUT, 5):
                time.sleep(5)
                if _check_login():
                    print(f"检测到登录 ({waited + 5}s)")
                    time.sleep(1)
                    break
                if (waited + 5) % 30 == 0:
                    print(f"  等待登录... ({waited + 5}s / {LOGIN_TIMEOUT}s)")
            else:
                print("登录超时，将尝试当前状态")

        # ── 搜索 + 即时下载：按执行计划（关键词 × 排序）逐项推进，支持断点续采 ──
        total_searches = len(plan)

        # 提取浏览器 Cookie 用于 httpx 下载鉴权
        browser_cookies = {c["name"]: c for c in context.cookies()}

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

                # 立即下载本批（带浏览器 Cookie）
                added, sk_ex, sk_h, sk_n, sk_dup = _download_batch(
                    urls, task_id, existing_url_set, remaining,
                    img_dir, today, httpx, browser_cookies,
                    content_hash_set,
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

                # 搜索间冷却 + CDP 保活
                if items_added < max_count and plan_idx < len(plan) - 1:
                    cool = random.randint(6, 12)
                    print(f"  ⏸ 冷却 {cool}s...")
                    # 轻量页面交互保持 CDP 连接活跃（随机间隔 + 偶发鼠标移动）
                    for _ in range(cool):
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
        loop.run_until_complete(_fail(err))
        return

    finally:
        # CDP 模式不关浏览器
        try:
            if 'pw' in dir() and pw:
                pw.stop()
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

    async def _done():
        async with async_session() as db:
            t = await db.get(ScraperTask, task_id)
            if t:
                t.status = "completed"
                t.items_found = items_found
                t.items_added = items_added
                t.diagnostics = funnel_diagnostics
                if error_msg:
                    t.error = error_msg
                t.resume_token = None  # 任务完结，清除断点进度
                t.finished_at = utcnow()
                await db.commit()
    loop.run_until_complete(_done())

    print(f"\n任务 {task_id} 完成: found={items_found}, added={items_added}")
    if error_msg:
        print(f"诊断: {error_msg}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/run_scraper.py <task_id>")
        sys.exit(1)
    run_scraper_sync(int(sys.argv[1]))
