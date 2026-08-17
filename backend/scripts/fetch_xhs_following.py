"""小红书关注列表采集脚本：通过 CDP 连接本地 Chrome 提取关注的博主信息。

功能：
  复用本机 Chrome 的小红书登录态（CDP 调试端口，默认 9222），自动发现当前用户 ID，
  通过小红书 IM 关注列表 API（edith.xiaohongshu.com/api/im/web/users/following/all）
  一次拉取全部关注用户（昵称 + 用户 ID），再逐个进入用户主页提取小红书号与 IP 属地，
  最终输出 CSV 并打印统计。

用法:
  python scripts/fetch_xhs_following.py [--port 9222] [--max-pages 5]
      [--no-fetch-ids] [--max-profile-visits 50] [--output <csv路径>]
      [--login-timeout 180] [--debug-screenshot]

执行流程:
  1. 探测 CDP 端口；端口不通时自动拉起调试模式 Chrome 并打开小红书登录页等待扫码；
  2. 自动发现用户 ID（顶部头像链接 / __INITIAL_STATE__ / localStorage，不硬编码）；
  3. 页面内调用 IM 关注列表 API（credentials: include 复用登录态）分页拉取全部关注；
  4. 默认逐个进入缺号用户主页，提取小红书号与 IP 属地（每个主页前随机 sleep 1~2 秒）；
  5. 输出 CSV 到 backend/storage/xhs_following.csv（utf-8-sig），打印最终统计。

说明:
  - 纯采集工具脚本，只产出 CSV，不写入主数据库。
  - 列表走已验证的 IM API；详情（小红书号/IP属地）无批量接口，走主页 DOM 提取，
    受 --max-profile-visits 上限保护以防风控。
"""

import argparse
import csv
import json
import logging
import os
import random
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

# 使脚本可以从任意目录启动（与 run_scraper.py 惯例一致）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402

logger = logging.getLogger(__name__)

# IM 关注列表 API（页面内 fetch，credentials: include 复用登录态）
FOLLOWING_API = "https://edith.xiaohongshu.com/api/im/web/users/following/all"
FOLLOWING_PAGE_SIZE = 200  # 接口单页大小（实测可一次返回全部）

# 页面内执行的关注列表拉取脚本：参数为对象 {api, pageNo, size}，返回 JSON 字符串 {success, list:[{user_id,nick_name}]}
_FETCH_FOLLOWING_JS = """
async (args) => {
    const r = await fetch(args.api + '?page=' + args.pageNo + '&size=' + args.size, {
        credentials: 'include',
        headers: {'accept': 'application/json'}
    });
    const j = await r.json();
    if (!j || !j.success) return JSON.stringify({success: false, msg: (j && j.msg) || '接口异常'});
    const list = (j.data && j.data.follow_user_d_t_o_list) || [];
    return JSON.stringify({
        success: true,
        list: list.map(u => ({user_id: u.user_id || '', nick_name: u.nick_name || ''}))
    });
}
"""


# ============================================================
#  纯函数：文本解析 / 去重 / CSV / 列表解析（供单元测试直接覆盖）
# ============================================================


def extract_user_id_from_href(href: str | None) -> str | None:
    """从个人主页链接中提取用户 ID。

    Args:
        href: 形如 /user/profile/{uid} 或带 query 的完整链接。

    Returns:
        用户 ID 字符串；无法提取时返回 None。
    """
    if not href:
        return None
    m = re.search(r"/user/profile/([0-9a-zA-Z]+)", href)
    return m.group(1) if m else None


def parse_ip_location(text: str) -> str:
    """从文本中解析 IP 属地（如「IP属地：浙江」→ 浙江）。

    仅识别带「IP属地：」前缀的写法，避免把昵称等其它文本误判为属地。

    Args:
        text: 卡片或页面文本。

    Returns:
        属地字符串；未识别到返回空字符串。
    """
    if not text:
        return ""
    m = re.search(r"IP属地[:：]\s*([^\s，,；;()（）]+)", text)
    return m.group(1).strip() if m else ""


def parse_xhs_id(text: str) -> str:
    """从文本中解析小红书号（如「小红书号：abc123」→ abc123）。

    Args:
        text: 用户主页文本。

    Returns:
        小红书号字符串；未识别到返回空字符串。
    """
    if not text:
        return ""
    m = re.search(r"小红书号[:：]\s*([0-9A-Za-z]{3,})", text)
    return m.group(1).strip() if m else ""


def parse_following_json(raw: str) -> list[dict]:
    """解析关注列表 API 返回的 JSON，转换为采集行。

    Args:
        raw: 页面内 fetch 返回的 JSON 字符串（{success, list:[{user_id,nick_name}]}）。

    Returns:
        采集行列表 [{nickname, xhs_id, ip_location, uid}]；失败返回空列表。
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("关注列表接口返回非 JSON 数据")
        return []
    if not data.get("success"):
        logger.warning(f"关注列表接口返回失败: {data.get('msg')}")
        return []
    rows: list[dict] = []
    for item in data.get("list") or []:
        uid = (item.get("user_id") or "").strip()
        nickname = (item.get("nick_name") or "").strip()
        if not uid:
            continue
        rows.append({
            "nickname": nickname,
            "xhs_id": "",
            "ip_location": "",
            "uid": uid,
        })
    return rows


def dedupe_rows(rows: list[dict]) -> list[dict]:
    """按昵称去重，保留首次出现的记录。

    Args:
        rows: 采集结果列表（每个元素含 nickname 等字段）。

    Returns:
        去重后的列表；昵称缺失或为空的记录被丢弃。
    """
    seen: set[str] = set()
    out: list[dict] = []
    for r in rows:
        key = (r.get("nickname") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def load_existing_csv(path: Path) -> dict[str, dict]:
    """读取现有 CSV，返回 {昵称: {xhs_id, ip_location}}，用于断点续跑。

    Args:
        path: CSV 路径（不存在时返回空字典）。

    Returns:
        昵称到已有记录（xhs_id / ip_location 字段）的映射。
    """
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            return {
                row["nickname"]: row
                for row in reader
                if row.get("nickname")
            }
    except Exception as e:
        logger.warning(f"读取已有 CSV 失败（忽略续跑）: {e}")
        return {}


def write_csv(rows: list[dict], path: Path) -> None:
    """将采集结果写入 CSV（表头 nickname,xhs_id,ip_location，utf-8-sig 编码）。

    使用 utf-8-sig 编码以便 Excel 直接打开中文不乱码；每次运行覆盖写。

    Args:
        rows: 采集结果列表。
        path: CSV 输出路径。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["nickname", "xhs_id", "ip_location"])
        writer.writeheader()
        for r in rows:
            writer.writerow({
                "nickname": r.get("nickname") or "",
                "xhs_id": r.get("xhs_id") or "",
                "ip_location": r.get("ip_location") or "",
            })


# ============================================================
#  日志 / 输出辅助
# ============================================================


def _setup_stdout_utf8() -> None:
    """将 stdout/stderr 包装为 UTF-8 行缓冲，避免 Windows 控制台中文乱码与日志积压。

    必须开启 line_buffering，否则 print 进度会积压在缓冲区直到进程退出。
    """
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


def setup_logging(log_path: Path) -> None:
    """配置日志：控制台 + 文件双输出（UTF-8，中文信息）。

    Args:
        log_path: 日志文件路径（backend/storage/xhs_following.log）。
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)


def _rdsleep(lo: float = 1.0, hi: float = 2.0) -> None:
    """随机间隔休眠，模拟真人操作节奏，降低风控概率。"""
    time.sleep(random.uniform(lo, hi))


# ============================================================
#  Chrome / CDP
# ============================================================


def _probe_cdp(port: int) -> tuple[bool, str, bool]:
    """探测 CDP 端口（延迟导入复用 scraper_service._check_cdp）。

    Args:
        port: CDP 调试端口。

    Returns:
        (是否可用, 详情信息, 是否为 Google Chrome)。
    """
    from app.services.scraper_service import _check_cdp

    return _check_cdp(port)


def _launch_chrome(port: int) -> None:
    """自动拉起带调试端口的 Chrome（使用采集专用用户数据目录）。

    启动后轮询探测直到 CDP 就绪；超时抛 RuntimeError。

    Args:
        port: CDP 调试端口。
    """
    exe = settings.chrome_executable
    if not exe or not os.path.isfile(exe):
        raise RuntimeError(
            f"未找到 Chrome 可执行文件（配置为空或路径无效）: {exe!r}。"
            f"请检查 backend/.env 的 CHROME_EXECUTABLE 配置。"
        )
    cmd = [
        exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={settings.chrome_user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    logger.info(f"启动调试 Chrome: {exe}（端口 {port}）")
    subprocess.Popen(cmd)

    deadline = time.time() + settings.chrome_startup_timeout
    while time.time() < deadline:
        ok, _detail, is_chrome = _probe_cdp(port)
        if ok and is_chrome:
            logger.info("调试 Chrome 就绪")
            return
        time.sleep(0.5)
    raise RuntimeError(
        f"Chrome 启动超时（{settings.chrome_startup_timeout}s），请检查 chrome_executable 配置"
    )


def connect_cdp(port: int) -> tuple:
    """连接本地 CDP Chrome，返回 (playwright, browser)。

    Args:
        port: CDP 调试端口。

    Returns:
        (sync_playwright 实例, 已连接的 Browser)。
    """
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    except Exception:
        pw.stop()
        raise
    return pw, browser


# ============================================================
#  登录
# ============================================================


def _is_logged_in(context) -> bool:
    """通过 Cookie 判断小红书是否已登录。

    Args:
        context: Playwright BrowserContext。

    Returns:
        已登录返回 True。
    """
    try:
        cookies = context.cookies()
    except Exception:
        return False
    xhs = [c for c in cookies if "xiaohongshu" in (c.get("domain") or "")]
    return any(c.get("name") in ("web_session", "a1") for c in xhs)


def _wait_login(page, context, timeout: int) -> None:
    """打开小红书首页等待用户扫码登录，轮询 Cookie 直到超时。

    Args:
        page: Playwright Page。
        context: Playwright BrowserContext。
        timeout: 等待超时秒数。

    Raises:
        RuntimeError: 超时仍未检测到登录态。
    """
    print("=" * 50)
    print(" >>> 请在 Chrome 中登录小红书 <<<")
    print(" 已自动打开小红书登录页，请扫码登录")
    print(f" 登录完成后脚本自动检测并继续（{timeout}s 超时）")
    print("=" * 50)
    try:
        page.goto(
            "https://www.xiaohongshu.com/explore",
            wait_until="domcontentloaded",
            timeout=30000,
        )
    except Exception as e:
        print(f"自动跳转登录页失败（可手动在地址栏输入 xiaohongshu.com）: {e}")

    for waited in range(0, timeout, 5):
        time.sleep(5)
        if _is_logged_in(context):
            print(f"检测到登录 ({waited + 5}s)")
            time.sleep(1)
            return
        if (waited + 5) % 30 == 0:
            print(f"  等待登录... ({waited + 5}s / {timeout}s)")
    raise RuntimeError(f"登录超时（{timeout}s），未检测到小红书登录态")


# ============================================================
#  页面导航与数据提取
# ============================================================


def goto_with_retry(page, url: str, retries: int = 2) -> None:
    """导航到指定 URL，网络异常时指数退避重试。

    Args:
        page: Playwright Page。
        url: 目标 URL。
        retries: 额外重试次数（默认 2 次）。
    """
    for attempt in range(retries + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            return
        except Exception as e:
            if attempt >= retries:
                raise
            logger.warning(f"导航失败（{attempt + 1}/{retries}）{url[:60]}...: {e}")
            time.sleep(2 ** (attempt + 1))


def discover_user_id(page) -> str | None:
    """自动发现当前登录用户 ID（不硬编码）。

    依次尝试：顶部头像链接 → __INITIAL_STATE__ → localStorage。

    Args:
        page: Playwright Page（会先导航到小红书首页）。

    Returns:
        用户 ID 字符串；全部失败返回 None。
    """
    try:
        goto_with_retry(page, "https://www.xiaohongshu.com/explore")
    except Exception as e:
        logger.warning(f"打开小红书首页失败（尝试从当前页探测）: {e}")

    # 1) 顶部头像链接
    try:
        links = page.query_selector_all("a[href*='/user/profile/']")
        for link in links:
            uid = extract_user_id_from_href(link.get_attribute("href") or "")
            if uid:
                logger.info(f"从头像链接发现用户 ID: {uid}")
                return uid
    except Exception as e:
        logger.warning(f"头像链接探测失败: {e}")

    # 2) __INITIAL_STATE__
    try:
        uid = page.evaluate(
            "() => window.__INITIAL_STATE__?.user?.userInfo?.userId || ''"
        )
        if uid:
            logger.info(f"从 __INITIAL_STATE__ 发现用户 ID: {uid}")
            return uid
    except Exception:
        pass

    # 3) localStorage
    try:
        uid = page.evaluate(
            "() => { try { const u = JSON.parse(localStorage.getItem('userInfo') || '{}'); "
            "return u.userId || u.id || ''; } catch (e) { return ''; } }"
        )
        if uid:
            logger.info(f"从 localStorage 发现用户 ID: {uid}")
            return uid
    except Exception:
        pass

    return None


def fetch_following_list(page, max_pages: int) -> list[dict]:
    """通过 IM 关注列表 API 分页拉取全部关注用户。

    在页面内 fetch（credentials: include 复用登录态），实测单页即可返回全部；
    循环分页仅作为大列表时的兜底。

    Args:
        page: Playwright Page。
        max_pages: 分页拉取上限。

    Returns:
        采集行列表 [{nickname, xhs_id, ip_location, uid}]。
    """
    rows: list[dict] = []
    seen_uids: set[str] = set()
    for page_no in range(1, max_pages + 1):
        try:
            raw = page.evaluate(
                _FETCH_FOLLOWING_JS,
                {"api": FOLLOWING_API, "pageNo": page_no, "size": FOLLOWING_PAGE_SIZE},
            )
        except Exception as e:
            logger.warning(f"关注列表接口第 {page_no} 页拉取失败: {e}")
            break
        batch = parse_following_json(raw)
        if not batch:
            logger.warning(f"关注列表接口第 {page_no} 页无数据，停止分页")
            break
        for r in batch:
            if r["uid"] not in seen_uids:
                seen_uids.add(r["uid"])
                rows.append(r)
        logger.info(f"关注列表第 {page_no} 页拉取 {len(batch)} 人（累计 {len(rows)}）")
        if len(batch) < FOLLOWING_PAGE_SIZE:
            break
        _rdsleep(0.5, 1.0)
    return rows


def fetch_user_detail(page, uid: str) -> tuple[str, str]:
    """进入用户主页提取 (小红书号, IP 属地)。

    Args:
        page: Playwright Page。
        uid: 用户 ID。

    Returns:
        (小红书号, IP属地) 二元组，未提取到的字段为空字符串。
    """
    goto_with_retry(page, f"https://www.xiaohongshu.com/user/profile/{uid}")
    try:
        page.wait_for_selector(".user-info, [class*='user-info']", timeout=10000)
    except Exception:
        pass  # 页面结构变化时靠文本兜底

    text = ""
    try:
        info = page.query_selector(".user-info, [class*='user-info']")
        if info:
            text = info.inner_text() or ""
    except Exception:
        pass
    if not text:
        try:
            text = (page.inner_text("body") or "")[:30000]
        except Exception:
            text = ""
    return parse_xhs_id(text), parse_ip_location(text)


def backfill_details(
    page, rows: list[dict], max_visits: int, save_callback=None
) -> list[dict]:
    """逐个进入详情不完整的用户主页回填小红书号与 IP 属地（每个主页前随机 sleep 1~2 秒）。

    详情不完整 = 缺小红书号或缺 IP 属地；每完成 10 人调用一次 save_callback 落盘，
    避免中途中断（浏览器关闭/手动停止）丢失已回填的详情。

    Args:
        page: Playwright Page。
        rows: 采集结果列表（就地更新 xhs_id / ip_location 字段）。
        max_visits: 单次运行访问主页上限（防风控）。
        save_callback: 可选回调 save_callback(rows)，用于定期写盘。

    Returns:
        更新后的 rows。
    """
    targets = [r for r in rows if not r.get("xhs_id") or not r.get("ip_location")]
    logger.info(f"待回填详情: {len(targets)} 人（上限 {max_visits}）")
    done = 0
    for r in targets:
        if done >= max_visits:
            logger.warning(
                f"已达回填上限 {max_visits}，剩余 {len(targets) - done} 人保持空号"
            )
            break
        _rdsleep(1.0, 2.0)  # 主页访问间隔，防风控
        try:
            xhs_id, ip_location = fetch_user_detail(page, r["uid"])
            if xhs_id:
                r["xhs_id"] = xhs_id
            else:
                logger.warning(f"未在主页找到小红书号: {r['nickname']}")
            if ip_location:
                r["ip_location"] = ip_location
        except Exception as e:
            logger.warning(f"回填失败 {r['nickname']}（uid={r['uid']}）: {e}")
        done += 1
        if done % 10 == 0:
            print(f"  回填进度 {done}/{min(len(targets), max_visits)}")
            if save_callback is not None:
                try:
                    save_callback(rows)
                except Exception as e:
                    logger.warning(f"回填进度落盘失败: {e}")
    return rows


# ============================================================
#  输出与统计
# ============================================================


def _save_debug_screenshot(page) -> None:
    """保存调试截图到 storage/debug/，用于页面改版时排查。"""
    try:
        debug_dir = settings.storage_root / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        path = debug_dir / f"xhs_follow_{int(time.time())}.png"
        page.screenshot(path=str(path))
        logger.info(f"调试截图已保存: {path}")
    except Exception as e:
        logger.warning(f"截图失败: {e}")


def print_stats(rows: list[dict]) -> None:
    """打印最终统计：总人数、小红书号获取情况、IP 属地分布、缺失项名单。"""
    total = len(rows)
    with_id = sum(1 for r in rows if r.get("xhs_id"))
    with_ip = sum(1 for r in rows if r.get("ip_location"))
    print("\n" + "=" * 50)
    print("采集统计")
    print("=" * 50)
    print(f"总人数:            {total}")
    if total:
        print(f"获取到小红书号:     {with_id}（{with_id / total * 100:.1f}%）")
        print(f"获取到 IP 属地:    {with_ip}（{with_ip / total * 100:.1f}%）")
    else:
        print("获取到小红书号:     0")

    ip_counter: Counter = Counter((r.get("ip_location") or "未知") for r in rows)
    print("IP 属地分布:")
    for ip, cnt in ip_counter.most_common():
        print(f"  {ip}: {cnt}")

    missing_id = [r["nickname"] for r in rows if not r.get("xhs_id")]
    if missing_id:
        print(f"未获取小红书号 ({len(missing_id)}): {', '.join(missing_id[:20])}")
    missing_ip = [r["nickname"] for r in rows if not r.get("ip_location")]
    if missing_ip:
        print(f"未获取 IP 属地 ({len(missing_ip)}): {', '.join(missing_ip[:20])}")


# ============================================================
#  主流程
# ============================================================


def parse_args(argv=None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="采集小红书关注列表（昵称 / 小红书号 / IP 属地）"
    )
    parser.add_argument("--port", type=int, default=9222, help="CDP 调试端口（默认 9222）")
    parser.add_argument(
        "--max-pages", type=int, default=5, help="关注列表接口分页拉取上限（默认 5）"
    )
    parser.add_argument(
        "--no-fetch-ids", action="store_true", help="跳过进入主页回填小红书号与 IP 属地"
    )
    parser.add_argument(
        "--max-profile-visits",
        type=int,
        default=50,
        help="回填详情时访问主页数量上限（默认 50）",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="断点续跑：读取已有 CSV 复用已回填详情，只补全缺小红书号或 IP 属地的用户",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="",
        help="CSV 输出路径（默认 backend/storage/xhs_following.csv）",
    )
    parser.add_argument(
        "--login-timeout", type=int, default=180, help="等待扫码登录超时秒数（默认 180）"
    )
    parser.add_argument(
        "--debug-screenshot",
        action="store_true",
        help="出错或结束时保存调试截图到 storage/debug/",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    """脚本主流程。

    Args:
        argv: 命令行参数列表（默认取 sys.argv[1:]）。

    Returns:
        进程退出码（0 成功 / 1 失败 / 130 用户中断）。
    """
    _setup_stdout_utf8()
    args = parse_args(argv)
    setup_logging(settings.storage_root / "xhs_following.log")
    logger.info(f"===== 开始采集小红书关注列表 (port={args.port}) =====")

    output = Path(args.output) if args.output else settings.storage_root / "xhs_following.csv"
    pw = None
    page = None
    try:
        # ── 1. CDP 探测 / 自动启动 ──
        ok, detail, is_chrome = _probe_cdp(args.port)
        if not ok:
            logger.info(f"端口 {args.port} 不可用（{detail}），自动启动调试 Chrome...")
            _launch_chrome(args.port)
        elif not is_chrome:
            raise RuntimeError(f"端口 {args.port} 被非 Chrome 程序占用: {detail}")
        else:
            logger.info(detail)

        pw, browser = connect_cdp(args.port)
        logger.info(f"已连接 Chrome {browser.version}")
        if not browser.contexts:
            raise RuntimeError("CDP 浏览器无可用上下文")
        context = browser.contexts[0]
        page = context.new_page()

        # ── 2. 登录态检查 / 扫码等待 ──
        if _is_logged_in(context):
            logger.info("检测到小红书登录态，直接开始")
        else:
            _wait_login(page, context, args.login_timeout)

        # ── 3. 自动发现用户 ID（用于确认登录身份，列表不依赖它） ──
        uid = discover_user_id(page)
        if uid:
            logger.info(f"发现当前用户 ID: {uid}")
        else:
            logger.warning("未自动发现用户 ID（不影响关注列表 API 拉取）")

        # ── 4. 通过 IM API 拉取全部关注 ──
        rows = fetch_following_list(page, args.max_pages)
        if not rows:
            raise RuntimeError(
                "关注列表为空或接口异常：请确认已在 Chrome 中登录小红书后重试"
            )
        rows = dedupe_rows(rows)
        logger.info(f"关注列表去重后共 {len(rows)} 人")

        # ── 4.5 断点续跑：复用已有 CSV 的详情，只补全缺失部分 ──
        if args.resume:
            existing = load_existing_csv(output)
            if existing:
                reused = 0
                for r in rows:
                    prev = existing.get(r["nickname"])
                    if prev:
                        if prev.get("xhs_id"):
                            r["xhs_id"] = prev["xhs_id"]
                        if prev.get("ip_location"):
                            r["ip_location"] = prev["ip_location"]
                        if r["xhs_id"]:
                            reused += 1
                logger.info(
                    f"断点续跑：复用 {reused} 人已有详情，"
                    f"待补全 {sum(1 for r in rows if not r['xhs_id'] or not r['ip_location'])} 人"
                )

        # 采集完成先落一份快照，回填失败也不至于全丢
        write_csv(rows, output)
        logger.info(f"CSV 快照已写入: {output}（回填前）")

        # ── 5. 回填小红书号 + IP 属地（可选，默认开启） ──
        if not args.no_fetch_ids:
            rows = backfill_details(
                page,
                rows,
                args.max_profile_visits,
                save_callback=lambda r: write_csv(r, output),
            )
            write_csv(rows, output)
            logger.info(f"回填后 CSV 已更新: {output}")

        # ── 6. 统计与收尾 ──
        print_stats(rows)
        if args.debug_screenshot and page is not None:
            _save_debug_screenshot(page)
        print(f"\n✅ 完成：结果已保存到 {output}")
        return 0
    except KeyboardInterrupt:
        print("\n用户中断，已退出")
        return 130
    except Exception as e:
        logger.error(f"采集失败: {e}", exc_info=True)
        print(f"\n❌ 采集失败: {e}")
        if args.debug_screenshot and page is not None:
            _save_debug_screenshot(page)
        return 1
    finally:
        # CDP 模式不关闭用户 Chrome；仅回收 Playwright 客户端
        try:
            if pw is not None:
                pw.stop()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
