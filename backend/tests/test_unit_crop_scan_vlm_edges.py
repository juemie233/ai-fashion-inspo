"""裁剪扫描 + VLM 复核（`services/crop_scan.py` / `services/crop_vlm.py`）边界补测。

既有 `tests/test_crop_scan.py` 通过接口覆盖真实图片判定主路径；这里补纯函数解析、
条带裁图、VLM 请求/异常、候选扫描的分支（时间预算、上限断点、content 两段游标、
单张检测的自动模式失败与 content 四种标注分支），以及 VLM 主判的阳性/阴性/未知/
超时处理。全部在函数边界 mock，不触网、不调真实 Ollama。
"""

from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from app.config import settings
from app.database import async_session
from app.models.inspiration import Inspiration
from app.services import crop_scan as cs
from app.services import crop_vlm as cv
from app.services.crop_candidates import _STAGE_CROPPED, _STAGE_FULL
from app.services.crop_common import CONTENT_MIN_RATIO, MIN_RATIO


# ── 通用小工具 ──


def _write_image(rel_path: str, size=(100, 400), color=(200, 200, 200)) -> str:
    path = settings.storage_root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, format="JPEG")
    return rel_path


def _img_bytes(size=(100, 400), color=(180, 180, 180)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


def _direct_insp(rel_path: str | None) -> Inspiration:
    """未落库的 Inspiration 实例（被测函数只读属性，不写库）。"""
    return Inspiration(id="insp-direct", file_path=rel_path, media_type="image")


def _bounds(**overrides) -> dict:
    base = {
        "top_frac": 0.0,
        "bottom_frac": 0.0,
        "kind": "plain",
        "glyph_top_frac": 0.0,
        "residual_top_frac": 0.0,
        "residual_bottom_frac": 0.0,
        "already_cropped": False,
    }
    base.update(overrides)
    return base


def _upload_screen(client, size=(100, 400)) -> str:
    r = client.post(
        "/api/inspirations",
        files={"file": ("shot.jpg", _img_bytes(size), "image/jpeg")},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.fixture(autouse=True)
def _reset_vlm_client(monkeypatch):
    """模块级 httpx 客户端缓存跨用例共享：每个用例前清空，避免污染。"""
    monkeypatch.setattr(cv, "_vlm_client", None)


# ══════════════════════════════════════════════════════════════
#  crop_vlm：解析 / 裁条 / 单条带判定 / 双条带复核
# ══════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", None),
        ("   ", None),
        ('{"bar": true}', True),
        ('{"bar": false}', False),
        ('{"bar": null}', None),
        ('{"bar": 1}', None),  # 非布尔
        ("```json\n{\"bar\": true}\n```", True),
        ("```\n{\"bar\": false}\n```", False),
        ('prefix "bar": true suffix', True),
        ('prefix "bar": false suffix', False),
        ("完全无法解析", None),
        ("[]", None),  # 非 dict → AttributeError 后走兜底
    ],
)
def test_parse_bar_answer_three_states(text, expected):
    assert cv._parse_bar_answer(text) is expected


def test_strip_jpeg_bytes_top_and_bottom(tmp_path):
    full = tmp_path / "full.jpg"
    Image.new("RGB", (100, 400), (200, 200, 200)).save(full, format="JPEG")

    top_bytes = cv._strip_jpeg_bytes(full, top=True)
    with Image.open(io.BytesIO(top_bytes)) as top:
        assert top.format == "JPEG"
        assert top.size == (768, 368)  # 顶部 12% = 48px：48*768//100

    bottom_bytes = cv._strip_jpeg_bytes(full, top=False)
    with Image.open(io.BytesIO(bottom_bytes)) as bottom:
        assert bottom.size == (768, 460)  # 底部 15% = 60px：60*768//100


def test_strip_jpeg_bytes_min_height_floor(tmp_path):
    """极扁的图裁出的条带高度有 24px 下限。"""
    full = tmp_path / "thin.jpg"
    Image.new("RGB", (2000, 20), (100, 100, 100)).save(full, format="JPEG")
    out = cv._strip_jpeg_bytes(full, top=True)
    with Image.open(io.BytesIO(out)) as img:
        assert img.height >= 24


class _FakeVlmResponse:
    def __init__(self, payload: dict | None = None, json_error: Exception | None = None):
        self._payload = payload or {"message": {"content": '{"bar": true}'}}
        self._json_error = json_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class _FakeVlmClient:
    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self.response = kwargs.pop("response", None) or _FakeVlmResponse()
        self.post_error = kwargs.pop("post_error", None)
        self.calls: list[tuple[str, dict]] = []

    async def post(self, url, json=None, **kwargs):  # noqa: ANN001
        self.calls.append((url, json))
        if self.post_error is not None:
            raise self.post_error
        return self.response


async def test_vlm_strip_check_sends_top_prompt(monkeypatch):
    fake = _FakeVlmClient()
    monkeypatch.setattr(cv, "_vlm_client", fake)
    monkeypatch.setattr(settings, "ollama_base_url", "http://fake-ollama:11434")
    monkeypatch.setattr(settings, "ollama_vision_model", "fake-vlm")

    verdict = await cv._vlm_strip_check(lambda: b"JPEGDATA", top=True)

    assert verdict is True
    url, payload = fake.calls[0]
    assert url == "http://fake-ollama:11434/api/chat"
    assert payload["model"] == "fake-vlm"
    assert payload["stream"] is False
    assert payload["think"] is False
    assert payload["options"] == {"temperature": 0, "num_predict": 32, "num_ctx": 2048}
    message = payload["messages"][0]
    assert message["role"] == "user"
    assert "状态栏" in message["content"]
    assert base64.b64decode(message["images"][0]) == b"JPEGDATA"


async def test_vlm_strip_check_bottom_prompt_differs(monkeypatch):
    fake = _FakeVlmClient(
        response=_FakeVlmResponse({"message": {"content": '{"bar": false}'}})
    )
    monkeypatch.setattr(cv, "_vlm_client", fake)

    verdict = await cv._vlm_strip_check(lambda: b"X", top=False)

    assert verdict is False
    assert "进度条" in fake.calls[0][1]["messages"][0]["content"]


@pytest.mark.parametrize(
    "error",
    [RuntimeError("boom"), ConnectionError("refused")],
)
async def test_vlm_strip_check_returns_none_on_post_error(monkeypatch, error):
    monkeypatch.setattr(cv, "_vlm_client", _FakeVlmClient(post_error=error))
    assert await cv._vlm_strip_check(lambda: b"X", top=True) is None


async def test_vlm_strip_check_returns_none_on_json_error(monkeypatch):
    fake = _FakeVlmClient(
        response=_FakeVlmResponse(json_error=ValueError("bad json"))
    )
    monkeypatch.setattr(cv, "_vlm_client", fake)
    assert await cv._vlm_strip_check(lambda: b"X", top=True) is None


async def test_vlm_strip_check_returns_none_when_image_builder_raises(monkeypatch):
    monkeypatch.setattr(cv, "_vlm_client", _FakeVlmClient())

    def _boom() -> bytes:
        raise OSError("图片读取失败")

    assert await cv._vlm_strip_check(_boom, top=True) is None


async def test_vlm_strip_check_lazily_creates_http_client(monkeypatch):
    """模块级客户端未初始化时懒创建（timeout=30），并复用到后续调用。"""
    monkeypatch.setattr(cv.httpx, "AsyncClient", _FakeVlmClient)
    assert cv._vlm_client is None  # autouse fixture 已清空

    assert await cv._vlm_strip_check(lambda: b"X", top=True) is True
    created = cv._vlm_client
    assert isinstance(created, _FakeVlmClient)
    await cv._vlm_strip_check(lambda: b"Y", top=False)
    assert cv._vlm_client is created  # 复用，不重复创建


@pytest.mark.parametrize(
    ("top_hit", "bottom_hit", "expected"),
    [
        (True, False, True),
        (False, True, True),
        (True, True, True),
        (False, False, False),
        (None, False, None),
        (False, None, None),
        (None, None, None),
    ],
)
async def test_vlm_residue_review_three_state_merge(
    monkeypatch, top_hit, bottom_hit, expected
):
    seen: list[bool] = []

    async def fake_check(image_bytes_func, top):  # noqa: ANN001
        seen.append(top)
        assert callable(image_bytes_func)
        return top_hit if top else bottom_hit

    monkeypatch.setattr(cv, "_vlm_strip_check", fake_check)
    assert await cv._vlm_residue_review(settings.storage_root / "whatever.jpg") is expected
    assert seen == [True, False]  # 顶部与底部分别独立提问


# ══════════════════════════════════════════════════════════════
#  crop_scan.scan_candidates：参数校验 / 时间预算 / 上限断点 / 两段游标 / VLM
# ══════════════════════════════════════════════════════════════


async def test_scan_rejects_invalid_mode_and_ratio():
    async with async_session() as db:
        with pytest.raises(ValueError) as mode_exc:
            await cs.scan_candidates(db, mode="unknown")
        assert "不支持的裁剪模式" in str(mode_exc.value)

        with pytest.raises(ValueError) as ratio_exc:
            await cs.scan_candidates(db, mode="ratio", crop_top=0.6, crop_bottom=0.5)
        assert "裁剪比例合计必须 < 1" in str(ratio_exc.value)


async def test_scan_limit_and_cursor_pagination(client, monkeypatch):
    """候选上限断点：next_cursor 指向已入选的最后一条，续扫不重不漏。"""
    first = _upload_screen(client, size=(100, 500))
    second = _upload_screen(client, size=(100, 600))

    async with async_session() as db:
        page1 = await cs.scan_candidates(db, mode="ratio", limit=1)

    assert page1["truncated"] is True
    assert page1["total"] == 1
    assert page1["scanned"] == 1
    assert page1["next_cursor"].startswith(f"{_STAGE_FULL}:")

    async with async_session() as db:
        page2 = await cs.scan_candidates(
            db, mode="ratio", limit=1, cursor=page1["next_cursor"]
        )
    # 第 2 条候选恰好达到上限：仍标 truncated（无法预知是否还有下一条）
    assert page2["total"] == 1
    assert page2["truncated"] is True
    assert {item["id"] for item in page1["items"] + page2["items"]} == {first, second}

    async with async_session() as db:
        page3 = await cs.scan_candidates(
            db, mode="ratio", limit=1, cursor=page2["next_cursor"]
        )
    assert page3["truncated"] is False
    assert page3["items"] == []
    assert page3["next_cursor"] is None


async def test_scan_time_budget_exhausted_before_first_item(client, monkeypatch):
    """预算在第一张前就耗尽：truncated=True 且无游标（什么都没扫）。"""
    _upload_screen(client)
    fake_time = type("FakeTime", (), {})()
    ticks = iter([0.0, 999.0])
    fake_time.monotonic = lambda: next(ticks)
    monkeypatch.setattr(cs, "time", fake_time)

    async with async_session() as db:
        body = await cs.scan_candidates(db, mode="ratio", time_budget=60.0)

    assert body["truncated"] is True
    assert body["scanned"] == 0
    assert body["items"] == []
    assert body["next_cursor"] is None


async def test_scan_content_mode_switches_to_cropped_stage(client, monkeypatch):
    """content 模式：完整截图段扫完仍有「已裁过」素材 → 切第二段游标。"""
    _upload_screen(client, size=(100, 220))  # ratio 2.2 → 完整截图段
    _upload_screen(client, size=(100, 150))  # ratio 1.5 → 已裁过段（本轮推迟）

    monkeypatch.setattr(cs, "_FULL_SCREENSHOT_RATIO", 2.0)

    def fake_analyze(path):  # noqa: ANN001
        return {}, _bounds(top_frac=0.02, bottom_frac=0.05, kind="gray_band")

    monkeypatch.setattr(cs, "analyze_screenshot_combined", fake_analyze)
    monkeypatch.setattr(cs, "screenshot_confidence", lambda _features: "high")
    monkeypatch.setattr(cs, "_content_candidate_qualified", lambda *args: True)

    async with async_session() as db:
        body = await cs.scan_candidates(db, mode="content")

    assert body["total"] == 1
    assert body["truncated"] is True
    assert body["next_cursor"] == f"{_STAGE_CROPPED}:"
    assert body["items"][0]["boundary_kind"] == "gray_band"


async def test_scan_vlm_review_keeps_and_removes(client, monkeypatch):
    """VLM 主判：阳性保留并置顶，阴性移除。"""
    _upload_screen(client)

    async def _positive(_path):
        return True

    monkeypatch.setattr(cs, "_vlm_residue_review", _positive)
    async with async_session() as db:
        kept = await cs.scan_candidates(db, mode="ratio", vlm_review=True)
    assert kept["vlm_reviewed"] is True
    assert kept["vlm_hits"] == 1
    assert kept["vlm_removed"] == 0
    assert kept["items"][0]["vlm_residue"] is True
    assert "AI 复核：检出系统 UI 残留" in kept["items"][0]["note"]

    async def _negative(_path):
        return False

    monkeypatch.setattr(cs, "_vlm_residue_review", _negative)
    async with async_session() as db:
        removed = await cs.scan_candidates(db, mode="ratio", vlm_review=True)
    assert removed["total"] == 0
    assert removed["vlm_removed"] == 1
    assert removed["vlm_reviewed"] is False


# ══════════════════════════════════════════════════════════════
#  crop_scan._scan_one_candidate：逐分支
# ══════════════════════════════════════════════════════════════


async def test_scan_one_missing_and_undecodable_files():
    async with async_session():
        assert await cs._scan_one_candidate(_direct_insp(None), "ratio", 0.1, 0.1, MIN_RATIO) == (None, False)
        assert await cs._scan_one_candidate(
            _direct_insp("no/such/file.jpg"), "ratio", 0.1, 0.1, MIN_RATIO
        ) == (None, False)

        bad_rel = "scan/bad.jpg"
        (settings.storage_root / bad_rel).parent.mkdir(parents=True, exist_ok=True)
        (settings.storage_root / bad_rel).write_bytes(b"not-an-image")
        assert await cs._scan_one_candidate(
            _direct_insp(bad_rel), "ratio", 0.1, 0.1, MIN_RATIO
        ) == (None, False)


async def test_scan_one_ratio_below_threshold():
    rel = _write_image("scan/wide.jpg", size=(400, 400))
    async with async_session():
        assert await cs._scan_one_candidate(
            _direct_insp(rel), "ratio", 0.1, 0.1, MIN_RATIO
        ) == (None, False)


async def test_scan_one_content_stage_mismatch_postpones(monkeypatch):
    """content 两段：完整截图素材在 cropped 段被推迟（不做解码）。"""
    monkeypatch.setattr(cs, "_FULL_SCREENSHOT_RATIO", 2.0)
    tall = _write_image("scan/tall.jpg", size=(100, 400))
    short = _write_image("scan/short.jpg", size=(100, 150))

    async with async_session():
        assert await cs._scan_one_candidate(
            _direct_insp(tall), "content", 0.0, 0.0, CONTENT_MIN_RATIO, _STAGE_CROPPED
        ) == (None, True)
        assert await cs._scan_one_candidate(
            _direct_insp(short), "content", 0.0, 0.0, CONTENT_MIN_RATIO, _STAGE_FULL
        ) == (None, True)


async def test_scan_one_content_detect_failure_is_silently_excluded(monkeypatch):
    rel = _write_image("scan/content.jpg", size=(100, 400))

    def _boom(_path):
        raise RuntimeError("检测炸了")

    monkeypatch.setattr(cs, "analyze_screenshot_combined", _boom)
    async with async_session():
        assert await cs._scan_one_candidate(
            _direct_insp(rel), "content", 0.0, 0.0, CONTENT_MIN_RATIO, _STAGE_FULL
        ) == (None, False)


async def test_scan_one_content_unqualified_excluded(monkeypatch):
    rel = _write_image("scan/content2.jpg", size=(100, 400))
    monkeypatch.setattr(
        cs, "analyze_screenshot_combined", lambda _path: ({}, _bounds())
    )
    monkeypatch.setattr(cs, "screenshot_confidence", lambda _f: "low")
    monkeypatch.setattr(cs, "_content_candidate_qualified", lambda *a: False)
    async with async_session():
        assert await cs._scan_one_candidate(
            _direct_insp(rel), "content", 0.0, 0.0, CONTENT_MIN_RATIO, _STAGE_FULL
        ) == (None, False)


async def test_scan_one_content_bottom_residual_only(monkeypatch):
    """仅底部残留：保留候选但不自动勾选，标注建议比例。"""
    rel = _write_image("scan/content3.jpg", size=(100, 400))
    bounds = _bounds(top_frac=0.0, residual_bottom_frac=0.04, kind="gray_band")
    monkeypatch.setattr(cs, "analyze_screenshot_combined", lambda _path: ({}, bounds))
    monkeypatch.setattr(cs, "screenshot_confidence", lambda _f: "high")
    monkeypatch.setattr(cs, "_content_candidate_qualified", lambda *a: True)

    async with async_session():
        item, postponed = await cs._scan_one_candidate(
            _direct_insp(rel), "content", 0.0, 0.0, CONTENT_MIN_RATIO, _STAGE_FULL
        )

    assert postponed is False
    assert item["auto_ok"] is False
    assert item["auto_checked"] is False
    assert item["crop_bottom"] == pytest.approx(0.04)
    assert "疑似底部导航条/进度条残留" in item["note"]


async def test_scan_one_content_top_suggestion_high_and_medium(monkeypatch):
    """疑似顶部残留：高置信默认勾选；非 high 只给建议不勾选。"""
    rel = _write_image("scan/content4.jpg", size=(100, 400))
    bounds = _bounds(top_frac=0.0, residual_top_frac=0.06, kind="status_bar")

    for confidence, checked in (("high", True), ("medium", False)):
        monkeypatch.setattr(
            cs, "analyze_screenshot_combined", lambda _p, b=bounds: ({}, b)
        )
        monkeypatch.setattr(cs, "screenshot_confidence", lambda _f, c=confidence: c)
        monkeypatch.setattr(cs, "_content_candidate_qualified", lambda *a: True)
        async with async_session():
            item, _postponed = await cs._scan_one_candidate(
                _direct_insp(rel), "content", 0.0, 0.0, CONTENT_MIN_RATIO, _STAGE_FULL
            )
        assert item["auto_ok"] is False
        assert item["auto_checked"] is checked
        assert item["crop_top"] == pytest.approx(0.06)
        assert "疑似顶部状态栏残留" in item["note"]
        assert ("已默认勾选" in item["note"]) is checked


async def test_scan_one_content_already_cropped(monkeypatch):
    rel = _write_image("scan/content5.jpg", size=(100, 400))
    bounds = _bounds(top_frac=0.0, already_cropped=True, kind="status_bar")
    monkeypatch.setattr(cs, "analyze_screenshot_combined", lambda _path: ({}, bounds))
    monkeypatch.setattr(cs, "screenshot_confidence", lambda _f: "low")
    monkeypatch.setattr(cs, "_content_candidate_qualified", lambda *a: True)

    async with async_session():
        item, _ = await cs._scan_one_candidate(
            _direct_insp(rel), "content", 0.0, 0.0, CONTENT_MIN_RATIO, _STAGE_FULL
        )
    assert item["auto_ok"] is False
    assert item["crop_top"] == 0.0
    assert item["note"] == "未检出可裁区域（可能已裁剪过），请目检确认"


async def test_scan_one_content_normal_bounds(monkeypatch):
    rel = _write_image("scan/content6.jpg", size=(100, 400))
    bounds = _bounds(top_frac=0.03, bottom_frac=0.05, kind="gray_band")
    monkeypatch.setattr(cs, "analyze_screenshot_combined", lambda _path: ({}, bounds))
    monkeypatch.setattr(cs, "screenshot_confidence", lambda _f: "high")
    monkeypatch.setattr(cs, "_content_candidate_qualified", lambda *a: True)

    async with async_session():
        item, _ = await cs._scan_one_candidate(
            _direct_insp(rel), "content", 0.0, 0.0, CONTENT_MIN_RATIO, _STAGE_FULL
        )
    assert item["crop_top"] == pytest.approx(0.03)
    assert item["crop_bottom"] == pytest.approx(0.05)
    assert item["boundary_kind"] == "gray_band"


async def test_scan_one_auto_mode_band_success(monkeypatch):
    rel = _write_image("scan/auto1.jpg", size=(100, 400))
    monkeypatch.setattr(cs, "detect_screenshot_features", lambda _path: {})
    monkeypatch.setattr(cs, "detect_photo_band", lambda _path: (40, 30))

    async with async_session():
        item, _ = await cs._scan_one_candidate(
            _direct_insp(rel), "auto", 0.03, 0.05, MIN_RATIO
        )
    assert item["crop_top"] == pytest.approx(40 / 400)
    assert item["crop_bottom"] == pytest.approx((400 - 1 - 30) / 400)
    assert item["auto_checked"] is True


async def test_scan_one_auto_mode_single_side_not_checked(monkeypatch):
    """只有单侧黑边：保留候选但不自动勾选（交人工判断）。"""
    rel = _write_image("scan/auto2.jpg", size=(100, 400))
    monkeypatch.setattr(cs, "detect_screenshot_features", lambda _path: {})
    monkeypatch.setattr(cs, "detect_photo_band", lambda _path: (40, 399))

    async with async_session():
        item, _ = await cs._scan_one_candidate(
            _direct_insp(rel), "auto", 0.03, 0.05, MIN_RATIO
        )
    assert item["auto_checked"] is False


async def test_scan_one_auto_mode_band_error_noted(monkeypatch):
    rel = _write_image("scan/auto3.jpg", size=(100, 400))
    monkeypatch.setattr(cs, "detect_screenshot_features", lambda _path: {})

    def _fail(_path):
        raise ValueError("未找到条带")

    monkeypatch.setattr(cs, "detect_photo_band", _fail)
    async with async_session():
        item, _ = await cs._scan_one_candidate(
            _direct_insp(rel), "auto", 0.03, 0.05, MIN_RATIO
        )
    assert item["auto_ok"] is False
    assert item["auto_checked"] is False
    assert item["note"] == "自动检测失败：未找到条带"


async def test_scan_one_feature_detection_error_falls_back_to_low(monkeypatch):
    rel = _write_image("scan/ratio1.jpg", size=(100, 400))

    def _boom(_path):
        raise RuntimeError("特征检测炸了")

    monkeypatch.setattr(cs, "detect_screenshot_features", _boom)
    async with async_session():
        item, _ = await cs._scan_one_candidate(
            _direct_insp(rel), "ratio", 0.05, 0.05, MIN_RATIO
        )
    assert item["confidence"] == "low"
    assert item["crop_top"] == 0.05


# ══════════════════════════════════════════════════════════════
#  crop_scan._apply_vlm_review：三态与超时
# ══════════════════════════════════════════════════════════════


def _candidate(index: int, rel_path: str | None = None, note: str | None = None) -> dict:
    return {
        "id": f"c{index}",
        "file_path": rel_path or "no/such.jpg",
        "note": note,
        "crop_top": 0.0,
    }


async def test_apply_vlm_review_keeps_missing_file_untouched(monkeypatch):
    candidate = _candidate(0, rel_path="no/such/file.jpg")

    async def _should_not_run(_path):
        raise AssertionError("文件不存在时不应调用 VLM")

    monkeypatch.setattr(cs, "_vlm_residue_review", _should_not_run)
    kept, hits, removed = await cs._apply_vlm_review([candidate], deadline=1e18)
    assert kept == [candidate]
    assert (hits, removed) == (0, 0)
    assert "vlm_residue" not in candidate


async def test_apply_vlm_review_positive_appends_note(monkeypatch):
    rel = _write_image("scan/vlm_pos.jpg", size=(100, 400))
    plain = _candidate(0, rel_path=rel)
    noted = _candidate(1, rel_path=rel, note="算法疑似")

    async def _positive(_path):
        return True

    monkeypatch.setattr(cs, "_vlm_residue_review", _positive)
    kept, hits, removed = await cs._apply_vlm_review([plain, noted], deadline=1e18)
    assert (hits, removed) == (2, 0)
    assert len(kept) == 2
    assert plain["note"] == "AI 复核：检出系统 UI 残留"
    assert noted["note"] == "算法疑似；AI 复核：检出系统 UI 残留"
    assert all(c["vlm_residue"] is True for c in kept)


async def test_apply_vlm_review_negative_removes_candidate(monkeypatch):
    rel = _write_image("scan/vlm_neg.jpg", size=(100, 400))

    async def _negative(_path):
        return False

    monkeypatch.setattr(cs, "_vlm_residue_review", _negative)
    kept, hits, removed = await cs._apply_vlm_review(
        [_candidate(0, rel_path=rel)], deadline=1e18
    )
    assert kept == []
    assert (hits, removed) == (0, 1)


async def test_apply_vlm_review_unknown_kept_with_annotation(monkeypatch):
    rel = _write_image("scan/vlm_unknown.jpg", size=(100, 400))

    async def _unknown(_path):
        return None

    monkeypatch.setattr(cs, "_vlm_residue_review", _unknown)
    kept, hits, removed = await cs._apply_vlm_review(
        [_candidate(0, rel_path=rel)], deadline=1e18
    )
    assert (hits, removed) == (0, 0)
    assert len(kept) == 1
    assert kept[0]["vlm_residue"] is None
    assert "AI 复核不可用" in kept[0]["note"]


async def test_apply_vlm_review_exception_treated_as_unknown(monkeypatch):
    rel = _write_image("scan/vlm_exc.jpg", size=(100, 400))

    async def _boom(_path):
        raise RuntimeError("VLM 炸了")

    monkeypatch.setattr(cs, "_vlm_residue_review", _boom)
    kept, hits, removed = await cs._apply_vlm_review(
        [_candidate(0, rel_path=rel)], deadline=1e18
    )
    assert (hits, removed) == (0, 0)
    assert kept[0]["vlm_residue"] is None


async def test_apply_vlm_review_deadline_keeps_rest_as_unknown(monkeypatch):
    """时间预算耗尽：剩余未复核候选按「未知」保留并标注。"""
    rel = _write_image("scan/vlm_deadline.jpg", size=(100, 400))
    candidates = [
        _candidate(0, rel_path=rel),
        _candidate(1, rel_path=rel),
        _candidate(2, rel_path=rel, note="算法疑似"),
    ]

    async def _unknown(_path):
        return None

    monkeypatch.setattr(cs, "_vlm_residue_review", _unknown)
    fake_time = type("FakeTime", (), {})()
    ticks = iter([0.0, 100.0])
    fake_time.monotonic = lambda: next(ticks)
    monkeypatch.setattr(cs, "time", fake_time)

    kept, hits, removed = await cs._apply_vlm_review(candidates, deadline=0.0)

    assert (hits, removed) == (0, 0)
    assert len(kept) == 3
    # 第 3 条未及复核 → 追加时间预算标注（保留已有 note）
    assert kept[2]["vlm_residue"] is None
    assert kept[2]["note"] == "算法疑似；未及 AI 复核（时间预算）"
