"""平台 Cookie 真实有效性校验：轻量 HTTP 探测登录态。

此前 Cookie 有效性仅用「文件 mtime 距今 <72h」启发式判断（cookies.py），
无法发现提前失效的会话（账号被挤下线 / 服务端撤销会话），任务跑到一半
被登录墙拦截才知道。本模块对已导入的 Cookie 做真实探测：

  1. 前置检查：Cookie 文件存在、可解析、包含平台会话 Cookie 字段
     （小红书 web_session / 抖音 sessionid 系列）
  2. 真实探测：携带 Cookie 请求平台自用的轻量登录态接口
     - 小红书: GET edith.xiaohongshu.com/api/sns/web/v2/user/me
     - 抖音:   GET www.douyin.com/passport/account/info/v2/

判定原则：只有**确定性证据**才标记 invalid（未登录响应 / 缺会话字段 /
文件损坏）；网络抖动、风控、响应结构变化一律 unknown，避免误报导致
任务被错误拦截或用户被误导重导 Cookie。

结果带缓存（按文件 mtime + TTL）：供任务创建前置校验复用，避免每次建
任务都发真实请求；Cookie 重新导入后 mtime 变化，缓存自动失效。
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# 探测结果缓存有效期（秒）：任务创建前置校验复用，5 分钟内不重复发真实请求
_CACHE_TTL_SECONDS = 300
# platform → (cookie 文件 mtime, 缓存写入时刻 monotonic, 结果)
_verify_cache: dict[str, tuple[float, float, dict]] = {}

# 平台会话 Cookie 字段（与 scripts/scraper_common.LOGIN_COOKIE_NAMES 口径一致；
# 此处独立定义避免 app 层反向依赖 scripts 包）
_SESSION_COOKIE_NAMES = {
    "xiaohongshu": ("web_session", "a1"),
    "douyin": ("sessionid", "sessionid_ss", "sid_tt", "sid_guard"),
}

_PROBE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


# ═══════════════════════════════════════════════════════════════
#  各平台探测响应解释器
# ═══════════════════════════════════════════════════════════════


def _interpret_xhs(status: int, payload: dict | None) -> tuple[str, str]:
    """小红书 user/me 响应解释：200+success=true 为已登录。"""
    if status == 200 and isinstance(payload, dict) and payload.get("success") is True:
        return "valid", "登录态有效"
    if status in (401, 403, 461):
        return "invalid", f"服务端返回 {status}（未登录或会话失效）"
    if isinstance(payload, dict):
        msg = str(payload.get("msg") or payload.get("message") or "")
        if payload.get("success") is False or "未登录" in msg or "登录" in msg:
            return "invalid", f"服务端判定未登录（{msg[:80] or 'success=false'}）"
    return "unknown", "探测响应无法解读（可能被风控），不视为失效"


def _interpret_douyin(status: int, payload: dict | None) -> tuple[str, str]:
    """抖音 account/info/v2 响应解释：200 + data.user_id 为已登录。"""
    if status == 200 and isinstance(payload, dict):
        data = payload.get("data")
        user_id = data.get("user_id") if isinstance(data, dict) else None
        # user_id 为 0 / "0" 表示游客态
        if user_id and str(user_id) != "0":
            return "valid", "登录态有效"
        status_code = payload.get("status_code")
        error_code = payload.get("error_code")
        if status_code not in (0, None) or error_code not in (0, None):
            return "invalid", f"服务端判定未登录（status_code={status_code} error_code={error_code}）"
    if status in (401, 403):
        return "invalid", f"服务端返回 {status}（未登录或会话失效）"
    return "unknown", "探测响应无法解读（可能被风控），不视为失效"


_PROBES: dict[str, dict] = {
    "xiaohongshu": {
        "url": "https://edith.xiaohongshu.com/api/sns/web/v2/user/me",
        "domain_hint": "xiaohongshu",
        "referer": "https://www.xiaohongshu.com/",
        "interpret": _interpret_xhs,
    },
    "douyin": {
        "url": "https://www.douyin.com/passport/account/info/v2/",
        "domain_hint": "douyin",
        "referer": "https://www.douyin.com/",
        "interpret": _interpret_douyin,
    },
}


def _result(platform: str, state: str, detail: str, probe_url: str) -> dict:
    return {
        "platform": platform,
        "state": state,
        "detail": detail,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "probe_url": probe_url,
    }


def _cookie_header(cookies: list[dict], domain_hint: str) -> str:
    """按平台域名过滤并拼装 Cookie 请求头（无 domain 字段的条目保留）。"""
    parts: list[str] = []
    for c in cookies:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        dom = str(c.get("domain") or "")
        if dom and domain_hint not in dom:
            continue
        parts.append(f"{c['name']}={c.get('value') or ''}")
    return "; ".join(parts)


def _cookie_path(platform: str) -> Path:
    return Path(settings.storage_root) / "cookies" / f"{platform}_cookies.json"


def _file_mtime(cookie_file: Path) -> float:
    try:
        return cookie_file.stat().st_mtime
    except OSError:
        return 0.0


async def _probe(platform: str, probe: dict, cookie_file: Path) -> dict:
    """执行单次探测：前置检查 → 真实请求 → 解释响应。"""
    if not cookie_file.exists() or cookie_file.stat().st_size == 0:
        return _result(platform, "no_file", "尚未导入 Cookie 文件", probe["url"])

    try:
        raw = json.loads(cookie_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return _result(platform, "invalid", f"Cookie 文件解析失败: {e}", probe["url"])

    if not isinstance(raw, list):
        return _result(platform, "invalid", "Cookie 文件格式错误（应为 JSON 数组）", probe["url"])

    cookies = [c for c in raw if isinstance(c, dict)]
    names = {c.get("name") for c in cookies}
    session_names = _SESSION_COOKIE_NAMES[platform]
    if not any(n in session_names for n in names):
        return _result(
            platform, "invalid",
            f"Cookie 中缺少会话字段（{', '.join(session_names)}），登录态不可能有效",
            probe["url"],
        )

    header = _cookie_header(cookies, probe["domain_hint"])
    if not header:
        return _result(platform, "invalid", "Cookie 均不属于该平台域名", probe["url"])

    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(
                probe["url"],
                headers={
                    "User-Agent": _PROBE_UA,
                    "Referer": probe["referer"],
                    "Cookie": header,
                },
            )
    except Exception as e:
        logger.warning(f"{platform} Cookie 探测请求失败（不视为失效）: {e}")
        return _result(
            platform, "unknown",
            f"探测请求失败: {type(e).__name__}（网络不可达或被风控，不视为失效）",
            probe["url"],
        )

    try:
        payload = resp.json()
    except Exception:
        payload = None

    state, detail = probe["interpret"](resp.status_code, payload)
    return _result(platform, state, detail, probe["url"])


async def verify_platform_cookie(platform: str, force: bool = False) -> dict:
    """校验指定平台 Cookie 的真实登录态。

    Args:
        platform: 平台标识（xiaohongshu / douyin）。
        force: 跳过缓存强制探测（Cookie 管理页手动「校验」按钮用）。

    Returns:
        {"platform", "state", "detail", "checked_at", "probe_url", "cached"}
        state 取值: valid / invalid / unknown / no_file
    """
    from app.services.scraper.cookies import _validate_cookie_platform

    platform = _validate_cookie_platform(platform)
    cookie_file = _cookie_path(platform)

    if not force:
        cached = _verify_cache.get(platform)
        if (
            cached
            and cached[0] == _file_mtime(cookie_file)
            and time.monotonic() - cached[1] < _CACHE_TTL_SECONDS
        ):
            return {**cached[2], "cached": True}

    result = await _probe(platform, _PROBES[platform], cookie_file)
    _verify_cache[platform] = (_file_mtime(cookie_file), time.monotonic(), result)
    return {**result, "cached": False}


def peek_verification(platform: str) -> dict | None:
    """读取缓存的校验结果（不发请求）。无缓存返回 None。

    供 cookie-status 接口附带最近一次校验状态：Cookie 管理页无需
    主动触发校验也能看到上次结果（含任务创建时的自动校验）。
    """
    cached = _verify_cache.get(platform)
    if not cached:
        return None
    return {**cached[2], "cached": True}


def invalidate_verification(platform: str) -> None:
    """清除平台校验缓存（Cookie 导入/删除后调用，保持口径一致）。"""
    _verify_cache.pop(platform, None)
