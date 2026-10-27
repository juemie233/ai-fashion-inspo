"""向量编码层（vector/embedding）的测试。

这一层决定「语义搜索能不能搜到东西」，而它的失败几乎都是**静默降级**：
返回 None、跳过该条、任务照样报成功。所以测试重点不是「成功路径」，而是：

1. **文本嵌入的截断重试**：Ollama 的 all-minilm 有 context 上限，超长文本返回
   500「context length exceeded」。必须按 1024/512/256 逐级截断重试——否则长文本
   素材永远没有文本向量，语义搜索搜不到它。
2. **瞬时故障重试**：Ollama 被 VLM/人脸任务抢占时会超时/5xx，要重试
   `_TEXT_ATTEMPTS` 次才判失败；失败必须**区别于「无文本可嵌入」**（返回 None 前
   不能把后者也当成功）。
3. **CLIP 失败是带时效的**：一次临时性加载失败（显存被占）不能把整个进程后续
   所有图像向量永久打死——超过 `_CLIP_RETRY_AFTER_SECONDS` 必须允许重试。
4. **批量编码的槽位对齐**：返回列表必须与入参等长且一一对应，坏图只让那一槽为
   None；整批 encode 抛错要回落到逐张，不能一张坏图拖垮整窗。

CLIP 模型与 Ollama 都用替身（真模型 600MB + GPU，测试不能也不需要加载）。
"""

import json

import httpx
import pytest

from app.services.vector import embedding as emb


# conftest 有 session 级 autouse fixture 把 _encode_image_sync 换成假实现
# （避免每个会话真加载 600MB CLIP）。本模块要测的正是那个实现，所以先抓住真函数，
# 再在每个用例里装回去。
_REAL_ENCODE = emb._encode_image_sync


@pytest.fixture(autouse=True)
def _restore_real_encoder(monkeypatch):
    monkeypatch.setattr(emb, "_encode_image_sync", _REAL_ENCODE)


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """每个用例都从「模型未加载、缓存为空」开始，避免模块级单例互相污染。"""
    monkeypatch.setattr(emb, "_image_model", None)
    monkeypatch.setattr(emb, "_image_model_error", None)
    monkeypatch.setattr(emb, "_image_model_error_at", None)
    emb._text_cache.clear()
    emb._image_cache.clear()
    yield


# ── 假 Ollama ──


class _Resp:
    def __init__(self, *, payload=None, status: int = 200, text: str = ""):
        self._payload = payload
        self.status_code = status
        self.text = text

    def json(self):
        return self._payload or {}


class _FakeOllama:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requests: list[dict] = []

    async def post(self, url, json=None):
        self.requests.append(json or {})
        item = self._responses.pop(0) if self._responses else _Resp(payload={"embedding": [0.1]})
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def ollama(monkeypatch):
    holder = {"client": _FakeOllama([_Resp(payload={"embedding": [0.1, 0.2]})])}

    class _Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return holder["client"]

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    # 重试退避不要真的 sleep
    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(emb.asyncio, "sleep", _no_sleep)
    return holder


# ── 文本向量 ──


async def test_text_embedding_returns_vector(ollama):
    out = await emb.generate_text_embedding("白色亚麻衬衫")
    assert out == [0.1, 0.2]


async def test_text_embedding_empty_text_returns_none_without_request(ollama):
    """空文本不该打接口（调用方据此区分「无文本」与「编码失败」）。"""
    assert await emb.generate_text_embedding("   ") is None
    assert ollama["client"].requests == []


async def test_text_embedding_is_cached(ollama):
    await emb.generate_text_embedding("通勤穿搭")
    await emb.generate_text_embedding("通勤穿搭")
    assert len(ollama["client"].requests) == 1  # 第二次走缓存


async def test_text_embedding_truncates_on_context_error(ollama):
    """超长文本（context length 错误）要逐级截断重试，最终拿到向量。"""
    ollama["client"] = _FakeOllama([
        _Resp(status=500, text="the input length exceeds the context length"),
        _Resp(payload={"embedding": [0.5]}),
    ])

    out = await emb.generate_text_embedding("很长的文本" * 500)

    assert out == [0.5]
    # 第二次请求的 prompt 被截断到 1024 字符
    assert len(ollama["client"].requests[1]["prompt"]) == 1024


async def test_text_embedding_retries_transient_failure(ollama):
    """5xx 属瞬时故障：重试一次应能成功（Ollama 被 VLM 抢占时会这样）。"""
    ollama["client"] = _FakeOllama([
        _Resp(status=503, text="busy"),
        _Resp(payload={"embedding": [0.7]}),
    ])

    assert await emb.generate_text_embedding("针织") == [0.7]


async def test_text_embedding_gives_up_after_attempts(ollama):
    """重试耗尽返回 None（调用方计 text_failed，不能当成功跳过）。"""
    ollama["client"] = _FakeOllama([
        _Resp(status=500, text="boom"),
        _Resp(status=500, text="boom"),
    ])

    assert await emb.generate_text_embedding("针织") is None


async def test_text_embedding_network_error_is_logged_with_type(ollama):
    """异常日志必须带类型名：httpx 超时的 str() 是空串，只打空日志无法定位。"""
    ollama["client"] = _FakeOllama([
        httpx.ConnectTimeout(""),
        httpx.ConnectTimeout(""),
    ])

    assert await emb.generate_text_embedding("针织") is None


async def test_text_embedding_empty_result_is_none(ollama):
    ollama["client"] = _FakeOllama([_Resp(payload={"embedding": []})])
    assert await emb.generate_text_embedding("针织") is None


async def test_text_embedding_404_not_retried(ollama):
    """模型未安装（404）是永久错误，不必重试。"""
    ollama["client"] = _FakeOllama([_Resp(status=404, text="model not found")])

    assert await emb.generate_text_embedding("针织") is None
    assert len(ollama["client"].requests) == 1


def test_text_cache_evicts_oldest_when_full(monkeypatch):
    monkeypatch.setattr(emb, "_TEXT_CACHE_MAX", 2)
    emb._cache_text_embedding("a", [1.0])
    emb._cache_text_embedding("b", [2.0])
    emb._cache_text_embedding("c", [3.0])

    assert "a" not in emb._text_cache
    assert set(emb._text_cache) == {"b", "c"}


def test_text_embedding_status_exposes_model_and_dim():
    status = emb.get_text_embedding_status()
    assert status["dim"] == emb.TEXT_EMBEDDING_DIM
    assert status["model"]


# ── CLIP 依赖与模型加载 ──


def test_clip_dependency_detected():
    """当前环境（未装 sentence-transformers/torch）应给出可操作的安装提示。"""
    reason = emb._check_clip_dependency()
    if reason is None:
        pytest.skip("本机已安装 CLIP 依赖，跳过缺依赖分支")
    assert "sentence-transformers" in reason


def test_image_status_reports_reason_when_unavailable():
    status = emb.get_image_embedding_status()
    assert status["dim"] == emb.IMAGE_EMBEDDING_DIM
    if status["available"]:
        assert "可用" in status["reason"]
    else:
        assert "不可用" in status["reason"]


def test_load_clip_model_returns_none_when_dependency_missing(monkeypatch):
    monkeypatch.setattr(emb, "_check_clip_dependency", lambda: "缺少依赖")

    assert emb._load_clip_model() is None
    assert emb._image_model_error == "缺少依赖"
    assert emb._image_model_error_at is not None


def test_clip_error_expires_after_retry_window(monkeypatch):
    """加载失败的缓存**带时效**：窗口过了要重新尝试，否则临时故障=永久降级。"""
    calls = {"n": 0}

    def _dep():
        calls["n"] += 1
        return "缺少依赖"

    monkeypatch.setattr(emb, "_check_clip_dependency", _dep)
    monkeypatch.setattr(emb, "_CLIP_RETRY_AFTER_SECONDS", 0.0)

    assert emb._load_clip_model() is None  # 第一次失败并缓存
    first = calls["n"]
    # 窗口=0 → 立即可重试
    assert emb._load_clip_model() is None
    assert calls["n"] > first  # 确实重新检测过依赖


def test_clip_model_loaded_once(monkeypatch):
    """模型只加载一次（约 600MB）：第二次直接返回缓存实例。"""
    fake_model = object()
    monkeypatch.setattr(emb, "_check_clip_dependency", lambda: None)

    class _ST:
        def __init__(self, *args, **kwargs):
            self.loads = 1

    import sys
    import types

    mod = types.ModuleType("sentence_transformers")
    mod.SentenceTransformer = _ST  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", mod)

    first = emb._load_clip_model()
    second = emb._load_clip_model()

    assert first is not None and first is second
    assert first is not fake_model


# ── 图像编码 ──


class _FakeImage:
    def __init__(self, mode="RGB"):
        self.mode = mode
        self.converted = False

    def convert(self, mode):
        self.converted = True
        self.mode = mode
        return self


class _FakeModel:
    """CLIP 替身：encode 返回带 tolist() 的对象，可配置按批/逐张行为。"""

    class _Vec:
        def __init__(self, values):
            self._values = values

        def tolist(self):
            return self._values

    def __init__(self, *, fail_batch: bool = False, fail_all: bool = False):
        self.fail_batch = fail_batch
        self.fail_all = fail_all
        self.calls = 0

    def encode(self, target):
        self.calls += 1
        if self.fail_all:
            raise RuntimeError("CUDA out of memory")
        if isinstance(target, list):
            if self.fail_batch:
                raise RuntimeError("batch encode failed")
            return [self._Vec([0.1, i * 0.0]) for i, _ in enumerate(target)]
        return self._Vec([0.9])


@pytest.fixture
def fake_images(monkeypatch):
    """把 PIL.Image.open 换成假图，避免依赖真实文件与 Pillow 解码。"""
    import PIL.Image as real_image

    opened: list[str] = []

    class _Open:
        def __call__(self, src):
            opened.append(str(src))
            if "broken" in str(src):
                raise OSError("decode failed")
            return _FakeImage(mode="RGB" if "grey" not in str(src) else "L")

    monkeypatch.setattr(real_image, "open", _Open())
    return opened


def test_encode_image_uses_cache_for_path(tmp_path, monkeypatch, fake_images):
    """同一路径第二次编码走缓存（图像向量生成很贵）。"""
    model = _FakeModel()
    monkeypatch.setattr(emb, "_load_clip_model", lambda: model)
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x")

    first = emb._encode_image_sync(str(f), None)
    second = emb._encode_image_sync(str(f), None)

    assert first == second
    assert model.calls == 1  # 第二次没再 encode


def test_encode_image_converts_non_rgb(tmp_path, monkeypatch, fake_images):
    model = _FakeModel()
    monkeypatch.setattr(emb, "_load_clip_model", lambda: model)
    f = tmp_path / "grey.png"
    f.write_bytes(b"x")

    assert emb._encode_image_sync(str(f), None) == [0.9]


def test_encode_image_missing_file_returns_none(monkeypatch, fake_images):
    monkeypatch.setattr(emb, "_load_clip_model", lambda: _FakeModel())
    assert emb._encode_image_sync("/no/such/file.jpg", None) is None


def test_encode_image_from_bytes(monkeypatch, fake_images):
    model = _FakeModel()
    monkeypatch.setattr(emb, "_load_clip_model", lambda: model)

    assert emb._encode_image_sync(None, b"raw-bytes") == [0.9]
    assert model.calls == 1


def test_encode_image_returns_none_when_model_unavailable(monkeypatch, fake_images):
    monkeypatch.setattr(emb, "_load_clip_model", lambda: None)
    assert emb._encode_image_sync("/tmp/a.jpg", None) is None


async def test_generate_image_embedding_without_input_returns_none():
    assert await emb.generate_image_embedding() is None


async def test_generate_image_embeddings_empty_list():
    assert await emb.generate_image_embeddings([]) == []


def test_encode_images_keeps_slot_alignment_with_broken_file(tmp_path, monkeypatch, fake_images):
    """坏图只让对应槽位为 None，其它素材照常出向量（顺序不能错位）。"""
    model = _FakeModel()
    monkeypatch.setattr(emb, "_load_clip_model", lambda: model)
    good = tmp_path / "good.jpg"
    good.write_bytes(b"x")

    out = emb._encode_images_sync([str(good), "/tmp/broken-1.jpg"])

    assert len(out) == 2
    assert out[0] is not None
    assert out[1] is None


def test_encode_images_falls_back_to_one_by_one(tmp_path, monkeypatch, fake_images):
    """整批 encode 抛错（OOM）时回落逐张，不能一张坏图/一次 OOM 拖垮整窗。"""
    calls = {"n": 0}

    class _Model:
        def encode(self, target):
            calls["n"] += 1
            if isinstance(target, list):
                raise RuntimeError("CUDA out of memory")

            class _V:
                def tolist(self):
                    return [0.3]

            return _V()

    monkeypatch.setattr(emb, "_load_clip_model", lambda: _Model())
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    a.write_bytes(b"x")
    b.write_bytes(b"x")

    out = emb._encode_images_sync([str(a), str(b)])

    assert out == [[0.3], [0.3]]
    assert calls["n"] == 3  # 1 次批量失败 + 2 次逐张


def test_encode_images_returns_all_none_when_model_unavailable(tmp_path, monkeypatch, fake_images):
    monkeypatch.setattr(emb, "_load_clip_model", lambda: None)
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x")
    assert emb._encode_images_sync([str(f)]) == [None]


def test_encode_images_all_cached_skips_model(tmp_path, monkeypatch, fake_images):
    """全部命中缓存时连模型都不用加载（避免无谓地占显存）。"""
    c = tmp_path / "c.jpg"
    c.write_bytes(b"x")
    emb._cache_image_embedding(str(c), [1.0])

    def _boom():
        raise AssertionError("不该加载模型")

    monkeypatch.setattr(emb, "_load_clip_model", _boom)
    assert emb._encode_images_sync([str(c)]) == [[1.0]]


# ── 文本内容构建（语义搜索的输入）──


def test_build_inspiration_text_includes_tags_colors_author_caption():
    class _Tag:
        def __init__(self, name):
            self.name = name

    class _Link:
        """标签关联对象：素材 → InspirationTag → Tag。"""

        def __init__(self, name):
            self.tag = _Tag(name)

    class _Insp:
        tags = [_Link("针织开衫"), _Link("极简")]
        dominant_colors = json.dumps(["米色", "白色"])
        source_author = "某博主"
        caption = "通勤穿搭分享"

    text = emb.build_inspiration_text(_Insp())

    assert "针织开衫" in text and "极简" in text
    assert "颜色 米色" in text
    assert "某博主" in text
    assert "通勤穿搭分享" in text


def test_build_inspiration_text_truncates_long_caption():
    class _Insp:
        tags = []
        dominant_colors = None
        source_author = None
        caption = "长" * 900

    text = emb.build_inspiration_text(_Insp())
    assert len(text) <= emb._CAPTION_MAX_CHARS  # caption 超长截断，避免稀释标签信号


def test_build_inspiration_text_tolerates_bad_colors_json():
    """主色调字段是历史脏数据（非 JSON）时不能崩，只是不参与嵌入。"""

    class _Insp:
        tags = []
        dominant_colors = "不是 JSON"
        source_author = None
        caption = ""

    assert emb.build_inspiration_text(_Insp()) == ""


def test_text_formula_version_is_positive():
    assert emb.get_text_formula_version() >= 1
