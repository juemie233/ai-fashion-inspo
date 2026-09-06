"""sock_verify（高风险袜类二次验证）单元测试。

覆盖：
- 风险识别（过膝/大腿/长筒袜命中；连裤袜/中筒/短袜/堆堆袜跳过）
- 验证判定解析
- 各判定下的改写语义（连裤袜降级/无袜子删除/过膝袜保持/不确定保持）
- 验证调用失败时保守保持原样
- 无风险袜类时不触发裁剪
"""

from app.services.ai_service import sock_verify
from app.services.ai_service.sock_verify import (
    _parse_verdict,
    find_risky_sock_types,
    maybe_verify_socks,
)

MODEL_CFG = {"timeout": 30, "num_predict": 512, "num_ctx": 8192}

TAGS_BLACK_KNEE = {
    "items": [
        {"type": "黑色过膝袜", "color": "黑色", "features": []},
        {"type": "百褶短裙", "color": "黑色", "features": []},
    ]
}
TAGS_SAFE_ONLY = {
    "items": [
        {"type": "黑色连裤袜", "color": "黑色", "features": []},
        {"type": "白色堆堆袜", "color": "白色", "features": []},
        {"type": "黑色吊带袜", "color": "黑色", "features": []},
    ]
}


def test_find_risky_hits_knee_thigh_ankle():
    """过膝袜/大腿袜/长筒袜命中；安全袜类不命中。"""
    assert find_risky_sock_types(TAGS_BLACK_KNEE) == ["黑色过膝袜"]
    assert find_risky_sock_types(
        {"items": [{"type": "白色大腿袜", "color": "白色", "features": []}]}
    ) == ["白色大腿袜"]
    assert find_risky_sock_types(
        {"items": [{"type": "黑色长筒袜", "color": "黑色", "features": []}]}
    ) == ["黑色长筒袜"]
    # 安全袜类：连裤/中筒/短袜/堆堆/吊带袜 不触发
    assert find_risky_sock_types(TAGS_SAFE_ONLY) == []


def test_parse_verdict():
    """验证答案解析：合法值通过，脏输出/越界词返回 None（保守保持原样）。"""
    assert _parse_verdict('{"sock": "连裤袜"}') == "连裤袜"
    assert _parse_verdict('{"sock":"无袜子"}') == "无袜子"
    assert _parse_verdict('{"sock": "不确定"}') == "不确定"
    assert _parse_verdict("不好意思，我看图后觉得是连裤袜") is None
    assert _parse_verdict('{"sock": "水手服"}') is None
    assert _parse_verdict("") is None


async def _verify(monkeypatch, verdict: str):
    """构造 maybe_verify_socks 调用：裁剪与定向模型调用打桩。"""
    async def _fake_crop(_file, *_a, **_k):
        return "crop-base64"

    async def _fake_call(_img, _prompt, _cfg, _size, _iid, _model):
        return f'{{"sock": "{verdict}"}}'

    monkeypatch.setattr(sock_verify, "_crop_lower_body", _fake_crop)
    monkeypatch.setattr(
        "app.services.ai_service.analyze._call_ollama_vision", _fake_call
    )
    return await maybe_verify_socks("images/x.jpg", TAGS_BLACK_KNEE, MODEL_CFG, "m", "insp-1")


async def test_verdict_pantyhose_downgrades(monkeypatch):
    """判定连裤袜 → 黑色过膝袜 改写为 黑色连裤袜。"""
    out = await _verify(monkeypatch, "连裤袜")
    types = [it["type"] for it in out["items"]]
    assert types == ["黑色连裤袜", "百褶短裙"]


async def test_verdict_no_sock_removes_item(monkeypatch):
    """判定无袜子 → 删除该袜类 item（保留其他单品）。"""
    out = await _verify(monkeypatch, "无袜子")
    assert [it["type"] for it in out["items"]] == ["百褶短裙"]


async def test_verdict_knee_keeps_original(monkeypatch):
    """判定过膝袜（确认膝上袜口证据）→ 保持原样。"""
    out = await _verify(monkeypatch, "过膝袜")
    assert [it["type"] for it in out["items"]] == ["黑色过膝袜", "百褶短裙"]


async def test_verdict_uncertain_keeps_original(monkeypatch):
    """判定不确定 → 保守保持原样。"""
    out = await _verify(monkeypatch, "不确定")
    assert [it["type"] for it in out["items"]] == ["黑色过膝袜", "百褶短裙"]


async def test_verify_call_failure_keeps_original(monkeypatch):
    """定向调用抛错 → 静默按原结果保存（不阻断分析）。"""
    async def _fake_crop(_file, *_a, **_k):
        return "crop-base64"

    async def _fake_call(*_a, **_k):
        raise RuntimeError("ollama down")

    monkeypatch.setattr(sock_verify, "_crop_lower_body", _fake_crop)
    monkeypatch.setattr(
        "app.services.ai_service.analyze._call_ollama_vision", _fake_call
    )
    out = await maybe_verify_socks(
        "images/x.jpg", TAGS_BLACK_KNEE, MODEL_CFG, "m", "insp-1"
    )
    assert [it["type"] for it in out["items"]] == ["黑色过膝袜", "百褶短裙"]


async def test_no_risky_socks_skips_crop(monkeypatch):
    """无风险袜类：不裁剪、不调用验证，直接返回原数据。"""
    called = False

    async def _fake_crop(*_a, **_k):
        nonlocal called
        called = True
        return "crop-base64"

    monkeypatch.setattr(sock_verify, "_crop_lower_body", _fake_crop)
    out = await maybe_verify_socks(
        "images/x.jpg", TAGS_SAFE_ONLY, MODEL_CFG, "m", "insp-1"
    )
    assert out is TAGS_SAFE_ONLY
    assert called is False
