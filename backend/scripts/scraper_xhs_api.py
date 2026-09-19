"""小红书站内接口采集：xhshow 纯 Python 签名，不打开详情页拿全量图片。

为什么需要本模块（补强原有 Playwright 详情页路径）：

  - 搜索/主页卡片只渲染 1 张封面，轮播图（3~9 张）只在详情页出现
  - Playwright `goto` 详情页在**渲染层**触发风控：xsec_token 失效被重定向 404、
    频繁访问触发「扫码后再手机查看」（见 TODO.md「小红书多图与视频采集」）

本模块改走两条服务端路径，全程不渲染页面：

  1. ``user_posted`` 接口 → 博主全部笔记（note_id / xsec_token / 分页游标）
  2. 笔记详情页 HTML 的 ``window.__INITIAL_STATE__.noteDetailMap``
     → 完整 ``imageList``（轮播图全量）+ desc / tagList / video

签名由 ``xhshow``（MIT，纯 Python 实现 x-s / x-s-common）本地生成：无浏览器、
无 Tampermonkey 用户脚本、无 TLS 指纹伪装。

实测约束（改动前先读，否则会退回 HTTP 406）：

  1. **签名串必须与请求串逐字节一致**。GET 必须用 ``Xhshow.build_url()`` 拼出
     完整 URL 后原样请求——把 params 交给 httpx 拼会把逗号编码成 ``%2C``，而签名
     按原文计算 → 签名失配 → 406（已实测）。
  2. ``noteDetailMap`` 子树是**干净 JSON**，直接 ``json.loads`` 即可；不要对整个
     ``__INITIAL_STATE__`` 做 ``undefined → null`` 字符串替换——外层 state 尾部
     仍有非法字面量，替换后依然解析失败（已实测），项目既有注释也把该做法标为脆弱。
  3. 图片 URL 的 ``notes_pre_post/`` 等中间路径段必须保留，否则原图 CDN 404
     （见 ``scraper_common.xhs_image_trace_id``）。
  4. ``feed`` 接口（POST）即使body字节与签名一致仍返回 406，本模块不使用它。

失败策略：本模块是「更优路径」而非「唯一路径」。除致命风控外一律返回空值让调用方
回退 Playwright——任何异常都不应让采集能力劣于改造前。
"""

import json
from urllib.parse import quote

import httpx

from .scraper_common import (
    ScraperBlockedError,
    _rdsleep,
    match_xhs_block_text,
    xhs_video_urls_from_page_state,
)

try:  # 可选依赖：缺失时回退 Playwright（CI / 未升级环境不应因此崩溃）
    from xhshow import Xhshow

    _HAS_XHSHOW = True
except ImportError:  # pragma: no cover - 仅在未安装 xhshow 的环境命中
    Xhshow = None
    _HAS_XHSHOW = False


# ═══════════════════════════════════════════════════════════════
#  常量
# ═══════════════════════════════════════════════════════════════

"""签名接口主机（与网页同源的 edith 域，项目 Cookie 探测也用该域）。"""
XHS_API_HOST = "https://edith.xiaohongshu.com"

"""网页主机（笔记详情页 HTML 从这里取）。"""
XHS_WEB_HOST = "https://www.xiaohongshu.com"

"""博主笔记列表接口路径。"""
XHS_USER_POSTED_PATH = "/api/sns/web/v1/user_posted"

"""笔记列表单页条数。"""
XHS_POSTED_PAGE_SIZE = 30

"""向接口声明可接受的图片格式（影响 urlDefault 的 `!nd_dft_*` 后缀）。"""
XHS_IMAGE_FORMATS = "jpg,webp,avif"

"""详情页请求的 xsec_source 取值：从博主主页进入的笔记。"""
XHS_XSEC_SOURCE = "pc_user"

"""接口请求 UA（缺完整 UA 会被网关按非浏览器请求拦掉）。"""
XHS_API_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

"""HTTP 状态 → 致命风控类型（沿用 scraper_common 的风控词汇）。"""
_XHS_FATAL_STATUS = {
    401: "login_wall",
    403: "login_wall",
    429: "rate_limit",
    461: "login_wall",
    471: "account_risk",
}


class XhsApiError(RuntimeError):
    """接口采集失败（非风控）：调用方据此回退 Playwright 路径。"""


def xhs_api_available() -> bool:
    """xhshow 是否可用（不可用时调用方应直接走 Playwright）。"""
    return _HAS_XHSHOW


# ═══════════════════════════════════════════════════════════════
#  纯函数（无网络、无浏览器，便于单测）
# ═══════════════════════════════════════════════════════════════


def user_id_of(profile_url: str) -> str:
    """从博主主页 URL 解析 user_id。

    非主页链接（笔记/搜索/话题）返回空串——接口只认
    ``/user/profile/{uid}``，混用会把笔记 ID 当 uid 传过去。

    Args:
        profile_url: 博主主页 URL（可带 xsec_token 等 query）。

    Returns:
        user_id；无法解析时返回空串。
    """
    path = (profile_url or "").split("?", 1)[0]
    if "/user/profile/" not in path:
        return ""
    uid = path.split("/user/profile/", 1)[1].split("/")[0].strip()
    return uid if uid.isalnum() else ""


def cookie_dict(cookies, domain_hint: str = "xiaohongshu") -> dict[str, str]:
    """把各种 Cookie 形态归一成 ``{name: value}``。

    实际出现三种形态：``{name: value}``、``{name: {name, value, ...}}``
    （run_scraper 从 Playwright context 取）、``[{name, value, domain, ...}]``
    （storage/cookies/*.json 的导出格式）。

    Args:
        cookies: 上述任一形态。
        domain_hint: 只保留域名含该子串的条目。浏览器上下文含全站 Cookie，
            混入其他站点会让 x-s-common 与实际站点对不上。

    Returns:
        Cookie 字典。
    """
    result: dict[str, str] = {}
    if isinstance(cookies, dict):
        for name, value in cookies.items():
            if isinstance(value, dict):
                domain = str(value.get("domain") or "")
                if domain and domain_hint not in domain:
                    continue
                result[str(name)] = str(value.get("value") or "")
            else:
                result[str(name)] = str(value or "")
        return result
    for item in cookies or ():
        if not isinstance(item, dict) or not item.get("name"):
            continue
        domain = str(item.get("domain") or "")
        if domain and domain_hint not in domain:
            continue
        result[str(item["name"])] = str(item.get("value") or "")
    return result


def find_balanced_object(text: str, start: int) -> str:
    """从 ``text[start]``（必须是 ``{``）起做括号配平扫描，返回完整对象字面量。

    为什么不用正则：``\\{.*?\\}`` 非贪婪匹配会在第一个 ``}`` 收尾，嵌套对象会被
    截断；贪婪又会在下一个对象处越界。配平扫描（跳过字符串与转义）才能稳定取到
    对象真实边界。

    Args:
        text: 源文本。
        start: ``{`` 的下标。

    Returns:
        完整对象字面量（含首尾花括号）。

    Raises:
        ValueError: 未找到配平的结尾（HTML 被截断或页面结构变化）。
    """
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise ValueError("对象未闭合（页面结构变化或响应被截断）")


def extract_note_detail_state(html: str) -> dict:
    """从笔记详情页 HTML 取 ``noteDetailMap`` 子树。

    只切子树而非整份 ``__INITIAL_STATE__``：外层含 ``undefined`` 等 JS 字面量，
    整体解析需要字符串替换（脆弱且实测尾部仍有非法字面量）；``noteDetailMap``
    本身是干净 JSON，直接 ``json.loads`` 即可。

    Args:
        html: 详情页 HTML。

    Returns:
        ``{note_id: {"note": {...}, ...}}``；找不到或解析失败返回空字典。
    """
    key = '"noteDetailMap"'
    pos = html.find(key)
    if pos < 0:
        return {}
    brace = html.find("{", pos + len(key))
    if brace < 0:
        return {}
    try:
        blob = find_balanced_object(html, brace)
        data = json.loads(blob)
    except (ValueError, json.JSONDecodeError):
        # 页面结构变化/响应截断：降级为空，由调用方回退 Playwright
        return {}
    return data if isinstance(data, dict) else {}


def first_note_of(note_map: dict) -> dict:
    """从 ``noteDetailMap`` 取第一篇笔记数据。

    Args:
        note_map: ``extract_note_detail_state`` 的返回值。

    Returns:
        笔记字典（``note`` 子节点优先）；无数据返回空字典。
    """
    if not isinstance(note_map, dict) or not note_map:
        return {}
    first = next(iter(note_map.values()), None)
    if not isinstance(first, dict):
        return {}
    note = first.get("note")
    return note if isinstance(note, dict) else first


def note_url_of(note: dict) -> str:
    """笔记记录 → 站点笔记 URL（**不带 xsec_token**）。

    不带 token 是刻意的：该 URL 会作为 ``source_url`` 落库，token 会过期，
    存进去的地址很快就打不开；去 token 后与 ``note_id_of()`` 口径一致，
    也天然完成「同一篇笔记不同 token 只算一篇」的幂等。

    Args:
        note: ``user_posted`` 的笔记记录。

    Returns:
        笔记 URL；无 note_id 返回空串。
    """
    note_id = str(note.get("note_id") or "").strip()
    return f"{XHS_WEB_HOST}/explore/{note_id}" if note_id else ""


def parse_user_posted(payload: dict) -> tuple[list[dict], str, bool]:
    """解析 ``user_posted`` 响应。

    Args:
        payload: 接口返回的 JSON。

    Returns:
        ``(笔记列表, 下一页游标, 是否还有更多)``。
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return [], "", False
    notes = [n for n in (data.get("notes") or []) if isinstance(n, dict)]
    return notes, str(data.get("cursor") or ""), bool(data.get("has_more"))


def video_state_of(note: dict) -> dict:
    """从笔记数据的 ``video`` 节点抽出视频状态（供无水印直链复用）。

    与 Playwright 路径从页面 state 抠出的结构保持一致，于是
    ``xhs_video_urls_from_page_state`` 可以原样复用（无水印原片优先）。

    Args:
        note: 笔记数据。

    Returns:
        ``{"originKey": str, "masters": [str, ...]}``。
    """
    video = note.get("video") or {}
    consumer = video.get("consumer") or {}
    stream = (video.get("media") or {}).get("stream") or {}
    masters: list[str] = []
    for item in stream.get("h264") or []:
        if not isinstance(item, dict):
            continue
        url = item.get("masterUrl") or item.get("master_url") or ""
        if url and url not in masters:
            masters.append(url)
    return {
        "originKey": str(consumer.get("originVideoKey") or ""),
        "masters": masters,
    }


def note_state_to_detail(note: dict) -> dict:
    """笔记数据 → 与 ``scraper_xhs.extract_note_detail`` **同形**的详情字典。

    保持同形是关键：``run_blogger_mode`` 的下载/入库/漏斗/日志全部复用，
    接口与 DOM 两个来源可互换。

    图片返回**原始** ``urlDefault``（不在这里展开原图候选链）——候选链由
    ``scraper_download.download_batch`` 统一展开，与 DOM 路径口径一致。

    Args:
        note: 笔记数据（``noteDetailMap`` 里的 ``note`` 节点）。

    Returns:
        ``{"img_urls": [...], "video_urls": [...], "caption": str, "tags": [...]}``。
    """
    images = note.get("imageList") or note.get("image_list") or []
    img_urls: list[str] = []
    for item in images:
        if not isinstance(item, dict):
            continue
        url = (
            item.get("urlDefault")
            or item.get("url_default")
            or item.get("url")
            or ""
        )
        if isinstance(url, str) and url.strip() and url not in img_urls:
            img_urls.append(url.strip())

    caption = str(note.get("desc") or note.get("title") or "")
    tags = [
        str(t.get("name")).strip()
        for t in (note.get("tagList") or note.get("tag_list") or [])
        if isinstance(t, dict) and str(t.get("name") or "").strip()
    ]
    return {
        "img_urls": img_urls,
        "video_urls": xhs_video_urls_from_page_state(video_state_of(note)),
        "caption": caption,
        "tags": tags,
    }


# ═══════════════════════════════════════════════════════════════
#  接口客户端
# ═══════════════════════════════════════════════════════════════


def _api_headers(referer: str) -> dict[str, str]:
    """签名接口的浏览器请求头（签名头由调用方合并进来）。"""
    return {
        "User-Agent": XHS_API_UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Origin": XHS_WEB_HOST,
        "Referer": referer,
    }


def _html_headers() -> dict[str, str]:
    """笔记详情页 HTML 的浏览器请求头。"""
    return {
        "User-Agent": XHS_API_UA,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9",
    }


def _raise_for_status(resp: httpx.Response, detail: str) -> None:
    """HTTP 状态 → 风控异常 / 通用失败（风控类不可重试）。"""
    kind = _XHS_FATAL_STATUS.get(resp.status_code)
    if kind:
        raise ScraperBlockedError(kind, f"{detail} HTTP {resp.status_code}")
    if resp.status_code >= 400:
        raise XhsApiError(f"{detail} HTTP {resp.status_code}")


def _raise_for_payload(payload: dict, detail: str) -> None:
    """业务层 ``success: false`` → 风控异常 / 通用失败。

    复用 ``match_xhs_block_text``：接口把风控原因写在 ``msg`` 里时，与页面文案
    是同一套词汇（登录/验证/频繁/账号异常），无需另立映射表。
    """
    if not isinstance(payload, dict) or payload.get("success") is not False:
        return
    msg = str(payload.get("msg") or payload.get("message") or "")
    kind = match_xhs_block_text(msg)
    if kind:
        raise ScraperBlockedError(kind, detail)
    raise XhsApiError(f"{detail} 接口返回失败：{msg or payload}")


class XhsApiScraper:
    """小红书站内接口采集器（同步 HTTP，与既有爬虫的同步执行模型一致）。"""

    def __init__(self, cookies, timeout: int = 20) -> None:
        """初始化。

        Args:
            cookies: 任意形态的 Cookie（见 :func:`cookie_dict`）。
            timeout: 单次请求超时（秒）。

        Raises:
            XhsApiError: xhshow 未安装或 Cookie 为空。
        """
        if not _HAS_XHSHOW:
            raise XhsApiError("未安装 xhshow（pip install xhshow），无法走接口采集")
        self.cookies = cookie_dict(cookies)
        if not self.cookies:
            raise XhsApiError("小红书 Cookie 为空，无法调用接口")
        self._signer = Xhshow()
        self._client = httpx.Client(
            cookies=self.cookies, timeout=timeout, follow_redirects=True
        )

    # ── 内部 ──

    def _request(
        self, method: str, url: str, headers: dict, **kwargs
    ) -> httpx.Response:
        try:
            resp = self._client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as e:
            raise XhsApiError(f"网络异常：{type(e).__name__}: {str(e)[:120]}") from e
        _raise_for_status(resp, url[:120])
        return resp

    def _signed_get(self, path: str, params: dict, referer: str) -> dict:
        """签名 GET（URI 与请求串逐字节一致，见模块文档约束 1）。"""
        url = self._signer.build_url(XHS_API_HOST + path, params)
        signed = self._signer.sign_headers_get(
            uri=XHS_API_HOST + path, cookies=self.cookies, params=params
        )
        resp = self._request(
            "GET", url, headers={**_api_headers(referer), **signed}
        )
        try:
            payload = resp.json()
        except ValueError as e:
            raise XhsApiError(f"{path} 响应非 JSON") from e
        _raise_for_payload(payload, path)
        return payload

    # ── 对外 ──

    def blogger_notes(
        self, user_id: str, max_notes: int, delay: float = 1.0
    ) -> list[dict]:
        """分页拉取博主笔记列表。

        Args:
            user_id: 博主 user_id。
            max_notes: 笔记数上限。
            delay: 翻页间隔基准（秒），实际取 0.5~1.5 倍随机。

        Returns:
            笔记记录列表（含 note_id / xsec_token，按发布时间倒序）。

        Raises:
            ScraperBlockedError: 命中致命风控。
            XhsApiError: 其他失败。
        """
        notes: list[dict] = []
        seen_ids: set[str] = set()
        seen_cursors: set[str] = {""}
        cursor = ""
        while len(notes) < max_notes:
            payload = self._signed_get(
                XHS_USER_POSTED_PATH,
                {
                    "num": str(min(XHS_POSTED_PAGE_SIZE, max_notes - len(notes))),
                    "cursor": cursor,
                    "user_id": user_id,
                    "image_formats": XHS_IMAGE_FORMATS,
                },
                referer=f"{XHS_WEB_HOST}/user/profile/{user_id}",
            )
            page, next_cursor, has_more = parse_user_posted(payload)
            for note in page:
                note_id = str(note.get("note_id") or "")
                if not note_id or note_id in seen_ids:
                    continue
                seen_ids.add(note_id)
                notes.append(note)
                if len(notes) >= max_notes:
                    break
            # 游标不前进即止：接口异常时会一直回同一页，无保护会空转打满风控
            if not has_more or not next_cursor or next_cursor in seen_cursors:
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor
            _rdsleep(delay * 0.5, delay * 1.5)
        return notes

    def note_detail(self, note_id: str, xsec_token: str = "") -> dict:
        """拉取单篇笔记详情（详情页 HTML → ``noteDetailMap`` → 详情字典）。

        Args:
            note_id: 笔记 ID。
            xsec_token: 笔记 token（``user_posted`` 返回；缺失时多数笔记会被
                重定向到错误页）。

        Returns:
            与 ``extract_note_detail`` 同形的详情字典。

        Raises:
            ScraperBlockedError: 致命风控，或笔记已删除/不可见（非致命，跳过本篇）。
            XhsApiError: 其他失败（页面结构变化等）。
        """
        url = f"{XHS_WEB_HOST}/explore/{note_id}"
        if xsec_token:
            url = (
                f"{url}?xsec_token={quote(xsec_token, safe='')}"
                f"&xsec_source={XHS_XSEC_SOURCE}"
            )
        resp = self._request("GET", url, headers=_html_headers())
        if "/explore/" not in str(resp.url):
            # token 失效/笔记删除会被重定向到错误页：非致命，跳过本篇
            raise ScraperBlockedError(
                "not_found", f"笔记 {note_id} 被重定向到 {str(resp.url)[:80]}"
            )
        note = first_note_of(extract_note_detail_state(resp.text))
        if not note:
            raise XhsApiError(f"笔记 {note_id} 详情页未取到 noteDetailMap")
        return note_state_to_detail(note)

    def close(self) -> None:
        """关闭底层连接池。"""
        self._client.close()


# ═══════════════════════════════════════════════════════════════
#  回退策略封装
# ═══════════════════════════════════════════════════════════════


def try_fetch_blogger_notes(
    profile_url: str,
    platform_user_id: str,
    cookies,
    max_notes: int,
    delay: float = 1.0,
) -> tuple[XhsApiScraper, list[dict]] | None:
    """尽力用接口拉博主笔记列表；不可用或失败时返回 ``None`` 让调用方回退。

    致命风控照常抛出：此时回退 Playwright 只会在已被限流的账号上继续加压，
    必须停止整轮（与 ``run_blogger_mode`` 中详情页风控的处理口径一致）。

    Args:
        profile_url: 博主主页 URL。
        platform_user_id: 博主 user_id（profile_url 解析失败时兜底）。
        cookies: 任意形态 Cookie。
        max_notes: 笔记数上限。
        delay: 翻页间隔基准（秒）。

    Returns:
        ``(客户端, 笔记列表)``；接口不可用/失败返回 ``None``。
        客户端需由调用方在使用完毕后 ``close()``。
    """
    if not _HAS_XHSHOW:
        return None
    user_id = user_id_of(profile_url) or str(platform_user_id or "").strip()
    if not user_id:
        return None
    scraper = None
    try:
        scraper = XhsApiScraper(cookies)
        notes = scraper.blogger_notes(user_id, max_notes, delay)
    except ScraperBlockedError:
        if scraper:
            scraper.close()
        raise
    except Exception as e:  # 任何非风控失败都回退，保证不劣于改造前
        if scraper:
            scraper.close()
        print(f"  接口采集不可用（{type(e).__name__}: {str(e)[:100]}），回退详情页路径")
        return None
    if not notes:
        scraper.close()
        print("  接口未返回笔记，回退详情页路径")
        return None
    return scraper, notes
