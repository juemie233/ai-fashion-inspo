"""质量审核「自动入垃圾桶」开关（审查 P2）。

默认关闭（只标记不删除，素材仍可在「未通过素材」里人工翻案）。打开后新判 rejected 的素材
会**整组**写入 deleted_at / trash_reason / trash_source —— 三字段必须同真同假，
否则会破坏垃圾桶状态不变量。
"""

from app.config import settings
from app.services.ai_service.quality import _auto_trash_fields


def test_off_by_default(monkeypatch):
    monkeypatch.setattr(settings, "quality_auto_trash_rejected", False)
    assert _auto_trash_fields("rejected") == {}


def test_on_produces_all_three_fields(monkeypatch):
    monkeypatch.setattr(settings, "quality_auto_trash_rejected", True)
    files = _auto_trash_fields("rejected")
    assert set(files) == {"deleted_at", "trash_reason", "trash_source"}
    assert files["trash_reason"] == "质量差"
    assert files["trash_source"] == "auto"
    assert files["deleted_at"] is not None


def test_approved_never_trashed(monkeypatch):
    monkeypatch.setattr(settings, "quality_auto_trash_rejected", True)
    assert _auto_trash_fields("approved") == {}
