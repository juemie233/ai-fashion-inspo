"""小红书采集脚本单元测试：详情页提取 / 博主页收集 / 搜索提取 / 博主采集管线。

scripts/scraper_xhs.py 是小红书采集的核心业务（此前无任何测试覆盖）。
本文件不启动浏览器、不发真实请求：用假 page 对象模拟 Playwright DOM 接口，
并打桩全部停顿/拟人化动作，只验证提取与聚合逻辑本身。
"""

import pytest

from scripts import scraper_xhs as sx


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    """打桩全部随机停顿与拟人化动作，保证用例瞬间完成且行为确定。"""
    monkeypatch.setattr(sx, "_rdsleep", lambda *a, **k: None)
    monkeypatch.setattr(sx, "_human_mouse_move", lambda *a, **k: None)
    monkeypatch.setattr(sx, "human_scroll", lambda *a, **k: None)
    monkeypatch.setattr(sx.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(sx, "_HASHTAG_SAVED_COUNT", [0])


# ── 假 DOM 元素 ──


class _FakeImg:
    """img 元素：可配置 src / data-src / width / height。"""

    def __init__(self, src="", data_src=None, width=None, height=None):
        self._attrs = {
            "src": src,
            "data-src": data_src,
            "width": width,
            "height": height,
        }

    def get_attribute(self, name: str):
        return self._attrs.get(name)


class _FakeVideo:
    """video 元素：src / poster 属性 + 可选 <source> 子元素。"""

    def __init__(self, src="", poster="", source_src=None):
        self._src = src
        self._poster = poster
        self._source = _FakeImg(src=source_src) if source_src else None

    def get_attribute(self, name: str):
        return {"src": self._src, "poster": self._poster}.get(name)

    def query_selector(self, _sel: str):
        return self._source


class _FakeTextEl:
    def __init__(self, text: str):
        self._text = text

    def inner_text(self) -> str:
        return self._text


class _FakeLink:
    def __init__(self, href: str):
        self._href = href

    def get_attribute(self, _name: str):
        return self._href


# ── 详情页提取 ──


class _FakeDetailPage:
    """详情页假页面：swiper 轮播容器 / 全页图片 / 视频播放器 / 正文元素。"""

    def __init__(self, swiper_imgs=(), page_imgs=(), videos=(), caption="", goto_error=None):
        self._swiper = list(swiper_imgs)
        self._imgs = list(page_imgs)
        self._videos = list(videos)
        self._caption = caption
        self._goto_error = goto_error
        self.visited: list[str] = []

    def goto(self, url, **_kw):
        self.visited.append(url)
        if self._goto_error:
            raise self._goto_error

    def wait_for_selector(self, *_a, **_k):
        pass

    def query_selector(self, sel: str):
        if "note-content" in sel and self._caption:
            return _FakeTextEl(self._caption)
        return None

    def query_selector_all(self, sel: str):
        if "swiper" in sel:
            return self._swiper
        if sel == "img":
            return self._imgs
        if sel == "video":
            return self._videos
        return []


def test_extract_note_carousel_multi_img_dedup():
    """轮播图：容器内多图全收、去重、data-src 兜底。"""
    page = _FakeDetailPage(
        swiper_imgs=[
            _FakeImg("https://img.example/1.jpg"),
            _FakeImg("https://img.example/1.jpg"),  # 重复
            _FakeImg("", data_src="https://img.example/2.jpg"),  # data-src 兜底
            _FakeImg("https://img.example/3.jpg"),
        ]
    )
    out = sx.extract_note_detail(page, "https://www.xiaohongshu.com/explore/n1")
    assert out["img_urls"] == [
        "https://img.example/1.jpg",
        "https://img.example/2.jpg",
        "https://img.example/3.jpg",
    ]


def test_extract_note_skips_icon_and_non_http():
    """头像/图标类 URL 与非 http 源不混入内容图。"""
    page = _FakeDetailPage(
        swiper_imgs=[
            _FakeImg("https://img.example/avatar_1.jpg"),
            _FakeImg("https://img.example/icon.png"),
            _FakeImg("data:image/png;base64,xxx"),
            _FakeImg("https://img.example/real.jpg"),
        ]
    )
    out = sx.extract_note_detail(page, "https://www.xiaohongshu.com/explore/n1")
    assert out["img_urls"] == ["https://img.example/real.jpg"]


def test_extract_note_protocol_relative_img_completed():
    """协议相对 URL（//）补全 https 后保留。"""
    page = _FakeDetailPage(swiper_imgs=[_FakeImg("//ci.xiaohongshu.com/a.jpg")])
    out = sx.extract_note_detail(page, "https://www.xiaohongshu.com/explore/n1")
    assert out["img_urls"] == ["https://ci.xiaohongshu.com/a.jpg"]


def test_extract_note_video_src_source_and_poster():
    """视频：video src 直链 / <source> 兜底 / poster 封面并入图片列表。"""
    page = _FakeDetailPage(
        page_imgs=[],
        videos=[
            _FakeVideo(src="https://v.example/a.mp4", poster="//ci.example/p1.jpg"),
            _FakeVideo(src="", source_src="https://v.example/b.mp4"),
        ],
    )
    out = sx.extract_note_detail(page, "https://www.xiaohongshu.com/explore/n2")
    assert out["video_urls"] == ["https://v.example/a.mp4", "https://v.example/b.mp4"]
    assert out["img_urls"] == ["https://ci.example/p1.jpg"]


def test_extract_note_caption_and_tags():
    """正文：候选选择器命中后截取；#话题 从正文提取且过滤标点。"""
    page = _FakeDetailPage(caption="夏日通勤穿搭分享 #OOTD #白色系，好看！")
    out = sx.extract_note_detail(page, "https://www.xiaohongshu.com/explore/n3")
    assert out["caption"] == "夏日通勤穿搭分享 #OOTD #白色系，好看！"
    assert out["tags"] == ["OOTD", "白色系"]


def test_extract_note_no_caption_no_tags():
    """无正文：tags 为空不误报。"""
    page = _FakeDetailPage(swiper_imgs=[_FakeImg("https://img.example/1.jpg")])
    out = sx.extract_note_detail(page, "https://www.xiaohongshu.com/explore/n4")
    assert out["caption"] == "" and out["tags"] == []


def test_extract_note_goto_error_returns_empty():
    """详情页导航失败：返回空结构，不抛异常（调用方跳过该笔记）。"""
    page = _FakeDetailPage(goto_error=RuntimeError("net err"))
    out = sx.extract_note_detail(page, "https://www.xiaohongshu.com/explore/n5")
    assert out == {"img_urls": [], "video_urls": [], "caption": "", "tags": []}


# ── 博主页笔记链接收集 ──


class _FakeProfilePage:
    """博主页假页面：固定一组 /explore/ 链接，evaluate 记录滚动次数。"""

    def __init__(self, hrefs, goto_error=None):
        self._hrefs = list(hrefs)
        self._goto_error = goto_error
        self.evaluate_calls = 0

    def goto(self, _url, **_kw):
        if self._goto_error:
            raise self._goto_error

    def wait_for_selector(self, *_a, **_k):
        pass

    def query_selector_all(self, sel: str):
        if "explore" in sel:
            return [_FakeLink(h) for h in self._hrefs]
        return []

    def evaluate(self, _js):
        self.evaluate_calls += 1


def test_collect_blogger_urls_prefix_and_dedup():
    """相对链接补全域名、绝对链接保留、重复去重。"""
    page = _FakeProfilePage([
        "/explore/aaa?x=1",
        "https://www.xiaohongshu.com/explore/bbb",
        "/explore/aaa",  # 同一篇（去重后不同 URL 由调用方语义决定；此处拼接后不同则保留）
    ])
    urls = sx.collect_blogger_note_urls(page, "https://www.xiaohongshu.com/user/profile/1", 10)
    assert urls == [
        "https://www.xiaohongshu.com/explore/aaa?x=1",
        "https://www.xiaohongshu.com/explore/bbb",
        "https://www.xiaohongshu.com/explore/aaa",
    ]


def test_collect_blogger_urls_max_notes_cap():
    """达到 max_notes 上限立即返回，不再滚动。"""
    page = _FakeProfilePage([f"/explore/n{i}" for i in range(10)])
    urls = sx.collect_blogger_note_urls(page, "https://www.xiaohongshu.com/user/profile/1", 3)
    assert len(urls) == 3
    assert page.evaluate_calls == 0  # 集满即停，未触发滚动


def test_collect_blogger_urls_stops_after_no_new_scrolls():
    """连续 2 次滚动无新链接即停止（懒加载到底）。"""
    page = _FakeProfilePage(["/explore/only"])
    urls = sx.collect_blogger_note_urls(page, "https://www.xiaohongshu.com/user/profile/1", 10)
    assert urls == ["https://www.xiaohongshu.com/explore/only"]
    assert page.evaluate_calls == 2  # 首轮收集后无新增，滚 2 次确认到底


def test_collect_blogger_urls_goto_error_returns_empty():
    page = _FakeProfilePage(["/explore/a"], goto_error=RuntimeError("nav fail"))
    assert sx.collect_blogger_note_urls(page, "https://www.xiaohongshu.com/user/profile/1", 10) == []


# ── 搜索模式 ──


class _FakeSearchCard:
    def __init__(self, href, imgs):
        self._href = href
        self._imgs = list(imgs)

    def query_selector(self, sel: str):
        return _FakeLink(self._href) if sel == "a" else None

    def query_selector_all(self, sel: str):
        return list(self._imgs) if sel == "img" else []


class _FakeSearchPage:
    def __init__(self, cards, closed=False):
        self._cards = list(cards)
        self._closed = closed
        self.urls: list[str] = []

    def is_closed(self) -> bool:
        return self._closed

    def goto(self, url, **_kw):
        self.urls.append(url)

    def wait_for_selector(self, *_a, **_k):
        raise RuntimeError("timeout")  # 模拟等待超时，函数应继续走 DOM 提取

    def evaluate(self, *_a):
        pass

    def query_selector_all(self, sel: str):
        return list(self._cards) if sel == "section.note-item" else []


def test_search_xhs_extracts_pairs_with_filters():
    """搜索提取：多图卡片全收、图标/小尺寸过滤、跨卡片去重、漏斗计数正确。"""
    cards = [
        # 正常卡片：2 张内容图
        _FakeSearchCard("/explore/n1", [
            _FakeImg("https://img.example/a.jpg"),
            _FakeImg("https://img.example/b.jpg"),
        ]),
        # 图标 URL（头像/图标关键词）被过滤
        _FakeSearchCard("/explore/n2", [
            _FakeImg("https://img.example/avatar.jpg"),
            _FakeImg("https://img.example/logo.png"),
        ]),
        # 小尺寸（width<100）被过滤
        _FakeSearchCard("/explore/n3", [
            _FakeImg("https://img.example/small.jpg", width=80, height=80),
        ]),
        # 无图片卡片
        _FakeSearchCard("/explore/n4", []),
    ]
    page = _FakeSearchPage(cards)
    pairs, funnel = sx.search_xiaohongshu(page, "穿搭", 20)

    assert pairs == [
        ("https://www.xiaohongshu.com/explore/n1", "https://img.example/a.jpg"),
        ("https://www.xiaohongshu.com/explore/n1", "https://img.example/b.jpg"),
    ]
    assert funnel["cards_total"] == 4
    assert funnel["cards_with_img"] == 3
    assert funnel["cards_without_img"] == 1
    assert funnel["skipped_icon"] == 2
    assert funnel["skipped_small"] == 1
    assert funnel["urls_extracted"] == 2


def test_search_xhs_dedup_across_cards():
    """同一图片出现在多张卡片（转发现象）只采一次。"""
    cards = [
        _FakeSearchCard("/explore/n1", [_FakeImg("https://img.example/same.jpg")]),
        _FakeSearchCard("/explore/n2", [_FakeImg("https://img.example/same.jpg")]),
    ]
    pairs, funnel = sx.search_xiaohongshu(_FakeSearchPage(cards), "穿搭", 20)
    assert len(pairs) == 1
    assert funnel["urls_extracted"] == 1


def test_search_xhs_url_keyword_and_sort():
    """搜索 URL 携带编码后的关键词与排序参数。"""
    page = _FakeSearchPage([])
    sx.search_xiaohongshu(page, "夏日 穿搭", 10, sort_type="time_descending")
    assert "keyword=%E5%A4%8F%E6%97%A5%20%E7%A9%BF%E6%90%AD" in page.urls[0]
    assert "sort=time_descending" in page.urls[0]
    assert "source=web_search_result_notes" in page.urls[0]


def test_search_xhs_pairs_capped_by_need_count():
    """返回 pair 数不超过 need_count × 2。"""
    cards = [
        _FakeSearchCard(f"/explore/n{i}", [_FakeImg(f"https://img.example/{i}.jpg")])
        for i in range(10)
    ]
    pairs, _funnel = sx.search_xiaohongshu(_FakeSearchPage(cards), "穿搭", 3)
    assert len(pairs) <= 6


def test_search_xhs_closed_page_raises():
    with pytest.raises(RuntimeError, match="页面已关闭"):
        sx.search_xiaohongshu(_FakeSearchPage([], closed=True), "穿搭", 10)


# ── 博主采集管线 ──


def test_run_blogger_mode_missing_profile_raises():
    """profile_url 与 platform_user_id 均缺失：明确报错而非静默空跑。"""
    with pytest.raises(RuntimeError, match="profile_url"):
        sx.run_blogger_mode(
            page=None, task_id=1, blogger_id=1, config={},
            img_dir=None, videos_dir=None, today="2026-08",
            httpx_module=None, browser_cookies={},
            existing_url_set=set(), content_hash_set=set(),
        )


def test_run_blogger_mode_profile_url_from_platform_user_id(monkeypatch):
    """无 profile_url 时用 platform_user_id 拼博主主页 URL。"""
    captured = {}

    def _fake_collect(page, profile_url, max_notes, max_scrolls=15):
        captured["profile_url"] = profile_url
        return []

    monkeypatch.setattr(sx, "collect_blogger_note_urls", _fake_collect)
    sx.run_blogger_mode(
        page=None, task_id=1, blogger_id=7, config={"platform_user_id": "abc123"},
        img_dir=None, videos_dir=None, today="2026-08",
        httpx_module=None, browser_cookies={},
        existing_url_set=set(), content_hash_set=set(),
    )
    assert captured["profile_url"] == "https://www.xiaohongshu.com/user/profile/abc123"


def test_run_blogger_mode_full_pipeline(monkeypatch):
    """博主模式全流程：多图+视频逐笔记入库，caption/博主关联/话题随 meta_map 传递；
    单笔记提取失败跳过不影响其余笔记。"""
    monkeypatch.setattr(
        sx, "collect_blogger_note_urls",
        lambda *_a, **_k: [
            "https://www.xiaohongshu.com/explore/n1",
            "https://www.xiaohongshu.com/explore/n2",
        ],
    )

    details = {
        "https://www.xiaohongshu.com/explore/n1": {
            "img_urls": ["https://img.example/1.jpg", "https://img.example/2.jpg"],
            "video_urls": ["https://v.example/1.mp4"],
            "caption": "#夏日穿搭 白色系",
            "tags": ["夏日穿搭"],
        },
        # n2 提取抛异常：应跳过并记录 error，不中断整轮
    }

    def _fake_extract(_page, note_url):
        if note_url.endswith("n2"):
            raise RuntimeError("detail boom")
        return details[note_url]

    monkeypatch.setattr(sx, "extract_note_detail", _fake_extract)

    img_calls = []

    def _fake_download_batch(pairs, task_id, _existing, _remaining, *_a, meta_map=None, **_k):
        # run_blogger_mode 以第 10 个位置参数传 meta_map
        if meta_map is None and len(_a) >= 1:
            meta_map = _a[-1]
        img_calls.append((list(pairs), task_id, meta_map))
        return len(pairs), 0, 0, 0, 0

    monkeypatch.setattr(sx, "download_batch", _fake_download_batch)

    video_calls = []

    def _fake_download_videos(pairs, *_a, meta_map=None, **_k):
        if meta_map is None and len(_a) >= 1:
            meta_map = _a[-1]
        video_calls.append((list(pairs), meta_map))
        return len(pairs), 0

    monkeypatch.setattr(sx, "download_videos", _fake_download_videos)

    items_found, items_added, notes_log = sx.run_blogger_mode(
        page=None, task_id=42, blogger_id=7,
        config={"profile_url": "https://www.xiaohongshu.com/user/profile/7"},
        img_dir=None, videos_dir=None, today="2026-08",
        httpx_module=None, browser_cookies={},
        existing_url_set=set(), content_hash_set=set(),
    )

    # 统计：n1 图 2 + 视频 1 = 3，n2 异常跳过
    assert items_found == 3
    assert items_added == 3
    assert len(notes_log) == 2
    assert notes_log[0]["added"] == 3
    assert "error" in notes_log[1]

    # 图片入库带任务 ID 与 meta_map（caption + 博主关联 + 话题）
    assert len(img_calls) == 1
    pairs, task_id, meta_map = img_calls[0]
    assert task_id == 42
    assert pairs == [
        ("https://www.xiaohongshu.com/explore/n1", "https://img.example/1.jpg"),
        ("https://www.xiaohongshu.com/explore/n1", "https://img.example/2.jpg"),
    ]
    meta = meta_map["https://www.xiaohongshu.com/explore/n1"]
    assert meta["caption"] == "#夏日穿搭 白色系"
    assert meta["blogger_id"] == 7
    assert meta["tags"] == ["夏日穿搭"]

    # 视频入库共用同一 meta_map
    assert len(video_calls) == 1
    assert video_calls[0][0] == [("https://www.xiaohongshu.com/explore/n1", "https://v.example/1.mp4")]


def test_run_blogger_mode_empty_note_list(monkeypatch):
    """博主页无笔记：返回全零统计与空日志，不报错。"""
    monkeypatch.setattr(sx, "collect_blogger_note_urls", lambda *_a, **_k: [])
    items_found, items_added, notes_log = sx.run_blogger_mode(
        page=None, task_id=1, blogger_id=1,
        config={"profile_url": "https://www.xiaohongshu.com/user/profile/1"},
        img_dir=None, videos_dir=None, today="2026-08",
        httpx_module=None, browser_cookies={},
        existing_url_set=set(), content_hash_set=set(),
    )
    assert (items_found, items_added, notes_log) == (0, 0, [])
