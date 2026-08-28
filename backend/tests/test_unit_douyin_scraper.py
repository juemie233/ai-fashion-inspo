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


# ── 详情页图片提取策略（任务 #47 回归：视频页全页兜底采回 69 张无关图）──


class _DetailFakePage:
    """详情页假页面：可配置图集容器/全页图片/视频播放器/渲染层。

    slide_imgs：命中图集容器选择器的图片元素；
    page_imgs：全页 img 兜底能看到的图片元素（含相关推荐封面等杂图）；
    has_video_el：页面是否有 <video> 播放器（模拟视频笔记）。
    """

    class _Img:
        def __init__(self, src: str) -> None:
            self._src = src

        def get_attribute(self, name: str):
            return self._src if name in ("src", "data-src") else None

    class _Video:
        def get_attribute(self, _name: str) -> str:
            return "blob:fake"  # blob 无法外下载，真实直链走渲染层

        def query_selector(self, _sel: str):
            return None

    def __init__(
        self,
        slide_imgs=(),
        page_imgs=(),
        has_video_el: bool = False,
        evaluate_return: str = "",
    ) -> None:
        self._slide = list(slide_imgs)
        self._imgs = list(page_imgs)
        self._video = has_video_el
        self._ret = evaluate_return

    def on(self, *_args, **_kwargs) -> None:
        pass

    def remove_listener(self, *_args, **_kwargs) -> None:
        pass

    def goto(self, *_args, **_kwargs) -> None:
        pass

    def wait_for_selector(self, *_args, **_kwargs) -> None:
        pass

    def evaluate(self, *_args, **_kwargs):
        return self._ret

    def query_selector(self, sel: str):
        if sel == "video":
            return self._Video() if self._video else None
        return None

    def query_selector_all(self, sel: str):
        if "slide" in sel:
            return self._slide
        if sel == "img":
            return self._imgs
        if sel == "video":
            return [self._Video()] if self._video else []
        return []


def _detail_monkeypatch(monkeypatch) -> None:
    """详情页提取的外部依赖全部打桩（导航/验证/停顿/拟人动作）。"""
    monkeypatch.setattr(sd, "goto_with_retry", lambda *_a, **_k: None)
    monkeypatch.setattr(sd, "_is_verify_page", lambda *_a: False)
    monkeypatch.setattr(sd, "_rdsleep", lambda *_a: None)
    monkeypatch.setattr(sd, "_human_mouse_move", lambda *_a: None)


def test_detail_video_note_skips_dom_image_fallback(monkeypatch):
    """视频笔记：图片只信 RENDER_DATA 封面，相关推荐封面不得混入。"""
    _detail_monkeypatch(monkeypatch)
    raw = _render_raw({"loaderData": {"postLoader": {"videoData": {
        "desc": "白色系穿搭",
        "video": {
            "origin_cover": {"url_list": ["https://p.example/cover.jpg"]},
            "play_addr": {"url_list": ["/aweme/v1/play/?video_id=1"]},
        },
    }}}})
    page = _DetailFakePage(
        page_imgs=[
            _DetailFakePage._Img(f"https://p9.douyinpic.com/recommend/{i}.jpg")
            for i in range(3)
        ],
        has_video_el=True,
        evaluate_return=raw,
    )
    out = sd._extract_douyin_detail(page, "https://www.douyin.com/video/1")
    assert out["img_urls"] == ["https://p.example/cover.jpg"]
    assert all("recommend" not in u for u in out["img_urls"])
    assert len(out["video_urls"]) == 1
    assert out["caption"] == "白色系穿搭"


def test_detail_slide_note_uses_container_only(monkeypatch):
    """图集笔记：只取图集容器内图片，页面其他图片不混入。"""
    _detail_monkeypatch(monkeypatch)
    slide = [
        _DetailFakePage._Img(f"https://img.example/slide{i}.jpg")
        for i in range(4)
    ]
    page = _DetailFakePage(
        slide_imgs=slide,
        page_imgs=[_DetailFakePage._Img("https://img.example/recommend.jpg")],
    )
    out = sd._extract_douyin_detail(page, "https://www.douyin.com/note/1")
    assert out["img_urls"] == [
        f"https://img.example/slide{i}.jpg" for i in range(4)
    ]


def test_detail_unknown_structure_keeps_fallback(monkeypatch):
    """非视频且无图集容器（结构未知）：保留全页过滤兜底。"""
    _detail_monkeypatch(monkeypatch)
    page = _DetailFakePage(
        page_imgs=[
            _DetailFakePage._Img(f"https://img.example/p{i}.jpg")
            for i in range(3)
        ],
    )
    out = sd._extract_douyin_detail(page, "https://www.douyin.com/video/2")
    assert out["img_urls"] == [
        f"https://img.example/p{i}.jpg" for i in range(3)
    ]


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
    """可切换验证态的假页面（模拟可见性语义）。

    mode: "visible"=有可见验证元素 / "hidden"=仅隐藏验证容器模板 /
    "none"=无任何验证元素。
    """

    class _El:
        def __init__(self, visible: bool) -> None:
            self._visible = visible

        def is_visible(self) -> bool:
            return self._visible

    def __init__(self, mode: str = "visible") -> None:
        self.mode = mode

    def goto(self, *_args, **_kwargs) -> None:
        pass

    def wait_for_selector(self, *_args, **_kwargs) -> None:
        pass

    def evaluate(self, *_args, **_kwargs) -> None:
        pass

    def query_selector(self, _sel: str):
        if self.mode == "visible":
            return self._El(True)
        if self.mode == "hidden":
            return self._El(False)
        return None

    def query_selector_all(self, _sel: str):
        return []  # 验证页/空页都没有作品卡片


def test_is_verify_page_detects_captcha():
    assert sd._is_verify_page(_VerifyFakePage(mode="visible")) is True
    assert sd._is_verify_page(_VerifyFakePage(mode="none")) is False


def test_is_verify_page_ignores_hidden_captcha_template():
    """回归：抖音预注入的隐藏验证容器不得判为验证态（曾误报空等 180s）。"""
    assert sd._is_verify_page(_VerifyFakePage(mode="hidden")) is False


def test_wait_verify_resolved_returns_when_solved(monkeypatch):
    monkeypatch.setattr(sd.time, "sleep", lambda *_: None)
    # 首次检查即通过 → False（已解决）
    assert sd._wait_verify_resolved(_VerifyFakePage(mode="none")) is False


def test_wait_verify_resolved_timeout_returns_true(monkeypatch):
    monkeypatch.setattr(sd.time, "sleep", lambda *_: None)
    # 始终处于验证态 → 等满 timeout 返回 True（超时）
    assert sd._wait_verify_resolved(_VerifyFakePage(mode="visible")) is True


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


# ── 搜索专用流程：首页搜索框 → 回车 → 精选搜索 ──


class _FakeSearchInput:
    def click(self) -> None:
        pass

    def fill(self, _value: str) -> None:
        pass

    def press(self, _key: str) -> None:
        pass


class _FakeLink:
    def __init__(self, href: str) -> None:
        self._href = href

    def get_attribute(self, _name: str):
        return self._href


class _FakeXhrResponse:
    """搜索接口响应假对象（供监听器捕获、按需取响应体）。"""

    def __init__(self, url: str, body: str) -> None:
        self.url = url
        self._body = body

    def text(self) -> str:
        return self._body


class _SearchFlowPage:
    """搜索全流程假页面（路由感知 + 接口响应捕获）。

    direct_renders: 直连 /search/ 路线是否渲染卡片（False 模拟空壳）；
    with_input: 首页是否有搜索框；
    render_after: 第 N 次锚点查询起返回链接（999 = 永不渲染）；
    xhr_payload: 非空时访问 /search/ 页触发 response 监听器（模拟
    搜索接口响应——DOM 空壳时接口层兜底的数据来源）；
    searchbox_used: 是否走到了搜索框路线（断言路线选择用）。
    """

    def __init__(
        self,
        with_input: bool = True,
        render_after: int = 1,
        total_links: int = 4,
        direct_renders: bool = True,
        xhr_payload: str | None = None,
    ) -> None:
        self.with_input = with_input
        self.render_after = render_after
        self.total_links = total_links
        self.direct_renders = direct_renders
        self.xhr_payload = xhr_payload
        self.searchbox_used = False
        self._rendered = 0
        self._polls = 0
        self._on_direct = False
        self._listener = None

    def on(self, _event: str, cb) -> None:
        self._listener = cb

    def remove_listener(self, *_args) -> None:
        self._listener = None

    def goto(self, url: str, *_args, **_kwargs) -> None:
        self._on_direct = "douyin.com/search/" in url
        if (
            self._listener is not None
            and self.xhr_payload is not None
            and "/search/" in url
        ):
            self._listener(
                _FakeXhrResponse(
                    "https://www.douyin.com/aweme/v1/web/general/"
                    "search/stream/?aid=6383",
                    self.xhr_payload,
                )
            )

    def wait_for_selector(self, *_args, **_kwargs) -> None:
        pass

    def evaluate(self, *_args, **_kwargs) -> None:
        pass

    def query_selector(self, sel: str):
        if "searchbar-input" in sel:
            self.searchbox_used = True
            return _FakeSearchInput() if self.with_input else None
        return None

    def query_selector_all(self, sel: str):
        if "video" not in sel:
            return []
        if self._on_direct and not self.direct_renders:
            return []
        self._polls += 1
        if self._polls < self.render_after:
            return []
        self._rendered = min(self.total_links, self._rendered + 2)
        return [
            _FakeLink(f"https://www.douyin.com/video/{i}")
            for i in range(self._rendered)
        ]


def test_collect_search_urls_direct_route(monkeypatch):
    """直连经典搜索页渲染 → 直接收集，不落搜索框兜底。"""
    monkeypatch.setattr(sd.time, "sleep", lambda *_: None)
    fake = _SearchFlowPage()
    urls, funnel = sd.collect_douyin_search_urls(fake, "小白裙穿搭", 10)
    assert len(urls) == 4
    assert all("/video/" in u for u in urls)
    assert funnel["cards_seen"] >= len(urls)
    assert funnel["urls_extracted"] == 4
    assert funnel.get("xhr_extracted", 0) == 0
    assert fake.searchbox_used is False
    assert "error" not in funnel


def test_collect_search_urls_via_searchbox(monkeypatch):
    """直连空壳 → 搜索框兜底路线 → 结果渲染 → 滚动收集。"""
    monkeypatch.setattr(sd.time, "sleep", lambda *_: None)
    fake = _SearchFlowPage(direct_renders=False)
    urls, funnel = sd.collect_douyin_search_urls(fake, "小白裙穿搭", 10)
    assert len(urls) == 4
    assert all("/video/" in u for u in urls)
    assert fake.searchbox_used is True
    assert funnel["urls_extracted"] == 4
    assert "error" not in funnel


def test_collect_search_urls_missing_input_reports_error(monkeypatch):
    """直连空壳 + 首页无搜索框 → 漏斗留痕（页面结构变化）。"""
    monkeypatch.setattr(sd.time, "sleep", lambda *_: None)
    urls, funnel = sd.collect_douyin_search_urls(
        _SearchFlowPage(direct_renders=False, with_input=False), "kw", 10
    )
    assert urls == []
    assert "搜索框" in funnel.get("error", "")


def test_collect_search_urls_render_timeout_reports_error(monkeypatch):
    """DOM 与接口均无数据 → 事实型漏斗报错，不误称验证。"""
    monkeypatch.setattr(sd.time, "sleep", lambda *_: None)
    urls, funnel = sd.collect_douyin_search_urls(
        _SearchFlowPage(direct_renders=False, render_after=999), "kw", 10
    )
    assert urls == []
    assert "未提取到作品链接" in funnel.get("error", "")
    assert "机器人验证" not in funnel.get("error", "")


def test_collect_search_urls_xhr_fallback(monkeypatch):
    """DOM 空壳但搜索接口有响应 → 解析 aweme_id 兜底（网络层）。"""
    monkeypatch.setattr(sd.time, "sleep", lambda *_: None)
    payload = json.dumps(
        {
            "data": [
                {"type": 1, "aweme_info": {"aweme_id": "111"}},
                {"type": 1, "aweme_info": {"aweme_id": "222"}},
                {"type": 2},  # 无 aweme_info 的条目跳过
            ]
        }
    )
    fake = _SearchFlowPage(direct_renders=False, xhr_payload=payload)
    urls, funnel = sd.collect_douyin_search_urls(fake, "kw", 10)
    assert urls == [
        "https://www.douyin.com/video/111",
        "https://www.douyin.com/video/222",
    ]
    assert funnel["xhr_extracted"] == 2
    assert funnel["urls_extracted"] == 2
    assert "error" not in funnel


# ── 素材入库 INSERT 与真实 schema 匹配（任务 #46 回归）──


def test_insert_inspiration_sql_matches_real_schema(tmp_path):
    """直写 sqlite 的素材 INSERT 必须能在 ORM 真实 schema 上执行。

    回归：图片路径 INSERT 漏写 updated_at 占位符（14 列 13 值），
    sqlite 报「13 values for 14 columns」，每张图下载后入库必败，
    整条抖音采集链路静默颗粒无收。用 ORM metadata 建表后实际执行
    两条 SQL，防列清单/占位符/参数再次漂移。
    """
    import sqlite3

    import app.models  # noqa: F401  # 确保全部模型注册进 metadata
    from app.database import Base
    from scripts import scraper_download as sdl
    from sqlalchemy import create_engine

    db_path = tmp_path / "t.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    engine.dispose()

    # 用 sqlite3 模块执行（与生产直写路径一致，? 占位符原生支持）
    now = "2026-08-28 12:00:00"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            sdl.INSERT_INSPIRATION_IMAGE_SQL,
            (
                "img-id", "scraper", "https://www.douyin.com/video/1",
                "images/2026-08-28/a.jpg", "image", "sha256-img",
                "caption", 1, now, now,
            ),
        )
        conn.execute(
            sdl.INSERT_INSPIRATION_VIDEO_SQL,
            (
                "vid-id", "scraper", "https://www.douyin.com/video/2",
                "videos/2026-08-28/a.mp4", "thumbs/2026-08-28/a.jpg",
                "video", "caption", 1, now, now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT updated_at, content_hash, thumbnail_path, rating, "
            "quality_status FROM inspirations WHERE id = 'img-id'"
        ).fetchone()
        assert row[0] == now
        assert row[1] == "sha256-img"
        assert row[2] is None
        assert row[3] == 0 and row[4] == "pending"
        row2 = conn.execute(
            "SELECT updated_at, content_hash, thumbnail_path, rating "
            "FROM inspirations WHERE id = 'vid-id'"
        ).fetchone()
        assert row2[0] == now
        assert row2[1] is None
        assert row2[2] == "thumbs/2026-08-28/a.jpg"
        assert row2[3] == 0
    finally:
        conn.close()


# ── download_batch 逐条提交行为（2026-08 抖音链路审查修复回归）──


class _FakeResp:
    """假 httpx 响应：只提供下载所需字段。"""

    def __init__(self, status_code: int = 200, content: bytes = b"fake-img"):
        self.status_code = status_code
        self.headers = {"content-type": "image/jpeg"}
        self.content = content


class _FakeHttpx:
    """按调用顺序返回预设结果（响应或异常），记录每次请求 URL。"""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        r = self.results[len(self.calls) - 1]
        if isinstance(r, Exception):
            raise r
        return r


def _make_download_env(tmp_path, monkeypatch):
    """建真实 ORM schema 的临时库，并指向 download_batch 的读库路径。"""
    import app.models  # noqa: F401  # 确保全部模型注册进 metadata
    from app.database import Base
    from sqlalchemy import create_engine

    from app.config import settings

    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    monkeypatch.setattr(settings, "storage_root", storage_root)
    db_path = storage_root.parent / "fashion_inspo.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    return db_path


def test_download_batch_partial_failure_keeps_previous(tmp_path, monkeypatch):
    """批内一张图失败：已入库的图不回滚、文件保留；失败图文件被清理。

    回归：此前「攒批 20 条一次提交」，批内任一张失败 rollback 会把之前
    已成功写入的图一并回滚（行丢失 + 文件残留成孤儿）。逐条提交后失败
    窗口只剩当前图，且其文件随行删除。
    """
    from scripts import scraper_download as sdl

    _make_download_env(tmp_path, monkeypatch)
    img_dir = tmp_path / "storage" / "images" / "2026-08"
    img_dir.mkdir(parents=True, exist_ok=True)

    urls = [
        ("https://www.douyin.com/video/1", "https://cdn/a.jpg"),
        ("https://www.douyin.com/video/2", "https://cdn/b.jpg"),
        ("https://www.douyin.com/video/3", "https://cdn/c.jpg"),
    ]
    # 第 2 张 3 次重试全部失败（结果序列按调用顺序：1 成功 + 3 失败 + 1 成功）
    httpx_mod = _FakeHttpx(
        [
            _FakeResp(content=b"aaa"),
            RuntimeError("网络抖动"),
            RuntimeError("网络抖动"),
            RuntimeError("网络抖动"),
            _FakeResp(content=b"ccc"),
        ]
    )
    monkeypatch.setattr(sdl.time, "sleep", lambda *a, **k: None)  # 加速重试退避

    added, sk_ex, sk_h, sk_n, sk_dup = sdl.download_batch(
        urls, task_id=1, existing_url_set=set(), remaining=10,
        img_dir=img_dir, today="2026-08", httpx_module=httpx_mod,
    )

    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "fashion_inspo.db"))
    try:
        rows = conn.execute(
            "SELECT source_url FROM inspirations ORDER BY source_url"
        ).fetchall()
    finally:
        conn.close()

    assert added == 2
    assert sk_n == 1  # 仅第 2 张计入网络失败
    assert [r[0] for r in rows] == ["https://www.douyin.com/video/1",
                                    "https://www.douyin.com/video/3"]
    # 磁盘：成功 2 张各留一个文件，失败图无残留（无孤儿文件）
    files = sorted(p.name for p in img_dir.iterdir())
    assert len(files) == 2


def test_download_batch_flushes_backfill_task(tmp_path, monkeypatch):
    """入库的素材在收尾时合并为一个向量回填任务入队（独立事务）。"""
    from scripts import scraper_download as sdl

    _make_download_env(tmp_path, monkeypatch)
    img_dir = tmp_path / "storage" / "images" / "2026-08"
    img_dir.mkdir(parents=True, exist_ok=True)

    urls = [
        ("https://www.douyin.com/video/1", "https://cdn/a.jpg"),
        ("https://www.douyin.com/video/2", "https://cdn/b.jpg"),
        ("https://www.douyin.com/video/3", "https://cdn/c.jpg"),
    ]
    httpx_mod = _FakeHttpx([_FakeResp(content=b"a"), _FakeResp(content=b"b"),
                            _FakeResp(content=b"c")])
    monkeypatch.setattr(sdl.time, "sleep", lambda *a, **k: None)

    added, *_ = sdl.download_batch(
        urls, task_id=1, existing_url_set=set(), remaining=10,
        img_dir=img_dir, today="2026-08", httpx_module=httpx_mod,
    )
    assert added == 3

    import json as _json
    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "fashion_inspo.db"))
    try:
        task = conn.execute(
            "SELECT type, total, result FROM task_queue "
            "WHERE type = 'vector_backfill'"
        ).fetchone()
    finally:
        conn.close()
    assert task is not None
    assert task[1] == 3
    ids = _json.loads(task[2])["inspiration_ids"]
    assert len(ids) == 3
