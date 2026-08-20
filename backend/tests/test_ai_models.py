"""AI 模型管理端点测试：状态、嵌入模型、配置重置、统计口径（模拟 Ollama）。

- 所有 Ollama 调用通过 FakeAsyncClient 模拟，不依赖本地 Ollama
- .env 写入被替换为空实现，避免污染真实 backend/.env
- model_configs.json 重定向到临时目录，避免污染真实模型配置
"""

import pytest

TAGS_PAYLOAD = {
    "models": [
        {"name": "qwen3-vl:8b-instruct", "size": 8000000000, "modified_at": "2025-01-01T00:00:00Z"},
        # 真实 Ollama 对无 tag 模型返回 :latest 后缀，配置里的 all-minilm 需与之等价匹配
        {"name": "all-minilm:latest", "size": 80000000, "modified_at": "2025-01-02T00:00:00Z"},
        {"name": "llava:7b", "size": 4000000000, "modified_at": "2025-01-03T00:00:00Z"},
    ]
}


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


class FakeAsyncClient:
    """模拟 httpx.AsyncClient：按 URL 返回预设响应。"""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def get(self, url):
        if url.endswith("/api/tags"):
            return FakeResponse(200, TAGS_PAYLOAD)
        if url.endswith("/api/ps"):
            return FakeResponse(200, {"models": []})
        if url.endswith("/api/version"):
            return FakeResponse(200, {"version": "0.5.7"})
        return FakeResponse(404)

    async def delete(self, url):
        return FakeResponse(200, {})

    async def post(self, url, **kwargs):
        return FakeResponse(200, {})


async def _noop_update_env(updates: dict) -> None:
    """替换 _update_env_file：测试不写真实 .env。"""


@pytest.fixture(autouse=True)
def fake_ollama(monkeypatch, tmp_path):
    """模拟 Ollama 与持久化副作用，隔离真实配置文件。"""
    import app.routers.ai_models as models_mod
    import app.routers.ai_settings as settings_mod
    from app.config import settings
    from app.services import model_config

    monkeypatch.setattr(models_mod.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(models_mod, "_update_env_file", _noop_update_env)
    monkeypatch.setattr(settings_mod, "_update_env_file", _noop_update_env)
    # model_configs.json 重定向到临时目录
    monkeypatch.setattr(model_config, "_CONFIG_FILE", tmp_path / "model_configs.json")
    # 固定嵌入模型配置：避免本地 .env 的 OLLAMA_EMBEDDING_MODEL（如带 :latest 后缀）
    # 与测试断言（all-minilm）不一致导致环境相关失败（CI 无 .env 时为默认值）
    monkeypatch.setattr(settings, "ollama_embedding_model", "all-minilm")


def test_list_models_marks_embedding(client):
    r = client.get("/api/ai/models")
    assert r.status_code == 200
    data = r.json()
    assert data["active_model"] == "qwen3-vl:8b-instruct"
    assert data["embedding_model"] == "all-minilm"
    by_name = {m["name"]: m for m in data["models"]}
    # 配置的 all-minilm 与实际安装的 all-minilm:latest 等价，应正确标记为嵌入模型
    assert by_name["all-minilm:latest"]["is_embedding"] is True
    assert by_name["qwen3-vl:8b-instruct"]["is_active"] is True
    assert by_name["llava:7b"]["is_embedding"] is False
    assert by_name["llava:7b"]["is_active"] is False


def test_ai_status(client):
    r = client.get("/api/ai/status")
    assert r.status_code == 200
    data = r.json()
    assert data["ollama_connected"] is True
    assert data["ollama_version"] == "0.5.7"
    assert data["active_model"] == "qwen3-vl:8b-instruct"
    assert data["embedding_model"] == "all-minilm"


def test_set_embedding_model(client):
    from app.config import settings

    original = settings.ollama_embedding_model
    try:
        r = client.put("/api/ai/models/embedding-active", params={"model_name": "all-minilm"})
        assert r.status_code == 200
        assert r.json()["embedding_model"] == "all-minilm"
        assert settings.ollama_embedding_model == "all-minilm"
    finally:
        settings.ollama_embedding_model = original


def test_set_embedding_model_not_installed(client):
    r = client.put("/api/ai/models/embedding-active", params={"model_name": "nonexistent"})
    assert r.status_code == 404


def test_settings_timeout_reset_flow(client):
    """超时按模型覆盖 → 清除自定义配置 → 回退全局默认值。"""
    from app.config import settings

    data = client.get("/api/ai/settings").json()
    assert data["defaults"]["analysis_timeout"] == settings.ai_analysis_timeout

    r = client.put("/api/ai/settings", params={"analysis_timeout": 120})
    assert r.status_code == 200
    assert client.get("/api/ai/settings").json()["analysis_timeout"] == 120

    r = client.delete("/api/ai/model-config")
    assert r.status_code == 200
    assert client.get("/api/ai/settings").json()["analysis_timeout"] == settings.ai_analysis_timeout


def test_sampling_params_includes_defaults(client):
    """采样参数响应携带后端全局默认值（前端「恢复默认」的数据源）。"""
    from app.config import settings

    data = client.get("/api/ai/sampling-params").json()
    assert data["defaults"]["num_predict"] == settings.ai_num_predict
    assert data["defaults"]["num_ctx"] == settings.ai_num_ctx
    assert data["defaults"]["temperature"] == settings.ai_temperature


def test_model_stats_avg_tags_snapshot_only(client, upload):
    """平均标签数只统计成功分析日志的结构化快照，混入灵感全量标签/失败日志/审核日志不参与。"""
    import sqlite3

    from app.config import settings

    # 上传两个真实素材，拿到素材 ID
    insp_a = upload().json()["id"]
    insp_b = upload().json()["id"]

    conn = sqlite3.connect(str(settings.storage_root.parent / "fashion_inspo.db"))
    try:
        cur = conn.cursor()
        # 60 个标签：前 10 个用于快照，全部用于灵感全量标签「污染」
        for i in range(60):
            cur.execute(
                "INSERT INTO tags (name, category, source, pinned, sort_order) VALUES (?, 'free', 'seed', 0, 0)",
                (f"测试标签{i}",),
            )
        cur.execute("SELECT id FROM tags ORDER BY id LIMIT 60")
        tag_ids = [row[0] for row in cur.fetchall()]
        # 旧口径会把灵感全量标签（此处 60 条）算进平均标签数
        for tid in tag_ids:
            cur.execute(
                "INSERT INTO inspiration_tags (inspiration_id, tag_id, confidence) VALUES (?, ?, 1.0)",
                (insp_a, tid),
            )

        def add_log(model, error, ms, n_tags):
            cur.execute(
                "INSERT INTO ai_analysis_log (inspiration_id, model_name, log_type, error, processing_time_ms) "
                "VALUES (?, ?, 'analysis', ?, ?)",
                (insp_a, model, error, ms),
            )
            log_id = cur.lastrowid
            for tid in tag_ids[:n_tags]:
                cur.execute(
                    "INSERT INTO ai_extracted_tags (log_id, tag_id, confidence) VALUES (?, ?, 0.9)",
                    (log_id, tid),
                )
            return log_id

        # 模型 A：2 成功（3+5 标签）+ 1 失败（10 标签快照不计入）
        add_log("qwen3-vl:8b-instruct", None, 100, 3)
        add_log("qwen3-vl:8b-instruct", None, 300, 5)
        add_log("qwen3-vl:8b-instruct", "解析失败", 200, 10)
        # 模型 B：1 成功（2 标签）
        add_log("llava:7b", None, 150, 2)
        # 质量审核日志不参与统计
        cur.execute(
            "INSERT INTO ai_analysis_log (inspiration_id, model_name, log_type, error) "
            "VALUES (?, 'qwen3-vl:8b-instruct', 'quality_check', NULL)",
            (insp_b,),
        )
        conn.commit()
    finally:
        conn.close()

    data = client.get("/api/ai/model-stats").json()
    by_name = {m["model_name"]: m for m in data["models"]}
    a = by_name["qwen3-vl:8b-instruct"]
    assert a["total_analyses"] == 3
    assert a["success_count"] == 2
    assert a["failure_count"] == 1
    assert a["avg_tags"] == 4.0  # (3+5)/2
    assert by_name["llava:7b"]["avg_tags"] == 2.0

    summary = data["models"][0]
    assert summary["model_name"] == "（全部模型汇总）"
    assert data["total_analyses"] == 4  # 不含 quality_check 日志
    assert summary["success_count"] == 3
    assert summary["avg_tags"] == round(10 / 3, 1)  # 10 条快照 / 3 次成功
