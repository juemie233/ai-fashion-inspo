"""小红书风控/异常页面识别单元测试（对齐 MediaCrawler 的错误分类实践）。

覆盖：
  - 文本特征 → 风控类型分类（纯函数，不依赖浏览器）
  - 可见性过滤：隐藏验证码模板不得误判为验证态（抖音侧曾因此空等 180s）
  - ScraperBlockedError 的致命/非致命语义（风控类不重试，停止整轮）
  - 采集入口接线：搜索页 / 详情页命中风控即抛出，不再静默返回空结果
"""

import pytest

from scripts import scraper_common as sc
from scripts import scraper_xhs as sx


class _El:
    """最小元素桩：只实现 is_visible。"""

    def __init__(self, visible: bool) -> None:
        self._visible = visible

    def is_visible(self) -> bool:
        return self._visible


class _FakePage:
    """最小页面桩：只实现风控检测所需接口。"""

    def __init__(
        self,
        text: str = "",
        captcha: bool = False,
        hidden_captcha: bool = False,
        closed: bool = False,
    ) -> None:
        self._text = text
        self._captcha = captcha
        self._hidden_captcha = hidden_captcha
        self._closed = closed
        self.goto_calls: list[str] = []

    def is_closed(self) -> bool:
        return self._closed

    def goto(self, url: str, **kwargs) -> None:
        self.goto_calls.append(url)

    def wait_for_selector(self, sel: str, **kwargs):
        raise Exception("未渲染")  # 风控页永远等不到内容区

    def query_selector(self, sel: str):
        if "captcha" in sel or "slider" in sel:
            if self._captcha:
                return _El(True)
            if self._hidden_captcha:
                return _El(False)
        return None

    def inner_text(self, sel: str = "") -> str:
        return self._text


# ── 文本分类（纯函数）──


@pytest.mark.parametrize(
    "text, expected",
    [
        ("请完成安全验证后继续", "captcha"),
        ("拖动滑块完成拼图", "captcha"),
        ("登录后查看搜索结果", "login_wall"),
        ("扫码登录小红书", "login_wall"),
        ("操作过于频繁，请稍后再试", "rate_limit"),
        ("系统繁忙", "rate_limit"),
        ("账号异常，请更换设备", "account_risk"),
        ("笔记不存在", "not_found"),
        ("当前笔记暂时无法浏览", "not_found"),
        ("今天穿了一条碎花连衣裙，很显瘦", ""),
    ],
)
def test_match_xhs_block_text(text, expected):
    assert sc.match_xhs_block_text(text) == expected


def test_match_xhs_block_text_only_scans_first_screen():
    """正文深处的「安全验证」字样不得触发误判（只看首屏 2000 字）。"""
    text = "正常笔记正文" * 400 + "请完成安全验证"
    assert len(text) > 2000
    assert sc.match_xhs_block_text(text) == ""


# ── 页面分类（含可见性过滤）──


def test_classify_visible_captcha():
    assert sc.classify_xhs_block(_FakePage(captcha=True)) == "captcha"


def test_classify_hidden_captcha_not_misjudged():
    """回归：页面预注入的隐藏验证容器不得判为验证态。"""
    assert sc.classify_xhs_block(_FakePage(hidden_captcha=True)) == ""


def test_classify_hidden_captcha_falls_back_to_text():
    """隐藏验证码 + 登录墙文案 → 仍能按文本判出登录墙。"""
    page = _FakePage(text="登录后查看搜索结果", hidden_captcha=True)
    assert sc.classify_xhs_block(page) == "login_wall"


def test_classify_normal_page():
    assert sc.classify_xhs_block(_FakePage(text="穿搭笔记列表")) == ""


# ── 异常语义：致命 vs 非致命 ──


@pytest.mark.parametrize(
    "kind", ["captcha", "login_wall", "rate_limit", "account_risk"]
)
def test_blocked_error_fatal_kinds(kind):
    assert sc.ScraperBlockedError(kind).is_fatal is True


def test_blocked_error_not_found_is_not_fatal():
    """笔记已删除只影响当前条目：跳过即可，不该停掉整轮采集。"""
    assert sc.ScraperBlockedError("not_found").is_fatal is False


def test_blocked_error_message_includes_detail():
    err = sc.ScraperBlockedError("captcha", "关键词=JK")
    assert "安全验证" in str(err)
    assert "关键词=JK" in str(err)


def test_raise_if_xhs_blocked_normal_page_returns_empty():
    assert sc.raise_if_xhs_blocked(_FakePage(text="正常内容")) == ""


def test_raise_if_xhs_blocked_raises_with_kind():
    with pytest.raises(sc.ScraperBlockedError) as ei:
        sc.raise_if_xhs_blocked(_FakePage(text="访问频繁"), "关键词=通勤")
    assert ei.value.kind == "rate_limit"
    assert ei.value.is_fatal is True


# ── 采集入口接线 ──


def test_search_raises_on_login_wall():
    """搜索页被登录墙拦截：抛风控异常供任务层停止整轮，而非静默返回空结果。"""
    page = _FakePage(text="登录后查看搜索结果")
    with pytest.raises(sc.ScraperBlockedError) as ei:
        sx.search_xiaohongshu(page, "JK制服", 20, "general")
    assert ei.value.kind == "login_wall"
    assert page.goto_calls and "search_result" in page.goto_calls[0]


def test_search_raises_on_rate_limit_after_no_cards():
    """静默风控：卡片始终不出且页面转限流文案 → 按风控处理。"""
    page = _FakePage(text="操作过于频繁，请稍后再试")
    with pytest.raises(sc.ScraperBlockedError) as ei:
        sx.search_xiaohongshu(page, "连衣裙", 20, "general")
    assert ei.value.kind == "rate_limit"


def test_extract_note_detail_raises_on_captcha():
    """详情页命中验证码：抛异常而不是返回空结果（避免误判「这篇没图片」）。"""
    page = _FakePage(captcha=True)
    with pytest.raises(sc.ScraperBlockedError) as ei:
        sx.extract_note_detail(page, "https://www.xiaohongshu.com/explore/abc")
    assert ei.value.kind == "captcha"
    assert "abc" in ei.value.detail


def test_extract_note_detail_not_found_is_non_fatal():
    page = _FakePage(text="笔记不存在")
    with pytest.raises(sc.ScraperBlockedError) as ei:
        sx.extract_note_detail(page, "https://www.xiaohongshu.com/explore/gone")
    assert ei.value.is_fatal is False


def test_collect_blogger_note_urls_raises_on_captcha():
    page = _FakePage(captcha=True)
    with pytest.raises(sc.ScraperBlockedError):
        sx.collect_blogger_note_urls(
            page, "https://www.xiaohongshu.com/user/profile/uid1", 10
        )
