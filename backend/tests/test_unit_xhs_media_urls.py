"""小红书媒体地址单元测试：原图 CDN 候选链 / 无水印视频直链 / 详情页优先级。

对齐 MediaCrawler 的媒体处理实践：
  - 图片：网页缩略图（sns-webpic-*）换成原图直链（sns-img-*，四节点轮换），
    原 URL 兜底
  - 视频：DOM <video src> 是带水印流，页面状态里的 originVideoKey 才能拼出
    无水印原片（sns-video-bd）
本文件不启动浏览器、不发真实请求。
"""

import pytest

from scripts import scraper_common as sc
from scripts import scraper_xhs as sx


# ── 图片 trace_id 提取 ──


@pytest.mark.parametrize(
    "url, expected",
    [
        (
            "https://sns-webpic-qc.xhscdn.com/202609/abc123!nd_dft_wgth_webp_3",
            "abc123",
        ),
        ("https://sns-webpic-qc.xhscdn.com/202609/abc123?imageView2/format/jpg", "abc123"),
        ("https://sns-img-bd.xhscdn.com/1040g0k031", "1040g0k031"),
        # 浏览器端上传的图片多一层 spectrum/ 路径，需保留
        (
            "https://sns-img-bd.xhscdn.com/spectrum/1040g0k031",
            "spectrum/1040g0k031",
        ),
        ("https://sns-img-bd.xhscdn.com/abc/", "abc"),
        ("", ""),
    ],
)
def test_xhs_image_trace_id(url, expected):
    assert sc.xhs_image_trace_id(url) == expected


# ── 图片候选链 ──


def test_image_candidates_prefers_original_then_fallback():
    """卡片缩略图 → 原图多 CDN 优先，原 URL 兜底（保序、无重复）。"""
    thumb = "https://sns-webpic-qc.xhscdn.com/202609/abc!nd_dft_wgth_webp_3"
    candidates = sc.xhs_image_url_candidates(thumb)
    assert candidates[0] == "https://sns-img-qc.xhscdn.com/abc"
    assert candidates[:4] == [
        f"https://{host}/abc" for host in sc.XHS_IMAGE_CDN_HOSTS
    ]
    assert candidates[-1] == thumb  # 原 URL 兜底
    assert len(candidates) == len(set(candidates))


def test_image_candidates_sns_img_url_not_duplicated():
    """已是原图直链：不再把自身重复追加一遍作为兜底。"""
    url = "https://sns-img-bd.xhscdn.com/abc"
    candidates = sc.xhs_image_url_candidates(url)
    assert candidates.count(url) == 1
    assert len(candidates) == len(sc.XHS_IMAGE_CDN_HOSTS)


def test_image_candidates_non_xhscdn_passthrough():
    """非小红书 CDN（如抖音图集）原样返回，避免误改地址。"""
    url = "https://p3.douyinpic.com/abc.jpg"
    assert sc.xhs_image_url_candidates(url) == [url]


def test_image_candidates_empty():
    assert sc.xhs_image_url_candidates("") == []
    assert sc.xhs_image_url_candidates("   ") == []


# ── 无水印视频直链 ──


def test_video_urls_origin_key_first_then_masters():
    state = {"originKey": "v0300fabc/video.mp4", "masters": ["https://wm.example/a.mp4"]}
    urls = sc.xhs_video_urls_from_page_state(state)
    assert urls == [
        "https://sns-video-bd.xhscdn.com/v0300fabc/video.mp4",
        "https://wm.example/a.mp4",
    ]


def test_video_urls_masters_only():
    state = {"originKey": "", "masters": ["https://wm.example/a.mp4"]}
    assert sc.xhs_video_urls_from_page_state(state) == ["https://wm.example/a.mp4"]


def test_video_urls_empty_state():
    assert sc.xhs_video_urls_from_page_state({}) == []
    assert sc.xhs_video_urls_from_page_state(None) == []


def test_video_urls_dedupe_master_equal_to_origin():
    url = "https://sns-video-bd.xhscdn.com/key.mp4"
    state = {"originKey": "key.mp4", "masters": [url]}
    assert sc.xhs_video_urls_from_page_state(state) == [url]


# ── 详情页接线：无水印优先、DOM 兜底 ──


class _Video:
    def __init__(self, src: str, poster: str = "") -> None:
        self._attrs = {"src": src, "poster": poster}

    def get_attribute(self, name: str):
        return self._attrs.get(name)

    def query_selector(self, _sel: str):
        return None


class _StateDetailPage:
    """详情页假页面：状态里有原片 key，DOM 里是带水印的 <video src>。"""

    def __init__(self, state) -> None:
        self._state = state

    def goto(self, _url, **_kw):
        pass

    def wait_for_selector(self, *_a, **_k):
        pass

    def query_selector(self, _sel: str):
        return None

    def query_selector_all(self, sel: str):
        if sel == "video":
            return [_Video("https://sns-video-bd.xhscdn.com/stream/wm.mp4")]
        return []

    def evaluate(self, _js: str):
        return self._state


def test_extract_note_detail_prefers_watermark_free_video():
    """无水印原片排在最前，带水印的 DOM 流作为兜底排在其后。"""
    page = _StateDetailPage(
        {"originKey": "v0300fabc/origin.mp4", "masters": []}
    )
    out = sx.extract_note_detail(page, "https://www.xiaohongshu.com/explore/n1")
    assert out["video_urls"] == [
        "https://sns-video-bd.xhscdn.com/v0300fabc/origin.mp4",
        "https://sns-video-bd.xhscdn.com/stream/wm.mp4",
    ]


def test_extract_note_detail_falls_back_to_dom_without_state():
    """页面状态取不到（结构变化/未登录）时退回 DOM 视频地址，不丢视频。"""
    page = _StateDetailPage(None)
    out = sx.extract_note_detail(page, "https://www.xiaohongshu.com/explore/n2")
    assert out["video_urls"] == [
        "https://sns-video-bd.xhscdn.com/stream/wm.mp4"
    ]


# ── 下载降级链（真实执行 download_batch，不 mock 下载逻辑）──


class _FakeResp:
    def __init__(self, status: int, content: bytes = b"", ct: str = "image/jpeg"):
        self.status_code = status
        self.content = content
        self.headers = {"content-type": ct}


class _FakeHttpx:
    """假 httpx：记录每次请求 URL，前 fail_until 个请求返回 404。"""

    def __init__(self, fail_until: int = 1) -> None:
        self.calls: list[str] = []
        self._fail_until = fail_until

    def get(self, url: str, **_kwargs):
        self.calls.append(url)
        if len(self.calls) <= self._fail_until:
            return _FakeResp(404)
        return _FakeResp(200, b"\xff\xd8\xff" + b"x" * 32)


THUMB = "https://sns-webpic-qc.xhscdn.com/202609/abc!nd_dft_wgth_webp_3"


def _run_download(fake, img_url=THUMB, platform="xiaohongshu"):
    from app.config import settings

    from scripts import scraper_download as sd

    img_dir = settings.images_dir / "2026-09"
    img_dir.mkdir(parents=True, exist_ok=True)
    return sd.download_batch(
        [("https://www.xiaohongshu.com/explore/n1", img_url)],
        task_id=1,
        existing_url_set=set(),
        remaining=5,
        img_dir=img_dir,
        today="2026-09",
        httpx_module=fake,
        cookies=None,
        content_hash_set=set(),
        platform=platform,
    )


def test_download_batch_falls_back_to_next_cdn():
    """首个 CDN 节点 404 → 换源重试成功；请求的是原图直链而非网页缩略图。"""
    fake = _FakeHttpx(fail_until=1)
    added, _sk_ex, sk_h, _sk_n, _sk_dup = _run_download(fake)

    assert added == 1
    assert sk_h == 0  # 换源成功，不计入失败
    assert fake.calls[0] == "https://sns-img-qc.xhscdn.com/abc"
    assert fake.calls[1] == "https://sns-img-hw.xhscdn.com/abc"
    assert len(fake.calls) == 2


def test_download_batch_counts_one_skip_when_all_candidates_fail():
    """全部候选失败：整张图只记一次失败（不按候选数放大统计）。"""
    fake = _FakeHttpx(fail_until=99)
    added, _sk_ex, sk_h, _sk_n, _sk_dup = _run_download(fake)

    assert added == 0
    assert sk_h == 1
    # 4 个原图 CDN + 原 URL 兜底
    assert len(fake.calls) == len(sc.XHS_IMAGE_CDN_HOSTS) + 1
    assert fake.calls[-1] == THUMB


def test_download_batch_other_platform_url_not_rewritten():
    """非小红书平台（抖音图集）地址原样请求，不套用小红书 CDN 规则。"""
    fake = _FakeHttpx(fail_until=0)
    added, *_rest = _run_download(
        fake, img_url="https://p3.douyinpic.com/abc.jpg", platform="douyin"
    )
    assert added == 1
    assert fake.calls == ["https://p3.douyinpic.com/abc.jpg"]
