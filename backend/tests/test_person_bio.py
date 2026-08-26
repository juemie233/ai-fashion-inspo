"""人物简介生成（根据标签调用本地大模型）集成测试。

模拟 Ollama（替换 httpx.AsyncClient），覆盖：
- 博主/模特有标签 → 生成成功返回 bio
- 无标签 → 400 明确提示
- 人物不存在 → 404
- Ollama 连接失败 → 502
- Prompt 管理接口：GET 默认 / PUT 保存 / DELETE 恢复默认 / 空值 400

person_bio_prompt.json 为运行时文件，测试通过 monkeypatch 重定向到临时目录，
避免污染真实 backend/person_bio_prompt.json。
"""

import httpx
import pytest


class FakeResponse:
    """模拟 httpx.Response 的最小接口。"""

    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "http error", request=None, response=self  # type: ignore[arg-type]
            )


class FakeOllamaClient:
    """模拟 httpx.AsyncClient：/api/chat 返回预设的人物简介文本。"""

    # 连接失败模拟：为 True 时 post 抛 ConnectError
    connect_error = False

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def post(self, url, json=None, **kwargs):
        if self.connect_error:
            raise httpx.ConnectError("connection refused")
        if url.endswith("/api/chat"):
            return FakeResponse(
                200,
                {"message": {"content": "这是一位擅长法式穿搭的博主，偏爱连衣裙与白色系。"}},
            )
        return FakeResponse(404, {})


@pytest.fixture
def fake_ollama(monkeypatch, tmp_path):
    """替换 httpx.AsyncClient，并把 person_bio_prompt.json 重定向到临时目录。"""
    from app.services import person_bio_prompt

    monkeypatch.setattr(httpx, "AsyncClient", FakeOllamaClient)
    monkeypatch.setattr(person_bio_prompt, "_PROMPT_FILE", tmp_path / "person_bio_prompt.json")
    FakeOllamaClient.connect_error = False  # 每个用例重置


def _upload_with_tags(client, upload, create_blogger, blogger_id: int) -> None:
    """上传一张素材 → 关联博主 → 打标签，构造「有标签」前置。"""
    insp_id = upload().json()["id"]
    r = client.post(
        f"/api/inspirations/{insp_id}/bloggers", json={"person_ids": [blogger_id]}
    )
    assert r.status_code == 200
    client.post(
        f"/api/inspirations/{insp_id}/tags",
        json={"names": ["法式", "连衣裙", "白色系"], "category": "style"},
    )


# ═══════════════════════════════════════════════════════════════
#  生成接口（/api/bloggers/{id}/generate-bio 与 /api/models/{id}/generate-bio）
# ═══════════════════════════════════════════════════════════════


def test_generate_blogger_bio_success(client, create_blogger, upload, fake_ollama):
    """博主有标签：生成成功，返回非空简介文本。"""
    blogger = create_blogger(name="法式博主")
    _upload_with_tags(client, upload, create_blogger, blogger["id"])

    r = client.post(f"/api/bloggers/{blogger['id']}/generate-bio")
    assert r.status_code == 200
    bio = r.json()["bio"]
    assert isinstance(bio, str) and len(bio) >= 4


def test_generate_model_bio_success(client, create_model, upload, fake_ollama):
    """模特有标签：生成成功（与博主共用同一实现）。"""
    model = create_model(name="职业模特A")
    insp_id = upload().json()["id"]
    r = client.post(f"/api/inspirations/{insp_id}/models", json={"person_ids": [model["id"]]})
    assert r.status_code == 200
    client.post(
        f"/api/inspirations/{insp_id}/tags",
        json={"names": ["高定", "台步", "杂志风"], "category": "style"},
    )

    r = client.post(f"/api/models/{model['id']}/generate-bio")
    assert r.status_code == 200
    assert len(r.json()["bio"]) >= 4


def test_generate_bio_no_tags_400(client, create_blogger, fake_ollama):
    """博主无标签：返回 400 与明确中文提示（不调用模型）。"""
    blogger = create_blogger(name="无标签博主")

    r = client.post(f"/api/bloggers/{blogger['id']}/generate-bio")
    assert r.status_code == 400
    assert "还没有任何标签" in r.json()["detail"]


def test_generate_bio_blogger_missing_404(client, fake_ollama):
    """博主不存在：返回 404。"""
    r = client.post("/api/bloggers/999999/generate-bio")
    assert r.status_code == 404


def test_generate_bio_model_missing_404(client, fake_ollama):
    """模特不存在：返回 404。"""
    r = client.post("/api/models/999999/generate-bio")
    assert r.status_code == 404


def test_generate_bio_ollama_down_502(client, create_blogger, upload, fake_ollama):
    """Ollama 不可达：返回 502 与连接失败提示。"""
    blogger = create_blogger(name="离线博主")
    _upload_with_tags(client, upload, create_blogger, blogger["id"])
    FakeOllamaClient.connect_error = True

    r = client.post(f"/api/bloggers/{blogger['id']}/generate-bio")
    assert r.status_code == 502
    assert "Ollama" in r.json()["detail"]


# ═══════════════════════════════════════════════════════════════
#  Prompt 管理接口（/api/ai/person-bio-prompt）
# ═══════════════════════════════════════════════════════════════


def test_person_bio_prompt_get_default(client, fake_ollama):
    """未配置时 GET 返回默认模板，is_default=True。"""
    r = client.get("/api/ai/person-bio-prompt")
    assert r.status_code == 200
    data = r.json()
    assert data["is_default"] is True
    assert "{kind}" in data["prompt"]
    assert data["default"] == data["prompt"]


def test_person_bio_prompt_put_and_get(client, fake_ollama):
    """PUT 保存后 GET 返回自定义内容，is_default=False。"""
    custom = "请为{kind}「{name}」写一段 30 字以内的简介，平台：{platform}。"
    r = client.put("/api/ai/person-bio-prompt", json={"prompt": custom})
    assert r.status_code == 200
    assert "已更新" in r.json()["message"]

    r = client.get("/api/ai/person-bio-prompt")
    assert r.status_code == 200
    data = r.json()
    assert data["prompt"] == custom
    assert data["is_default"] is False


def test_person_bio_prompt_put_empty_400(client, fake_ollama):
    """PUT 空/空白 Prompt：返回 400。"""
    assert client.put("/api/ai/person-bio-prompt", json={"prompt": ""}).status_code == 400
    assert client.put("/api/ai/person-bio-prompt", json={"prompt": "   "}).status_code == 400


def test_person_bio_prompt_delete_restores_default(client, fake_ollama):
    """DELETE 恢复默认：GET 回到默认模板。"""
    client.put("/api/ai/person-bio-prompt", json={"prompt": "自定义内容{name}"})
    r = client.delete("/api/ai/person-bio-prompt")
    assert r.status_code == 200
    assert "已恢复默认" in r.json()["message"]

    r = client.get("/api/ai/person-bio-prompt")
    assert r.status_code == 200
    assert r.json()["is_default"] is True


def test_person_bio_prompt_invalid_placeholder_502(
    client, create_blogger, upload, fake_ollama
):
    """自定义 Prompt 含未知占位符：生成时返回 502 并提示修正模板。"""
    client.put("/api/ai/person-bio-prompt", json={"prompt": "未知占位符 {unknown_field}"})
    blogger = create_blogger(name="占位符博主")
    _upload_with_tags(client, upload, create_blogger, blogger["id"])

    r = client.post(f"/api/bloggers/{blogger['id']}/generate-bio")
    assert r.status_code == 502
    assert "占位符" in r.json()["detail"]
