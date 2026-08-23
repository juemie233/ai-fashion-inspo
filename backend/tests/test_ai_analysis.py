"""AI 分析与质量审核核心链路集成测试（模拟 Ollama，不依赖本地模型）。

复用 test_ai_models 的 FakeAsyncClient 思路，但按 /api/chat 的 prompt 内容
路由到不同预设输出，覆盖：
- 完整穿搭分析（analyze_image 保存标签 + 写日志）
- 质量审核二分类（approved / rejected）
- 穿搭大标签建议（只建议不入库）
- 质量统计口径、批量审核/重审任务创建
"""

import httpx
import pytest

from app.database import async_session
from app.models.inspiration import Inspiration
from app.models.tag import InspirationTag, Tag


class FakeResponse:
    """模拟 httpx.Response 的最小接口。"""

    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class FakeOllamaClient:
    """模拟 httpx.AsyncClient：按 /api/chat 的 prompt 内容返回预设模型输出。"""

    # 穿搭二分类结果，测试可覆盖（rejected 用例置为 False）
    outfit_is_outfit = True
    # 穿搭分析输出覆盖（重分析测试用）；None 时用内置默认
    analysis_override: str | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def post(self, url, json=None, **kwargs):
        if url.endswith("/api/chat"):
            content = self._content_for(json or {})
            return FakeResponse(
                200, {"message": {"content": content}, "done_reason": "stop"}
            )
        return FakeResponse(404, {})

    @classmethod
    def _content_for(cls, body):
        try:
            prompt = body["messages"][0]["content"]
        except (KeyError, IndexError, TypeError):
            prompt = ""
        if "穿搭标签总结" in prompt:
            return '{"outfit_tags": ["法式连衣裙穿搭", "白色系穿搭"]}'
        if "完整的真人穿搭照片" in prompt:
            verdict = "true" if cls.outfit_is_outfit else "false"
            return f'{{"is_outfit": {verdict}, "reason": "穿搭照片"}}'
        if "疑似由 AI 生成" in prompt:
            return '{"is_ai_generated": false, "confidence": 0.1}'
        # 默认：完整穿搭分析（测试可覆盖 analysis_override 返回不同标签集）
        if cls.analysis_override is not None:
            return cls.analysis_override
        return '{"style": ["法式"], "items": [{"type": "连衣裙"}], "dominant_colors": ["#FFFFFF"]}'


@pytest.fixture
def fake_ollama(monkeypatch):
    """把 httpx.AsyncClient 替换为 FakeOllamaClient（服务内函数均运行时 import httpx）。"""
    monkeypatch.setattr(httpx, "AsyncClient", FakeOllamaClient)
    FakeOllamaClient.outfit_is_outfit = True  # 每个用例重置
    FakeOllamaClient.analysis_override = None


async def _inspiration_tags(inspiration_id: str) -> dict[str, str]:
    """返回素材当前标签 {标签名: 关联 source}。"""
    async with async_session() as db:
        from sqlalchemy import select

        rows = await db.execute(
            select(Tag.name, InspirationTag.source)
            .join(Tag, Tag.id == InspirationTag.tag_id)
            .where(InspirationTag.inspiration_id == inspiration_id)
        )
        return {name: source for name, source in rows.all()}


async def test_reanalysis_replaces_ai_tags_keeps_tag_rows(
    client, upload, fake_ollama
):
    """重新分析：旧 AI 标签关联被清除、新 AI 标签生效；标签本身不删除。"""
    from app.services.ai_service.analyze import analyze_image

    insp = upload().json()
    # 第一次分析：法式 + 连衣裙
    async with async_session() as db:
        assert await analyze_image(db, insp["id"], insp["file_path"]) is True
    tags1 = await _inspiration_tags(insp["id"])
    assert {"法式", "连衣裙"} <= set(tags1)
    assert all(s == "ai_generated" for s in tags1.values())

    # 第二次分析：日系 + 百褶裙（完全不同的标签集）
    FakeOllamaClient.analysis_override = (
        '{"style": ["日系"], "items": [{"type": "百褶裙"}]}'
    )
    async with async_session() as db:
        assert await analyze_image(db, insp["id"], insp["file_path"]) is True

    tags2 = await _inspiration_tags(insp["id"])
    # 旧 AI 关联消失，新 AI 关联出现
    assert "法式" not in tags2 and "连衣裙" not in tags2
    assert "日系" in tags2 and "百褶裙" in tags2
    assert all(s == "ai_generated" for s in tags2.values())

    # 旧标签本身仍保留在 tags 表中（未被删除，仅解除关联）
    async with async_session() as db:
        from sqlalchemy import func, select

        orphan = await db.execute(
            select(func.count()).select_from(Tag).where(Tag.name.in_(["法式", "连衣裙"]))
        )
        assert orphan.scalar() == 2


async def test_reanalysis_preserves_manual_and_seed_tags(
    client, upload, fake_ollama
):
    """重新分析只清 AI 关联：手动标签与种子标签关联保留。"""
    from app.services.ai_service.analyze import analyze_image

    insp = upload().json()
    # 首次分析产生 AI 标签
    async with async_session() as db:
        await analyze_image(db, insp["id"], insp["file_path"])
    # 手动添加一个标签（source=manual 关联）
    client.post(f"/api/inspirations/{insp['id']}/tags", json={"names": ["手动标签"]})
    # 预置一个种子标签并关联（模拟种子标签关联 source=manual）
    async with async_session() as db:
        from sqlalchemy import select

        from app.models.tag import InspirationTag

        seed = (
            await db.execute(select(Tag).where(Tag.name == "法式"))
        ).scalar_one_or_none()
        # 法式 已作为 AI 关联存在；额外造一个种子标签并手动关联
        seed_tag = Tag(name="种子测试标签", category="free", source="seed")
        db.add(seed_tag)
        await db.flush()
        db.add(
            InspirationTag(
                inspiration_id=insp["id"], tag_id=seed_tag.id, confidence=1.0,
                source="manual",
            )
        )
        await db.commit()

    before = await _inspiration_tags(insp["id"])
    assert "手动标签" in before and "种子测试标签" in before

    # 重新分析
    FakeOllamaClient.analysis_override = '{"style": ["街头"]}'
    async with async_session() as db:
        await analyze_image(db, insp["id"], insp["file_path"])

    after = await _inspiration_tags(insp["id"])
    # 手动标签与种子标签关联保留
    assert "手动标签" in after and after["手动标签"] == "manual"
    assert "种子测试标签" in after and after["种子测试标签"] == "manual"
    # 旧 AI 标签 法式/连衣裙 被替换
    assert "法式" not in after
    # 新 AI 标签生效
    assert after.get("街头") == "ai_generated"


async def test_reanalysis_manual_same_name_not_overwritten(
    client, upload, fake_ollama
):
    """手动关联与 AI 同名标签时，该关联保持 manual，重分析不被误删为 AI。"""
    from app.services.ai_service.analyze import analyze_image

    insp = upload().json()
    # 先手动关联「法式」（此时标签由 get_or_create_tag 以 manual 创建）
    client.post(f"/api/inspirations/{insp['id']}/tags", json={"names": ["法式"]})
    before = await _inspiration_tags(insp["id"])
    assert before.get("法式") == "manual"

    # AI 分析也产出「法式」：既有 manual 关联不应被覆盖成 ai_generated，
    # 且由于 clear_ai_tags 只删 AI 关联，手动关联保留
    async with async_session() as db:
        await analyze_image(db, insp["id"], insp["file_path"])
    after = await _inspiration_tags(insp["id"])
    assert after.get("法式") == "manual"  # 仍是手动，不被覆盖/删除


async def test_multiple_reanalyses_keep_only_latest(client, upload, fake_ollama):
    """多次重新分析：每次只保留最后一次的 AI 标签（幂等替换，不累积）。"""
    from app.services.ai_service.analyze import analyze_image

    insp = upload().json()
    outputs = [
        '{"style": ["法式"]}',
        '{"style": ["日系"]}',
        '{"style": ["街头"]}',
    ]
    for out in outputs:
        FakeOllamaClient.analysis_override = out
        async with async_session() as db:
            await analyze_image(db, insp["id"], insp["file_path"])

    after = await _inspiration_tags(insp["id"])
    assert after == {"街头": "ai_generated"}  # 只留最后一次，无累积


async def test_failed_analysis_keeps_existing_ai_tags(client, upload, fake_ollama):
    """分析失败（无法解析）时不清除既有 AI 标签，避免半更新/误删。"""
    from app.services.ai_service.analyze import analyze_image

    insp = upload().json()
    async with async_session() as db:
        await analyze_image(db, insp["id"], insp["file_path"])
    assert "法式" in await _inspiration_tags(insp["id"])

    # 返回无法解析为分析 JSON 的内容（保存路径不进入，旧标签保留）
    FakeOllamaClient.analysis_override = "抱歉，我无法分析这张图片。"
    async with async_session() as db:
        success = await analyze_image(db, insp["id"], insp["file_path"])

    assert success is False
    after = await _inspiration_tags(insp["id"])
    assert "法式" in after  # 既有 AI 标签未被清空


async def test_analyze_image_saves_tags(client, upload, fake_ollama):
    """完整分析：调用（模拟）视觉模型后保存标签并写分析日志。"""
    from app.services.ai_service.analyze import analyze_image

    insp = upload().json()
    async with async_session() as db:
        success = await analyze_image(db, insp["id"], insp["file_path"])

    assert success is True
    detail = client.get(f"/api/inspirations/{insp['id']}").json()
    names = {t["tag"]["name"] for t in detail["tags"]}
    assert "法式" in names
    assert detail["analysis_status"] == "done"


async def test_check_image_quality_approves(client, upload, fake_ollama):
    """质量审核：穿搭二分类通过 → approved，非 AI。"""
    from app.services.ai_service.quality import check_image_quality

    insp = upload().json()
    client.patch(f"/api/inspirations/{insp['id']}", json={"quality_status": "pending"})

    async with async_session() as db:
        status, _reason, ai = await check_image_quality(
            db, insp["id"], insp["file_path"]
        )

    assert status == "approved"
    assert ai is False
    async with async_session() as db:
        saved = await db.get(Inspiration, insp["id"])
        assert saved.quality_status == "approved"


async def test_check_image_quality_rejects(client, upload, fake_ollama):
    """质量审核：非穿搭内容 → rejected。"""
    from app.services.ai_service.quality import check_image_quality

    FakeOllamaClient.outfit_is_outfit = False
    insp = upload().json()
    client.patch(f"/api/inspirations/{insp['id']}", json={"quality_status": "pending"})

    async with async_session() as db:
        status, _reason, _ai = await check_image_quality(
            db, insp["id"], insp["file_path"]
        )

    assert status == "rejected"
    async with async_session() as db:
        saved = await db.get(Inspiration, insp["id"])
        assert saved.quality_status == "rejected"


def test_outfit_tags_suggest(client, upload, fake_ollama):
    """大标签建议：根据小标签调用（模拟）模型，返回建议（只建议不入库）。"""
    insp = upload().json()
    client.post(f"/api/inspirations/{insp['id']}/tags", json={"names": ["法式", "白色"]})

    r = client.post("/api/ai/outfit-tags/suggest", params={"inspiration_id": insp["id"]})
    assert r.status_code == 200
    data = r.json()
    assert "法式连衣裙穿搭" in data["suggestions"]
    assert set(data["small_tags"]) == {"法式", "白色"}


def test_outfit_tags_suggest_no_tags(client, upload, fake_ollama):
    """无小标签时直接返回空建议，不调用模型。"""
    insp = upload().json()
    r = client.post("/api/ai/outfit-tags/suggest", params={"inspiration_id": insp["id"]})
    assert r.status_code == 200
    assert r.json()["suggestions"] == []


def test_quality_stats(client, upload):
    """质量审核统计口径：pending/approved/rejected 计数与通过率。"""
    upload()  # manual_upload 默认 approved
    b = upload().json()["id"]
    c = upload().json()["id"]
    client.patch(f"/api/inspirations/{b}", json={"quality_status": "pending"})
    client.patch(f"/api/inspirations/{c}", json={"quality_status": "rejected"})

    data = client.get("/api/ai/quality-stats").json()
    assert data["approved"] == 1
    assert data["pending"] == 1
    assert data["rejected"] == 1
    assert data["total"] == 3
    assert data["pass_rate"] == 50.0


def test_quality_check_creates_task(client, upload):
    """批量审核：创建任务记录并返回 task_id（无 Ollama 依赖）。"""
    a = upload().json()["id"]
    client.patch(f"/api/inspirations/{a}", json={"quality_status": "pending"})

    r = client.post("/api/ai/quality-check", params={"limit": 50})
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 1
    assert data["task_id"] is not None


def test_quality_recheck_resets_approved(client, upload):
    """重新审核：approved 重置为 pending 后提交任务。"""
    upload()  # approved
    r = client.post("/api/ai/quality-recheck")
    assert r.status_code == 200
    data = r.json()
    assert data["count"] >= 1
    assert data["task_id"] is not None


def test_quality_rejected_to_trash(client, upload):
    """已拒绝素材批量移入垃圾桶（软删除）：列表移除、reason=质量差、可恢复。"""
    a = upload().json()["id"]
    b = upload().json()["id"]
    client.patch(f"/api/inspirations/{b}", json={"quality_status": "rejected"})

    # 批量移入垃圾桶
    r = client.delete("/api/inspirations/quality-rejected")
    assert r.status_code == 200
    data = r.json()
    assert data["trashed"] == 1
    assert data["message"]

    # 已拒绝素材从正常列表消失，且出现在垃圾桶中（reason=质量差，供负样本学习）
    assert client.get("/api/inspirations", params={"quality_status": "rejected"}).json()["total"] == 0
    trash = client.get("/api/inspirations/trash").json()
    assert trash["total"] == 1
    item = trash["items"][0]
    assert item["id"] == b
    assert item["trash_reason"] == "质量差"

    # 再次调用：无已拒绝素材 → trashed=0（幂等）
    r2 = client.delete("/api/inspirations/quality-rejected")
    assert r2.status_code == 200
    assert r2.json()["trashed"] == 0

    # 素材仍可恢复（非物理删除）
    r3 = client.post(f"/api/inspirations/{b}/restore")
    assert r3.status_code == 200
    assert client.get(f"/api/inspirations/{b}").status_code == 200


async def test_analysis_history_excludes_trash_materials(client, upload, fake_ollama):
    """移入垃圾桶后历史不再显示该素材日志。"""
    from app.services.ai_service.analyze import analyze_image

    # 创建素材并执行分析
    insp = upload().json()
    async with async_session() as db:
        await analyze_image(db, insp["id"], insp["file_path"])

    # 验证历史中存在该素材的分析记录
    r = client.get("/api/ai/history", params={"inspiration_id": insp["id"]})
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 1
    assert any(log["inspiration_id"] == insp["id"] for log in data["items"])

    # 将该素材移入垃圾桶
    client.post(f"/api/inspirations/{insp['id']}/trash", json={"reason": "不喜欢"})

    # 再次查询历史：不应再显示该素材的分析记录
    r2 = client.get("/api/ai/history", params={"inspiration_id": insp["id"]})
    assert r2.status_code == 200
    data2 = r2.json()
    assert not any(log["inspiration_id"] == insp["id"] for log in data2["items"])

    # 恢复后应重新出现在历史中
    client.post(f"/api/inspirations/{insp['id']}/restore")
    r3 = client.get("/api/ai/history", params={"inspiration_id": insp["id"]})
    assert r3.status_code == 200
    data3 = r3.json()
    assert any(log["inspiration_id"] == insp["id"] for log in data3["items"])
