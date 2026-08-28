"""抖音采集单元测试：URL 解析 / 媒体归一化 / RENDER_DATA 提取 / 请求头与登录检测。

采集脚本族已拆分为 scripts/ 包内模块（scraper_common / scraper_douyin 等），
直接以包路径导入。本文件不启动浏览器、不发真实请求，仅验证纯函数与轻量
解析逻辑。
"""

import json
import urllib.parse

from scripts import scraper_common as sc
from scripts import scraper_douyin as sd


# ── 作品 ID 解析 ──


def test_parse_aweme_id_video_url():
    assert sd._parse_douyin_aweme_id("https://www.douyin.com/video/730123") == "730123"


def test_parse_aweme_id_note_url_and_query():
    u = "https://www.douyin.com/note/7456789988776655?previous_item_id=1&x=2"
    assert sd._parse_douyin_aweme_id(u) == "7456789988776655"


def test_parse_aweme_id_trailing_slash():
    assert sd._parse_douyin_aweme_id("https://www.douyin.com/video/900/") == "900"


def test_parse_aweme_id_share_short_link_unsupported():
    # 分享短链无法客户端解出 ID，返回 None 由调用方跳过
    assert sd._parse_douyin_aweme_id("https://v.douyin.com/iAbCdEf/") is None


def test_parse_aweme_id_empty():
    assert sd._parse_douyin_aweme_id("") is None


# ── 规范详情页 URL ──


def test_canonical_url_strips_query_params():
    href = "//www.douyin.com/video/111222?previous_item_id=abc&utm=1"
    assert sd._canonical_douyin_url(href) == "https://www.douyin.com/video/111222"


def test_canonical_url_note_kind():
    href = "https://www.douyin.com/note/333444?q=1"
    assert sd._canonical_douyin_url(href) == "https://www.douyin.com/note/333444"


def test_canonical_url_non_douyin_domain_rejected():
    assert sd._canonical_douyin_url("https://example.com/video/555") is None


def test_canonical_url_blob_and_empty_rejected():
    assert sd._canonical_douyin_url("blob:https://www.douyin.com/x") is None
    assert sd._canonical_douyin_url("") is None
    assert sd._canonical_douyin_url("https://www.douyin.com/search?q=a") is None


# ── 媒体 URL 归一化 ──


def test_normalize_media_protocol_relative():
    assert sd._normalize_douyin_media_url("//p3.douyinpic.com/a.jpg") == \
        "https://p3.douyinpic.com/a.jpg"


def test_normalize_media_path_gets_host_prefix():
    u = "/aweme/v1/play/?video_id=xyz"
    assert sd._normalize_douyin_media_url(u) == f"https://www.douyin.com{u}"


def test_normalize_media_blob_rejected():
    assert sd._normalize_douyin_media_url("blob:https://www.douyin.com/v") is None


def test_normalize_media_plain_http_kept():
    assert sd._normalize_douyin_media_url("https://v.douyinvod.com/x.mp4") == \
        "https://v.douyinvod.com/x.mp4"


def test_normalize_media_empty():
    assert sd._normalize_douyin_media_url("") is None


# ── 下载请求头平台化（此前硬编码小红书 Referer 的回归测试）──


def test_download_headers_referer_per_platform():
    h_dy = sc.build_download_headers("douyin")
    h_xhs = sc.build_download_headers("xiaohongshu")
    assert h_dy["Referer"] == "https://www.douyin.com/"
    assert h_xhs["Referer"] == "https://www.xiaohongshu.com/"
    assert "Cookie" not in h_dy


def test_download_headers_cookie_assembled():
    cookies = {"SESSDATA": {"name": "SESSDATA", "value": "abc"},
               "ttwid": {"name": "ttwid", "value": "xyz"}}
    h = sc.build_download_headers("douyin", cookies)
    cookie_val = h["Cookie"]
    assert "SESSDATA=abc" in cookie_val and "ttwid=xyz" in cookie_val


def test_video_size_limit_by_platform():
    assert sc.MAX_VIDEO_BYTES["douyin"] == 50 * 1024 * 1024
    assert sc.MAX_VIDEO_BYTES["xiaohongshu"] == 100 * 1024 * 1024


# ── 登录检测 ──


def test_platform_has_login_douyin_sessionid():
    # 抖音网页版真实登录态是 sessionid 系列（SESSDATA 是 B 站的）
    cookies = [{"name": "sessionid", "domain": ".douyin.com"},
               {"name": "ttwid", "domain": ".douyin.com"}]
    assert sc.platform_has_login(cookies, "douyin") is True
    cookies_ss = [{"name": "sessionid_ss", "domain": ".douyin.com"}]
    assert sc.platform_has_login(cookies_ss, "douyin") is True


def test_platform_has_login_douyin_sessdata_not_a_login_cookie():
    # 回归：SESSDATA 是 B 站 Cookie 名，不得再作为抖音登录判据
    # （曾导致已登录也被判未登录 → 每次任务空等 180s）
    cookies = [{"name": "SESSDATA", "domain": ".douyin.com"}]
    assert sc.platform_has_login(cookies, "douyin") is False


def test_platform_has_login_xhs_web_session():
    cookies = [{"name": "web_session", "domain": ".xiaohongshu.com"}]
    assert sc.platform_has_login(cookies, "xiaohongshu") is True


def test_platform_has_login_wrong_domain_name_not_counted():
    # Cookie 名命中但域名不属于该平台 → 未登录
    cookies = [{"name": "sessionid", "domain": ".example.com"}]
    assert sc.platform_has_login(cookies, "douyin") is False
    # 有平台域名 Cookie 但没有会话名 → 未登录
    cookies2 = [{"name": "ttwid", "domain": ".douyin.com"}]
    assert sc.platform_has_login(cookies2, "douyin") is False


def test_platform_has_login_no_cookies():
    assert sc.platform_has_login([], "douyin") is False


# ── RENDER_DATA 解析 ──


class _FakePage:
    """仅实现 evaluate 的假页面：模拟 RENDER_DATA 文本节点读取。"""

    def __init__(self, evaluate_return: str):
        self._ret = evaluate_return

    def evaluate(self, *_args, **_kwargs):
        return self._ret


def _render_raw(payload: dict) -> str:
    return urllib.parse.quote(json.dumps(payload, ensure_ascii=False))


def test_render_data_extracts_full_detail():
    data = {"anyContainer": {"loaderData": {"postLoader": {
        "videoData": {
            "desc": "#穿搭分享 今日 OOTD",
            "video": {
                "origin_cover": {"url_list": ["https://p.example/oc.jpg"]},
                "cover": {"url_list": ["//p.example/c.jpg"]},
                "play_addr": {"url_list": ["/aweme/v1/play/?video_id=9"]},
            },
            "images": [
                {"url_list": ["https://img.example/1.jpg", "https://img.example/2.jpg"]},
            ],
        }}}}}
    out = sd._extract_douyin_render_data(_FakePage(_render_raw(data)))
    assert out["caption"] == "#穿搭分享 今日 OOTD"
    assert any(u.endswith("/aweme/v1/play/?video_id=9") and u.startswith("https://www.douyin.com")
               for u in out["video_urls"])
    assert "https://img.example/1.jpg" in out["img_urls"]
    assert "https://p.example/oc.jpg" in out["img_urls"]
    # 协议相对封面已补全协议头
    assert "https://p.example/c.jpg" in out["img_urls"]


def test_render_data_empty_page_returns_empty():
    out = sd._extract_douyin_render_data(_FakePage(""))
    assert out["img_urls"] == [] and out["video_urls"] == [] and out["caption"] == ""


def test_render_data_broken_json_is_safe():
    out = sd._extract_douyin_render_data(_FakePage("%%%not-json%%%"))
    assert out["caption"] == ""


def test_render_data_without_aweme_node_returns_empty():
    out = sd._extract_douyin_render_data(_FakePage(_render_raw({"foo": {"bar": 1}})))
    assert out["caption"] == ""


def test_find_aweme_walker_via_list():
    deep = [{"items": {"candidate": "no"}, "x": [{"videoData": {"desc": "d", "video": {}}}]}]
    hit = sd._find_douyin_aweme_data(deep)
    assert hit is not None and hit["desc"] == "d"


def test_find_aweme_walker_depth_guard():
    node: dict = {"leaf": {"desc": "deep"}}
    for i in range(16):
        node = {f"k{i}": node}
    assert sd._find_douyin_aweme_data(node) is None


# ── 抖音 DOM 选择器常量完备性（防手滑改空导致全链路失效）──


def test_selector_constants_nonempty():
    for const in (
        sc.DOUYIN_DETAIL_ANCHOR,
        sc.DOUYIN_SLIDE_IMG_SELECTOR,
        sc.DOUYIN_DETAIL_READY,
        sc.DOUYIN_HASHTAG_ANCHOR,
    ):
        assert isinstance(const, str) and const.strip()
    assert all(sc.DOUYIN_DESC_SELECTORS)


# ── 机器人验证检测与人工等待 ──


class _VerifyFakePage:
    """可切换验证态的假页面：query_selector 按验证开关返回命中。"""

    def __init__(self, captcha: bool = True) -> None:
        self.captcha = captcha

    def goto(self, *_args, **_kwargs) -> None:
        pass

    def wait_for_selector(self, *_args, **_kwargs) -> None:
        pass

    def evaluate(self, *_args, **_kwargs) -> None:
        pass

    def query_selector(self, _sel: str):
        return object() if self.captcha else None

    def query_selector_all(self, _sel: str):
        return []  # 验证页/空页都没有作品卡片


def test_is_verify_page_detects_captcha():
    assert sd._is_verify_page(_VerifyFakePage(captcha=True)) is True
    assert sd._is_verify_page(_VerifyFakePage(captcha=False)) is False


def test_wait_verify_resolved_returns_when_solved(monkeypatch):
    monkeypatch.setattr(sd.time, "sleep", lambda *_: None)
    # 首次检查即通过 → False（已解决）
    assert sd._wait_verify_resolved(_VerifyFakePage(captcha=False)) is False


def test_wait_verify_resolved_timeout_returns_true(monkeypatch):
    monkeypatch.setattr(sd.time, "sleep", lambda *_: None)
    # 始终处于验证态 → 等满 timeout 返回 True（超时）
    assert sd._wait_verify_resolved(_VerifyFakePage(captcha=True)) is True


def test_collect_urls_verify_timeout_reports_error(monkeypatch):
    """搜索页被验证拦截且人工超时 → 漏斗 error 明确留痕（任务 #44 回归）。"""
    monkeypatch.setattr(sd.time, "sleep", lambda *_: None)
    monkeypatch.setattr(sd, "_wait_verify_resolved", lambda page, timeout=180: True)
    urls, funnel = sd.collect_douyin_detail_urls(_VerifyFakePage(), "https://x", 10)
    assert urls == []
    assert "机器人验证" in funnel.get("error", "")
    assert funnel["urls_extracted"] == 0


def test_collect_urls_verify_solved_resumes_scroll(monkeypatch):
    """人工完成验证后继续滚动收集（无 error，正常返回漏斗）。"""
    monkeypatch.setattr(sd.time, "sleep", lambda *_: None)
    monkeypatch.setattr(sd, "_wait_verify_resolved", lambda page, timeout=180: False)
    urls, funnel = sd.collect_douyin_detail_urls(_VerifyFakePage(), "https://x", 10)
    assert urls == []
    assert "error" not in funnel  # 解决后继续，不误报失败
