"""小红书分页状态机单元测试：URL 归一化 / 笔记 ID 幂等 / 非笔记过滤 / 超额截断。

对齐 MediaCrawler 的分页实践（note_id + cursor 幂等、按 model_type 过滤
非笔记卡片、按剩余额度截断）在 DOM 采集侧的实现。不启动浏览器、不发请求。
"""

from scripts import scraper_xhs as sx


# ── 桩：卡片 / 图片 / 链接 ──


class _Img:
    def __init__(self, src: str, width: str = "", height: str = "") -> None:
        self._attrs = {"src": src, "data-src": "", "width": width, "height": height}

    def get_attribute(self, name: str):
        return self._attrs.get(name)


class _Link:
    def __init__(self, href: str) -> None:
        self._href = href

    def get_attribute(self, name: str):
        return self._href if name == "href" else None


class _Card:
    def __init__(self, href: str, imgs: list[_Img] | None = None) -> None:
        self._href = href
        self._imgs = imgs or []

    def query_selector(self, sel: str):
        return _Link(self._href)

    def query_selector_all(self, sel: str):
        return self._imgs


class _BrokenCard:
    """卡片解析抛异常：不得中断整批提取。"""

    def query_selector(self, sel: str):
        raise RuntimeError("DOM 已 detach")

    def query_selector_all(self, sel: str):
        raise RuntimeError("DOM 已 detach")


# ── URL 归一化 ──


def test_canonical_note_url_relative_explore_keeps_token():
    url = sx.canonical_note_url("/explore/abc123?xsec_token=T1&xsec_source=pc_search")
    assert url == (
        "https://www.xiaohongshu.com/explore/abc123?xsec_token=T1&xsec_source=pc_search"
    )


def test_canonical_note_url_discovery_item():
    assert (
        sx.canonical_note_url("/discovery/item/def456")
        == "https://www.xiaohongshu.com/discovery/item/def456"
    )


def test_canonical_note_url_protocol_relative():
    assert (
        sx.canonical_note_url("//www.xiaohongshu.com/explore/xyz")
        == "https://www.xiaohongshu.com/explore/xyz"
    )


def test_canonical_note_url_rejects_non_note_links():
    """用户主页 / 搜索词 / 空链接都不是笔记（等价于过滤「相关搜索」卡片）。"""
    assert sx.canonical_note_url("/user/profile/5f58bd99") == ""
    assert sx.canonical_note_url("/search_result/?keyword=JK") == ""
    assert sx.canonical_note_url("") == ""


def test_note_id_of_ignores_query_and_slash():
    assert sx.note_id_of("https://www.xiaohongshu.com/explore/abc?xsec_token=T") == "abc"
    assert sx.note_id_of("https://www.xiaohongshu.com/explore/abc/") == "abc"
    assert sx.note_id_of("") == ""


# ── 卡片提取：幂等 / 过滤 / 截断 ──


def _extract(cards, need_pairs, seen=None, seen_notes=None):
    counters: dict = {}
    pairs = sx.extract_image_pairs_from_cards(
        cards, need_pairs, seen if seen is not None else set(),
        seen_notes if seen_notes is not None else set(), counters,
    )
    return pairs, counters


def test_extract_skips_non_note_cards():
    cards = [
        _Card("/user/profile/uid1", [_Img("https://sns-webpic-qc.xhscdn.com/a.jpg")]),
        _Card("/explore/n1", [_Img("https://sns-webpic-qc.xhscdn.com/b.jpg")]),
    ]
    pairs, counters = _extract(cards, 10)
    assert [u for _, u in pairs] == ["https://sns-webpic-qc.xhscdn.com/b.jpg"]
    assert counters["non_note"] == 1


def test_extract_dedupes_same_note_with_different_token():
    """回归：同一篇笔记不同 xsec_token 只处理一次（否则重复下载）。"""
    cards = [
        _Card("/explore/n1?xsec_token=T1", [_Img("https://sns-webpic-qc.xhscdn.com/a.jpg")]),
        _Card("/explore/n1?xsec_token=T2", [_Img("https://sns-webpic-qc.xhscdn.com/b.jpg")]),
    ]
    pairs, counters = _extract(cards, 10)
    assert len(pairs) == 1
    assert counters["dup_note"] == 1


def test_extract_dedupes_same_image_across_rounds():
    """滚动重叠区域：同一张图跨轮次只取一次（seen 集合由调用方跨轮复用）。"""
    seen: set[str] = set()
    seen_notes: set[str] = set()
    img = _Img("https://sns-webpic-qc.xhscdn.com/same.jpg")
    first, _ = _extract([_Card("/explore/n1", [img])], 10, seen, seen_notes)
    second, counters = _extract([_Card("/explore/n2", [img])], 10, seen, seen_notes)
    assert len(first) == 1
    assert second == []
    assert counters["dup_img"] == 1


def test_extract_truncates_to_need_pairs():
    """超额截断：need_pairs=2 时不再解析更多卡片。"""
    cards = [
        _Card(f"/explore/n{i}", [_Img(f"https://sns-webpic-qc.xhscdn.com/{i}.jpg")])
        for i in range(5)
    ]
    pairs, _ = _extract(cards, 2)
    assert len(pairs) == 2


def test_extract_skips_icon_and_small_images():
    cards = [
        _Card(
            "/explore/n1",
            [
                _Img("https://sns-webpic-qc.xhscdn.com/avatar_x.jpg"),
                _Img("https://sns-webpic-qc.xhscdn.com/small.jpg", "50", "50"),
                _Img("https://sns-webpic-qc.xhscdn.com/ok.jpg", "800", "1200"),
            ],
        )
    ]
    pairs, counters = _extract(cards, 10)
    assert [u for _, u in pairs] == ["https://sns-webpic-qc.xhscdn.com/ok.jpg"]
    assert counters["icon"] == 1
    assert counters["small"] == 1


def test_extract_survives_broken_card():
    cards = [
        _BrokenCard(),
        _Card("/explore/n1", [_Img("https://sns-webpic-qc.xhscdn.com/ok.jpg")]),
    ]
    pairs, _ = _extract(cards, 10)
    assert len(pairs) == 1


def test_extract_counts_card_without_image():
    pairs, counters = _extract([_Card("/explore/n1", [])], 10)
    assert pairs == []
    assert counters["without_img"] == 1


# ── 博主主页收集：笔记 ID 去重 ──


class _ProfilePage:
    """博主主页桩：每轮滚动返回一批链接（模拟懒加载 + token 变化）。"""

    def __init__(self, rounds: list[list[str]]) -> None:
        self._rounds = rounds
        self._idx = 0
        self.goto_calls: list[str] = []

    def goto(self, url: str, **kwargs) -> None:
        self.goto_calls.append(url)

    def wait_for_selector(self, sel: str, **kwargs):
        return True

    def query_selector(self, sel: str):
        return None  # 无风控元素

    def inner_text(self, sel: str = "") -> str:
        return "博主主页"

    def query_selector_all(self, sel: str):
        if "/explore/" in sel:
            links = self._rounds[min(self._idx, len(self._rounds) - 1)]
            self._idx += 1
            return [_Link(h) for h in links]
        return []

    def evaluate(self, script: str):
        return None


def test_collect_blogger_note_urls_dedupes_by_note_id():
    """同一笔记在不同轮次带不同 token → 只收一次详情页。"""
    page = _ProfilePage(
        [
            ["/explore/n1?xsec_token=T1"],
            ["/explore/n1?xsec_token=T2", "/explore/n2?xsec_token=T3"],
        ]
    )
    urls = sx.collect_blogger_note_urls(
        page, "https://www.xiaohongshu.com/user/profile/uid1", 10, max_scrolls=3
    )
    assert urls == [
        "https://www.xiaohongshu.com/explore/n1?xsec_token=T1",
        "https://www.xiaohongshu.com/explore/n2?xsec_token=T3",
    ]


def test_collect_blogger_note_urls_respects_max_notes():
    page = _ProfilePage([["/explore/n1", "/explore/n2", "/explore/n3"]])
    urls = sx.collect_blogger_note_urls(
        page, "https://www.xiaohongshu.com/user/profile/uid1", 2, max_scrolls=1
    )
    assert len(urls) == 2
