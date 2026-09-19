"""小红书站内接口采集单元测试：签名客户端 / 分页 / 详情解析 / 回退策略。

覆盖 scripts/scraper_xhs_api.py（站内接口路径）。本文件**不发真实请求**：
HTTP 层用假响应替换，只验证解析与聚合逻辑本身。

其中「真实响应形态」一组用例取自 2026-09 真机实测抓包（博主主页 → user_posted
→ 笔记详情页 HTML），用来锁死已验证的接口契约，避免改动时静默失效。
"""

import json

import httpx
import pytest

from scripts import scraper_common as sc
from scripts import scraper_xhs_api as api

requires_xhshow = pytest.mark.skipif(
    not api.xhs_api_available(),
    reason="未安装 xhshow（接口路径在未安装环境自动回退 Playwright）",
)


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    """打桩翻页间隔，保证用例瞬间完成且行为确定。"""
    monkeypatch.setattr(api, "_rdsleep", lambda *a, **k: None)


# ═══════════════════════════════════════════════════════════════
#  实测样本（2026-09 真机抓包）
# ═══════════════════════════════════════════════════════════════

"""实测 urlDefault：网页图形态（`/{日期}/{hash}/notes_pre_post/{fileId}!nd_...`）。"""
REAL_URL_DEFAULT = (
    "http://sns-webpic-qc.xhscdn.com/202609191827/"
    "a59c0e4304157458cbe212054a4460f9/notes_pre_post/"
    "1040g3k0324vtb63m2s105pkv2u2p3h2s81r3118!nd_dft_wlteh_jpg_3"
)

"""实测 noteDetailMap 里的 note 节点（字段已按真实响应裁剪）。"""
REAL_NOTE = {
    "noteId": "6aa4118000000000120248ca",
    "type": "normal",
    "title": "今日",
    "desc": "#ootd[话题]#\n#穿搭技巧[话题]#",
    "tagList": [
        {"name": "ootd", "type": "topic"},
        {"name": "穿搭技巧", "type": "topic"},
    ],
    "xsecToken": "ABgQ5S05kOYmmkzHGIGD9AAyd9I0ZTe9xK_qnRQm",
    "user": {"nickname": "星期八不早8"},
    "imageList": [
        {"urlDefault": REAL_URL_DEFAULT, "urlPre": "http://x/prev.jpg", "width": 1080},
        {"urlDefault": "http://sns-webpic-qc.xhscdn.com/202609191827/deadbeef/notes_pre_post/1040g3second!nd_dft_wlteh_jpg_3"},
    ],
}

"""实测 user_posted 响应（字段已按真实响应裁剪）。"""
REAL_USER_POSTED = {
    "success": True,
    "code": 0,
    "msg": "成功",
    "data": {
        "notes": [
            {
                "note_id": "6aa4118000000000120248ca",
                "type": "normal",
                "display_title": "今日",
                "xsec_token": "ABgQ5S05kOYmmkzHGIGD9AAyd9I0ZTe9xK_qnRQm",
                "time": 1789813642378,
            },
            {"note_id": "6aa2b9320000000028003dbd", "type": "video"},
        ],
        "cursor": "CURSOR_2",
        "has_more": True,
    },
}


def _html_with_state(state: dict, tail: str = "</script></body>") -> str:
    """构造含 __INITIAL_STATE__ 的详情页 HTML。"""
    return (
        "<html><head><script>window.__INITIAL_STATE__="
        + json.dumps(state, ensure_ascii=False)
        + tail
        + "</html>"
    )


def _detail_html(note: dict = None, extra_after: str = "") -> str:
    """构造笔记详情页 HTML：noteDetailMap 后可按需追加兄弟节点（验证配平扫描）。"""
    note_map = {"6aa4118000000000120248ca": {"note": note or REAL_NOTE}}
    state = {"global": {"a": 1}, "note": {"noteDetailMap": note_map, "other": {"b": 2}}}
    return _html_with_state(state, tail=extra_after + "</script></body>")


def _resp(html: str, url: str = "https://www.xiaohongshu.com/explore/abc") -> httpx.Response:
    """假 httpx 响应（带 request 才能取到 resp.url）。"""
    return httpx.Response(200, text=html, request=httpx.Request("GET", url))


# ═══════════════════════════════════════════════════════════════
#  user_id_of
# ═══════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://www.xiaohongshu.com/user/profile/669f1785000000002401c45c",
         "669f1785000000002401c45c"),
        # 主页链接常带 xsec_token，必须去 query 后解析
        ("https://www.xiaohongshu.com/user/profile/669f1785000000002401c45c"
         "?xsec_token=ABQR7PuX&xsec_source=pc_search",
         "669f1785000000002401c45c"),
        ("/user/profile/5e3a2b1c9f4d8e7a", "5e3a2b1c9f4d8e7a"),
        # 字符集与 blogger_enrichment_service.PROFILE_ID_RE 同口径：`-`/`_` 也合法，
        # 不能因为用了 str.isalnum() 就静默回退 Playwright
        ("/user/profile/uid-with_dash", "uid-with_dash"),
        # 非主页链接必须返回空串：否则会把笔记 ID 当 uid 传给接口
        ("https://www.xiaohongshu.com/explore/669f1785000000002401c45c", ""),
        ("https://www.xiaohongshu.com/search_result?keyword=穿搭", ""),
        ("", ""),
    ],
)
def test_user_id_of(url, expected):
    assert api.user_id_of(url) == expected


# ═══════════════════════════════════════════════════════════════
#  cookie_dict
# ═══════════════════════════════════════════════════════════════


def test_cookie_dict_accepts_three_shapes():
    """三种实际形态都要能归一。"""
    flat = api.cookie_dict({"a1": "v1", "web_session": "v2"})
    assert flat == {"a1": "v1", "web_session": "v2"}

    # run_scraper 从 Playwright context 取到的形态：{name: cookie_dict}
    from_context = api.cookie_dict(
        {
            "a1": {"name": "a1", "value": "v1", "domain": ".xiaohongshu.com"},
            "web_session": {
                "name": "web_session",
                "value": "v2",
                "domain": ".xiaohongshu.com",
            },
        }
    )
    assert from_context == {"a1": "v1", "web_session": "v2"}

    # storage/cookies/*.json 的导出形态：[{name, value, domain}]
    from_file = api.cookie_dict(
        [
            {"name": "a1", "value": "v1", "domain": ".xiaohongshu.com"},
            {"name": "web_session", "value": "v2", "domain": ".xiaohongshu.com"},
        ]
    )
    assert from_file == {"a1": "v1", "web_session": "v2"}


def test_cookie_dict_filters_other_domains():
    """浏览器上下文含全站 Cookie：非小红书域名必须剔除，否则签名头站点对不上。"""
    cookies = api.cookie_dict(
        [
            {"name": "a1", "value": "v1", "domain": ".xiaohongshu.com"},
            {"name": "sessionid", "value": "dy", "domain": ".douyin.com"},
        ]
    )
    assert cookies == {"a1": "v1"}


# ═══════════════════════════════════════════════════════════════
#  find_balanced_object
# ═══════════════════════════════════════════════════════════════


def test_find_balanced_object_handles_nesting():
    """嵌套对象不能被截断（这是不用非贪婪正则的原因）。"""
    text = 'x={"a":{"b":{"c":1}},"d":2} tail'
    assert api.find_balanced_object(text, text.index("{")) == '{"a":{"b":{"c":1}},"d":2}'


def test_find_balanced_object_ignores_braces_in_strings():
    """字符串里的花括号与转义引号不得影响配平。"""
    text = 'x={"a":"} { \\" {","b":1} tail'
    assert api.find_balanced_object(text, text.index("{")) == '{"a":"} { \\" {","b":1}'


def test_find_balanced_object_raises_when_unclosed():
    with pytest.raises(ValueError):
        api.find_balanced_object('x={"a":1', 2)


# ═══════════════════════════════════════════════════════════════
#  extract_note_detail_state
# ═══════════════════════════════════════════════════════════════


def test_extract_note_detail_state_real_shape():
    """实测形态：noteDetailMap 后仍有兄弟节点，配平扫描不得越界。"""
    state = api.extract_note_detail_state(_detail_html())
    assert list(state) == ["6aa4118000000000120248ca"]
    assert state["6aa4118000000000120248ca"]["note"]["title"] == "今日"


def test_extract_note_detail_state_missing_returns_empty():
    assert api.extract_note_detail_state("<html>没有状态</html>") == {}
    assert api.extract_note_detail_state("") == {}


def test_extract_note_detail_state_survives_outer_undefined():
    """外层含 JS 字面量 undefined 时，只切子树仍应成功（不做字符串替换）。"""
    html = (
        "<script>window.__INITIAL_STATE__="
        '{"global":{"x":undefined},"note":{"noteDetailMap":'
        '{"n1":{"note":{"title":"t"}}}}}</script>'
    )
    state = api.extract_note_detail_state(html)
    assert state["n1"]["note"]["title"] == "t"


def test_first_note_of_falls_back_to_flat_node():
    """noteDetailMap 的 value 可能是 note 本身而非 {note: ...}。"""
    assert api.first_note_of({"n1": {"title": "扁平"}}) == {"title": "扁平"}
    assert api.first_note_of({}) == {}


# ═══════════════════════════════════════════════════════════════
#  parse_user_posted / note_url_of
# ═══════════════════════════════════════════════════════════════


def test_parse_user_posted_real_shape():
    notes, cursor, has_more = api.parse_user_posted(REAL_USER_POSTED)
    assert [n["note_id"] for n in notes] == [
        "6aa4118000000000120248ca",
        "6aa2b9320000000028003dbd",
    ]
    assert cursor == "CURSOR_2"
    assert has_more is True


@pytest.mark.parametrize("payload", [{}, {"data": None}, {"data": []}, None])
def test_parse_user_posted_malformed(payload):
    assert api.parse_user_posted(payload) == ([], "", False)


def test_note_url_of_has_no_token():
    """落库用的 source_url 不能带 xsec_token（token 会过期）。"""
    url = api.note_url_of({"note_id": "abc", "xsec_token": "T0KEN"})
    assert url == "https://www.xiaohongshu.com/explore/abc"
    assert "xsec_token" not in url
    assert api.note_url_of({}) == ""


# ═══════════════════════════════════════════════════════════════
#  note_state_to_detail
# ═══════════════════════════════════════════════════════════════


def test_note_state_to_detail_real_shape():
    detail = api.note_state_to_detail(REAL_NOTE)
    assert detail["img_urls"] == [
        REAL_URL_DEFAULT,
        REAL_NOTE["imageList"][1]["urlDefault"],
    ]
    assert detail["caption"].startswith("#ootd")
    assert detail["tags"] == ["ootd", "穿搭技巧"]
    assert detail["video_urls"] == []


def test_note_state_to_detail_title_fallback_and_dedup():
    """无 desc 时回落 title；同一图片重复出现只保留一次。"""
    note = {
        "title": "只有标题",
        "imageList": [{"urlDefault": REAL_URL_DEFAULT}, {"urlDefault": REAL_URL_DEFAULT}],
    }
    detail = api.note_state_to_detail(note)
    assert detail["caption"] == "只有标题"
    assert detail["img_urls"] == [REAL_URL_DEFAULT]


def test_note_state_to_detail_video_watermark_free_first():
    """视频：originVideoKey 拼无水印直链且排在 masterUrl 之前。"""
    note = {
        "imageList": [],
        "video": {
            "consumer": {"originVideoKey": "v0300fabc/origin.mp4"},
            "media": {"stream": {"h264": [{"masterUrl": "https://wm.example/a.mp4"}]}},
        },
    }
    detail = api.note_state_to_detail(note)
    assert detail["video_urls"] == [
        f"{sc.XHS_VIDEO_HOST}/v0300fabc/origin.mp4",
        "https://wm.example/a.mp4",
    ]


def test_note_state_to_detail_empty_note():
    assert api.note_state_to_detail({}) == {
        "img_urls": [],
        "video_urls": [],
        "caption": "",
        "tags": [],
    }


def test_detail_json_shape_matches_dom_extractor():
    """两个来源（接口 / DOM）必须同形：run_blogger_mode 下游才能共用。"""
    detail = api.note_state_to_detail(REAL_NOTE)
    for key in ("img_urls", "video_urls", "caption", "tags"):
        assert key in detail


# ── 跨模块回归：接口给的 urlDefault 必须能展开成「带前缀的原图直链」──


def test_api_url_default_expands_to_original_with_path_prefix():
    """回归：网页图 urlDefault → 原图直链必须保留 notes_pre_post/ 前缀。

    此前 trace_id 只取最后一段，四个原图 CDN 全部 404，候选链静默回落到末尾
    兜底的网页压缩图（实测 206KB vs 原图 479KB）。
    """
    detail = api.note_state_to_detail(REAL_NOTE)
    candidates = sc.xhs_image_url_candidates(detail["img_urls"][0])
    assert candidates[0] == (
        "https://sns-img-qc.xhscdn.com/notes_pre_post/"
        "1040g3k0324vtb63m2s105pkv2u2p3h2s81r3118"
    )
    assert candidates[-1] == REAL_URL_DEFAULT  # 原 URL 兜底仍在


# ═══════════════════════════════════════════════════════════════
#  失败分类
# ═══════════════════════════════════════════════════════════════


def test_raise_for_payload_fatal_maps_to_scraper_blocked():
    with pytest.raises(sc.ScraperBlockedError) as ei:
        api._raise_for_payload({"success": False, "msg": "请先登录"}, "/x")
    assert ei.value.kind == "login_wall"
    assert ei.value.is_fatal is True


def test_raise_for_payload_not_found_is_not_fatal():
    """笔记删除属非致命：应跳过本篇继续，而不是停止整轮。"""
    with pytest.raises(sc.ScraperBlockedError) as ei:
        api._raise_for_payload({"success": False, "msg": "笔记不存在"}, "/x")
    assert ei.value.kind == "not_found"
    assert ei.value.is_fatal is False


def test_raise_for_payload_generic_failure_is_api_error():
    """无法归类的失败 → XhsApiError（触发回退 Playwright，而非停整轮）。"""
    with pytest.raises(api.XhsApiError):
        api._raise_for_payload({"success": False, "code": -1}, "/x")


def test_raise_for_payload_success_passthrough():
    assert api._raise_for_payload({"success": True}, "/x") is None
    assert api._raise_for_payload(None, "/x") is None


@pytest.mark.parametrize(
    "status, kind",
    [(401, "login_wall"), (403, "login_wall"), (429, "rate_limit"), (461, "login_wall")],
)
def test_raise_for_status_fatal(status, kind):
    resp = httpx.Response(status, request=httpx.Request("GET", "https://x/y"))
    with pytest.raises(sc.ScraperBlockedError) as ei:
        api._raise_for_status(resp, "/y")
    assert ei.value.kind == kind
    assert ei.value.is_fatal is True


def test_raise_for_status_406_is_api_error():
    """406（签名失配）是通用失败：应回退而不是停整轮。"""
    resp = httpx.Response(406, request=httpx.Request("GET", "https://x/y"))
    with pytest.raises(api.XhsApiError):
        api._raise_for_status(resp, "/y")


# ═══════════════════════════════════════════════════════════════
#  XhsApiScraper：分页与详情
# ═══════════════════════════════════════════════════════════════


def _make_scraper() -> api.XhsApiScraper:
    return api.XhsApiScraper(cookies={"a1": "v1", "web_session": "v2"})


@requires_xhshow
def test_scraper_rejects_empty_cookies():
    with pytest.raises(api.XhsApiError):
        api.XhsApiScraper(cookies={})


@requires_xhshow
def test_blogger_notes_paginates_and_stops_at_max():
    """翻页推进 + 上限截断（第二页只取所需条数）。"""
    scraper = _make_scraper()
    params_seen: list[dict] = []
    pages = [
        {
            "success": True,
            "data": {
                "notes": [{"note_id": f"n{i}"} for i in range(2)],
                "cursor": "C1",
                "has_more": True,
            },
        },
        {
            "success": True,
            "data": {
                "notes": [{"note_id": f"m{i}"} for i in range(2)],
                "cursor": "C2",
                "has_more": False,
            },
        },
    ]

    def fake_get(path, params, referer):
        params_seen.append(params)
        return pages[len(params_seen) - 1]

    scraper._signed_get = fake_get
    notes = scraper.blogger_notes("uid1", max_notes=3)
    assert [n["note_id"] for n in notes] == ["n0", "n1", "m0"]
    assert params_seen[0]["cursor"] == ""
    assert params_seen[1]["cursor"] == "C1"
    assert params_seen[1]["num"] == "1"  # 只差 1 条 → 只要 1 条
    scraper.close()


@requires_xhshow
def test_blogger_notes_dedupes_by_note_id():
    """接口跨页重复返回同一篇：不能重复下载。"""
    scraper = _make_scraper()
    pages = [
        {
            "success": True,
            "data": {
                "notes": [{"note_id": "n0"}, {"note_id": "n1"}],
                "cursor": "C1",
                "has_more": True,
            },
        },
        {
            "success": True,
            "data": {
                "notes": [{"note_id": "n1"}, {"note_id": "n2"}],
                "cursor": "C2",
                "has_more": False,
            },
        },
    ]
    calls: list[dict] = []

    def fake_get(path, params, referer):
        calls.append(params)
        return pages[len(calls) - 1]

    scraper._signed_get = fake_get
    notes = scraper.blogger_notes("uid1", max_notes=10)
    assert [n["note_id"] for n in notes] == ["n0", "n1", "n2"]
    scraper.close()


@requires_xhshow
def test_blogger_notes_stops_on_repeated_cursor():
    """游标不前进即止：否则会一直回同一页，空转打满风控。"""
    scraper = _make_scraper()
    calls: list[dict] = []

    def fake_get(path, params, referer):
        calls.append(params)
        return {
            "success": True,
            "data": {"notes": [{"note_id": "n0"}], "cursor": "SAME", "has_more": True},
        }

    scraper._signed_get = fake_get
    notes = scraper.blogger_notes("uid1", max_notes=100)
    assert len(notes) == 1
    assert len(calls) <= 2  # 第一页 + 重复游标那页，之后必须停
    scraper.close()


@requires_xhshow
def test_note_detail_parses_html():
    scraper = _make_scraper()
    scraper._request = lambda *a, **k: _resp(_detail_html())
    detail = scraper.note_detail("6aa4118000000000120248ca", "TOK")
    assert len(detail["img_urls"]) == 2
    assert detail["tags"] == ["ootd", "穿搭技巧"]
    scraper.close()


@requires_xhshow
def test_note_detail_redirect_off_explore_is_not_found():
    """token 失效/笔记删除会被重定向到错误页：非致命，跳过本篇。"""
    scraper = _make_scraper()
    scraper._request = lambda *a, **k: _resp(
        "<html></html>", url="https://www.xiaohongshu.com/404?error=1"
    )
    with pytest.raises(sc.ScraperBlockedError) as ei:
        scraper.note_detail("gone", "BAD")
    assert ei.value.kind == "not_found"
    assert ei.value.is_fatal is False
    scraper.close()


@requires_xhshow
def test_note_detail_missing_state_is_api_error():
    """结构变化（取不到 noteDetailMap）→ 通用失败，让调用方回退。"""
    scraper = _make_scraper()
    scraper._request = lambda *a, **k: _resp("<html>空的</html>")
    with pytest.raises(api.XhsApiError):
        scraper.note_detail("n1", "TOK")
    scraper.close()


# ═══════════════════════════════════════════════════════════════
#  try_fetch_blogger_notes：回退策略
# ═══════════════════════════════════════════════════════════════


def test_try_fetch_returns_none_when_xhshow_missing(monkeypatch):
    """未安装 xhshow：直接回退，不抛异常（改造不得让能力劣于改造前）。"""
    monkeypatch.setattr(api, "_HAS_XHSHOW", False)
    assert (
        api.try_fetch_blogger_notes(
            "https://www.xiaohongshu.com/user/profile/uid1", "", {"a1": "v"}, 10
        )
        is None
    )


def test_try_fetch_returns_none_without_user_id():
    assert api.try_fetch_blogger_notes("https://www.xiaohongshu.com/explore/x", "", {}, 10) is None


@requires_xhshow
def test_try_fetch_falls_back_on_generic_failure(monkeypatch):
    """通用失败（网络/签名/结构变化）→ 返回 None 回退 Playwright。"""

    def boom(self, user_id, max_notes, delay=1.0):
        raise api.XhsApiError("签名失配")

    monkeypatch.setattr(api.XhsApiScraper, "blogger_notes", boom)
    assert (
        api.try_fetch_blogger_notes(
            "https://www.xiaohongshu.com/user/profile/uid1", "", {"a1": "v"}, 10
        )
        is None
    )


@requires_xhshow
def test_try_fetch_reraises_fatal_block(monkeypatch):
    """致命风控必须抛出：回退 Playwright 只会在已被限流的账号上继续加压。"""

    def boom(self, user_id, max_notes, delay=1.0):
        raise sc.ScraperBlockedError("rate_limit", "访问频繁")

    monkeypatch.setattr(api.XhsApiScraper, "blogger_notes", boom)
    with pytest.raises(sc.ScraperBlockedError) as ei:
        api.try_fetch_blogger_notes(
            "https://www.xiaohongshu.com/user/profile/uid1", "", {"a1": "v"}, 10
        )
    assert ei.value.is_fatal is True


@requires_xhshow
def test_try_fetch_falls_back_on_non_fatal_block(monkeypatch):
    """非致命类型（not_found）必须回退而不是抛出。

    回归：not_found 在 scraper_common 里的语义是「跳过当前条目」，此前与致命风控
    一起 re-raise，会被 run_scraper 的兜底 except 当成整轮失败（原因写成
    「笔记不存在或已被删除」），把可跳过的状态升级成任务失败。
    """
    closed: list[bool] = []

    def boom(self, user_id, max_notes, delay=1.0):
        raise sc.ScraperBlockedError("not_found", "内容不存在")

    monkeypatch.setattr(api.XhsApiScraper, "blogger_notes", boom)
    monkeypatch.setattr(api.XhsApiScraper, "close", lambda self: closed.append(True))
    result = api.try_fetch_blogger_notes(
        "https://www.xiaohongshu.com/user/profile/uid1", "", {"a1": "v"}, 10
    )
    assert result is None
    assert closed == [True]  # 回退路径同样要释放连接池


@requires_xhshow
def test_try_fetch_uses_platform_user_id_fallback(monkeypatch):
    """profile_url 解析不出 uid 时用 platform_user_id 兜底。"""
    seen: list[str] = []

    def fake_notes(self, user_id, max_notes, delay=1.0):
        seen.append(user_id)
        return [{"note_id": "n0"}]

    monkeypatch.setattr(api.XhsApiScraper, "blogger_notes", fake_notes)
    result = api.try_fetch_blogger_notes(
        "https://www.xiaohongshu.com/explore/x", "669f1785000000002401c45c", {"a1": "v"}, 10
    )
    assert result is not None
    scraper, notes = result
    assert seen == ["669f1785000000002401c45c"]
    assert notes == [{"note_id": "n0"}]
    scraper.close()


@requires_xhshow
def test_try_fetch_returns_none_when_no_notes(monkeypatch):
    """接口返回空列表 → 回退（可能真的是新号，也可能是被静默风控成空）。"""
    monkeypatch.setattr(
        api.XhsApiScraper, "blogger_notes", lambda self, u, m, delay=1.0: []
    )
    assert (
        api.try_fetch_blogger_notes(
            "https://www.xiaohongshu.com/user/profile/uid1", "", {"a1": "v"}, 10
        )
        is None
    )
