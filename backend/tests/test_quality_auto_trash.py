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


async def test_auto_trash_writes_three_fields(client, upload, monkeypatch):
    """开关打开 + 模型判 rejected → 三字段整组落库（软删除不变量）。"""
    from app.config import settings
    from app.database import async_session
    from app.models.inspiration import Inspiration
    from app.services.ai_service import quality as quality_mod

    monkeypatch.setattr(settings, "quality_auto_trash_rejected", True)

    async def fake_chat(image_data, prompt, model_cfg, temperature):
        return '{"is_outfit": false, "reason": "不是穿搭", "reason_code": "not_outfit"}', None

    monkeypatch.setattr(quality_mod, "_ollama_vision_chat", fake_chat)

    insp = upload().json()
    client.patch(f"/api/inspirations/{insp['id']}", json={"quality_status": "pending"})

    async with async_session() as db:
        status, _reason, _ai = await quality_mod.check_image_quality(
            db, insp["id"], insp["file_path"]
        )
    assert status == "rejected"

    async with async_session() as db:
        row = await db.get(Inspiration, insp["id"])
        # 三字段同真同假：不能只写一个
        assert row.deleted_at is not None
        assert row.trash_reason == "质量差"
        assert row.trash_source == "auto"
