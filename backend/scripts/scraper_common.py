"""爬虫通用常量与工具函数 — 零业务逻辑的配置/工具层。

所有爬虫模块共享的常量定义、通用工具函数、平台通用逻辑（登录检测等）
均在此文件中集中管理，避免重复定义和硬编码散落各处。
"""

import random
import time
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings


# ═══════════════════════════════════════════════════════════════
#  常量定义
# ═══════════════════════════════════════════════════════════════

"""各排序类型对应的标签文本（用于日志输出）。"""
SORT_LABELS = {
    "general": "综合",
    "time_descending": "最新",
    "popularity_descending": "最热",
}

"""各平台搜索排序方式（固定顺序，作为断点续采执行计划的基础）。"""
SORT_TYPES = ["general", "popularity_descending", "time_descending"]

"""各平台媒体 CDN 下载所需的 Referer（缺失或填错平台域名会被 CDN 拒绝）。"""
DOWNLOAD_REFERERS = {
    "xiaohongshu": "https://www.xiaohongshu.com/",
    "douyin": "https://www.douyin.com/",
}

"""登录检测用的会话 Cookie 名（命中任一即视为已登录）。"""
LOGIN_COOKIE_NAMES = {
    "xiaohongshu": ("web_session", "a1"),  # 历史版本登录态为 a1
    # 注意：SESSDATA 是 B 站的会话 Cookie 名，抖音网页版实际是 sessionid
    # 系列（2026-08 实测调试 Chrome 真实登录态）。写错导致已登录也被判
    # 未登录 → 每次任务都导航到抖音首页（精选推荐流）空等满 180s。
    "douyin": ("sessionid", "sessionid_ss", "sid_tt", "sid_guard"),
}

"""未登录时引导用户扫码的平台首页 URL。"""
PLATFORM_HOME_URLS = {
    "xiaohongshu": "https://www.xiaohongshu.com/explore",
    "douyin": "https://www.douyin.com/",
}

"""平台名称映射（中文）用于日志输出。"""
PLATFORM_NAMES = {"xiaohongshu": "小红书", "douyin": "抖音"}

"""下载请求头中的通用 User-Agent。"""
DOWNLOAD_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
)

"""单视频下载大小上限（字节）：抖音更小以控磁盘；小红书沿用历史 100MB。"""
DEFAULT_VIDEO_MAX_BYTES = 100 * 1024 * 1024
MAX_VIDEO_BYTES = {
    "xiaohongshu": 100 * 1024 * 1024,
    "douyin": 50 * 1024 * 1024,
}

"""抖音内容页链接锚点（搜索结果卡与博主页作品网格共用）。"""
DOUYIN_DETAIL_ANCHOR = "a[href*='/video/'], a[href*='/note/']"

"""图集容器选择器（最多图选择器，命中即不必全页过滤）。"""
DOUYIN_SLIDE_IMG_SELECTOR = (
    'div[data-e2e="slide-list"] img, div[class*="slide"] img'
)

"""详情页主体就绪信号（标题或描述任一出现即认为渲染完成）。"""
DOUYIN_DETAIL_READY = (
    "[data-e2e='video-desc'], [data-e2e='slide-list'], h1, video"
)

"""正文描述候选选择器（按优先级取第一个非空）。"""
DOUYIN_DESC_SELECTORS = (
    "[data-e2e='video-desc']",
    ".video-info-detail .title",
    "h1[data-e2e='video-title']",
    "span[class*='desc']",
)

"""话题标签锚点选择器（点击进入话题聚合页的 <a>）。"""
DOUYIN_HASHTAG_ANCHOR = "a[href*='/hashtag/']"

"""直连经典搜索页 URL 模板（type=video 只出视频/图集笔记）。

入口有效性会被抖音轮换（2026-08-28 实测：直连 type=video 正常渲染、
搜索框回车落地的 jingxuan 页被静默风控成空壳——与此前的结论相反），
故采集端以「DOM + 搜索接口响应」双层合并，不押注单一入口。"""
DOUYIN_SEARCH_DIRECT_URL_FMT = "https://www.douyin.com/search/{kw}?type=video"

"""搜索结果接口特征：响应 URL 命中任一即视为搜索结果数据。

精选搜索被风控成空壳时 DOM 无卡片，但接口响应仍含完整结果
（实测 general/search/stream 响应 378KB、满是 aweme_id）。"""
DOUYIN_SEARCH_XHR_HINTS = (
    "general/search/stream",
    "general/search/single",
    "search/item",
)

"""视频 CDN 特征：网络响应 URL 命中任一即视为真实视频直链。"""
DOUYIN_VIDEO_URL_HINTS = ("douyinvod.com", "/aweme/v1/play/")

"""抖音机器人验证（滑块/验证码）特征选择器：命中任一即认为处于验证态。

注意：抖音会在每个页面预注入隐藏的验证容器模板，text= / [id*=] 选择器
连隐藏元素也会命中——调用方必须用 is_visible() 过滤（_is_verify_page
已内置），否则正常搜索页会被误判成验证态空等 180s（真实案例）。"""
DOUYIN_VERIFY_SELECTORS = (
    "#captcha_container",
    "[id*='captcha']",
    "[class*='captcha_verify']",
    "text=安全验证",
    "text=拖动滑块",
)

"""抖音首页 URL（搜索框入口导航用）。"""
DOUYIN_HOME_URL = "https://www.douyin.com/"

"""抖音首页搜索框选择器：首页 → 输入关键词 → 回车 进入精选搜索。

2026-08 实测：直连 /search/?type=general URL 只渲染导航壳（0 作品卡片，
无验证码，疑似静默风控）；首页搜索框回车落到 /jingxuan/search/ 页，
结果正常渲染（67 个 /video/ 锚点）。"""
DOUYIN_SEARCH_INPUT_SELECTOR = "input[data-e2e='searchbar-input']"

"""精选搜索结果首屏渲染等待上限（秒）：实测 >15s 才出首批卡片，60s
仍未出视为静默风控/加载失败。"""
DOUYIN_SEARCH_RENDER_WAIT = 60

"""抖音媒体 URL 归一化时补充的主机前缀。"""
DOUYIN_MEDIA_HOST = "https://www.douyin.com"


# ═══════════════════════════════════════════════════════════════
#  通用工具函数
# ═══════════════════════════════════════════════════════════════


def utcnow() -> datetime:
    """获取当前 UTC 时间（naive datetime，移除时区信息）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _rdsleep(lo: float = 0.5, hi: float = 2.0) -> None:
    """随机睡眠指定时间区间（秒），模拟人类操作间隔。

    Args:
        lo: 最小睡眠时间（秒）。
        hi: 最大睡眠时间（秒）。
    """
    time.sleep(random.uniform(lo, hi))


def _human_mouse_move(page) -> None:
    """随机移动鼠标到页面某处，模拟真人浏览时的无意识动作。

    Args:
        page: Playwright 页面对象。
    """
    try:
        vp = page.viewport_size or {"width": 1920, "height": 1080}
        w, h = vp.get("width", 1920), vp.get("height", 1080)
        page.mouse.move(
            random.randint(int(w * 0.15), int(w * 0.85)),
            random.randint(int(h * 0.15), int(h * 0.85)),
        )
    except Exception:
        pass  # 鼠标移动失败不影响主流程


def human_scroll(page, steps=None) -> None:
    """分步随机滚动到底部：随机步长 + 随机停顿，避免一步到底的机器特征。

    最终仍滚动到底以触发懒加载，但过程更接近真人浏览。
    分步数控制在 1~2 步，避免滚动事件被过度放大（每步都是一次可见滚动）。

    Args:
        page: Playwright 页面对象。
        steps: 滚动步数（可选，默认随机 1~2 步）。
    """
    steps = steps or random.randint(1, 2)
    for _ in range(steps):
        page.evaluate(f"window.scrollBy(0, {random.randint(400, 900)})")
        _rdsleep(0.6, 1.5)
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")


def clean_media_url(src: str) -> str:
    """清洗媒体 URL：去空白、补全协议头。

    Args:
        src: 原始 URL 字符串。

    Returns:
        清洗后的 URL 字符串（空字符串则返回空字符串）。
    """
    src = (src or "").strip()
    if src.startswith("//"):
        src = "https:" + src
    return src


def is_content_image(src: str) -> bool:
    """判断 URL 是否为「内容图」（排除头像/图标/logo/角标等非素材图）。

    Args:
        src: 媒体 URL 字符串。

    Returns:
        是否为内容图。
    """
    if not src.startswith("http"):
        return False
    low = src.lower()
    skip_kw = (
        "avatar", "icon", "logo", "emoji", "favicon",
        "qrcode", "qr_code", "verified",
    )
    return not any(k in low for k in skip_kw)


# ═══════════════════════════════════════════════════════════════
#  平台通用：下载请求头 / 登录检测
# ═══════════════════════════════════════════════════════════════


def build_download_headers(
    platform: str, cookies: dict | None = None
) -> dict:
    """构建平台匹配的媒体下载请求头。

    按平台取对应 Referer，并附带浏览器 Cookie 以通过 CDN 鉴权。

    Args:
        platform: 平台标识（"xiaohongshu" | "douyin"）。
        cookies: 浏览器 Cookie 字典。

    Returns:
        请求头字典。
    """
    req_headers = {
        "Referer": DOWNLOAD_REFERERS.get(platform, "https://www.xiaohongshu.com/"),
        "User-Agent": DOWNLOAD_UA,
    }
    if cookies:
        req_headers["Cookie"] = "; ".join(
            f"{c['name']}={c['value']}" for c in cookies.values()
        )
    return req_headers


def platform_has_login(cookies: list[dict], platform: str) -> bool:
    """根据浏览器 Cookie 判断平台是否处于登录状态。

    Args:
        cookies: 浏览器 Cookie 列表。
        platform: 平台标识。

    Returns:
        是否已登录。
    """
    names = LOGIN_COOKIE_NAMES.get(platform, ())
    domain_hint = PLATFORM_NAMES.get(platform, "")
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
    return False


def ensure_platform_login(
    context,
    page,
    platform: str,
    timeout: int = 180,
) -> bool:
    """确保平台已登录：未登录则导航到首页并轮询等待用户扫码。

    小红书与抖音共用一套逻辑（差异仅在首页 URL 与会话 Cookie 名）。
    超时后不强制失败——沿用原行为，以当前状态尝试采集，
    由漏斗统计自然暴露「无结果」问题。

    Args:
        context: Playwright 浏览器上下文。
        page: Playwright 页面对象。
        platform: 平台标识。
        timeout: 登录等待超时（秒），默认 180。

    Returns:
        最终是否检测到登录状态。
    """
    name = PLATFORM_NAMES.get(platform, platform)

    def _check() -> bool:
        return platform_has_login(context.cookies(), platform)

    if _check():
        print(f"{name}已在登录状态，直接开始采集")
        return True

    print(f"\n{'='*50}")
    print(f" >>> 请在 Chrome 中登录{name} <<<")
    print(" 已自动打开平台首页，请扫码登录")
    print(f" 登录完成后脚本自动检测并继续（{timeout}s 超时）")
    print(f"{'='*50}")

    # 将空白标签页导航到平台首页（未登录时展示扫码入口）
    try:
        page.goto(
            PLATFORM_HOME_URLS[platform],
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


def goto_with_retry(
    page, url: str, retries: int = 2, timeout: int = 30000
) -> str:
    """导航到指定 URL，异常时指数退避重试（抖音列表页/详情页共用）。

    导航是采集的硬前提：单次网络抖动 / ERR_ABORTED / 慢响应不应直接
    判死整轮采集（真实案例：任务 #43 一次导航异常即报「导航失败」，
    异常被吞、无重试、无留痕，无从排查）。

    Args:
        page: Playwright 页面对象。
        url: 目标 URL。
        retries: 失败后的额外重试次数（总尝试 = retries + 1）。
        timeout: 单次导航超时（毫秒）。

    Returns:
        空字符串表示成功；失败返回最后一次异常摘要
        （「类型: 消息」，供调用方写入漏斗/日志）。
    """
    last_error = ""
    for attempt in range(retries + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            return ""
        except Exception as e:
            last_error = f"{type(e).__name__}: {str(e)[:200]}"
            if attempt >= retries:
                return last_error
            print(
                f"  导航失败（第 {attempt + 1}/{retries + 1} 次尝试）"
                f"{url[:80]}: {last_error}"
            )
            time.sleep(2 ** (attempt + 1))
    return last_error
