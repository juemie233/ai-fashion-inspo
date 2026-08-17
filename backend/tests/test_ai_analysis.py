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
        # 默认：完整穿搭分析
        return '{"style": ["法式"], "items": [{"type": "连衣裙"}], "dominant_colors": ["#FFFFFF"]}'


@pytest.fixture
def fake_ollama(monkeypatch):
    """把 httpx.AsyncClient 替换为 FakeOllamaClient（服务内函数均运行时 import httpx）。"""
    monkeypatch.setattr(httpx, "AsyncClient", FakeOllamaClient)
    FakeOllamaClient.outfit_is_outfit = True  # 每个用例重置


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
