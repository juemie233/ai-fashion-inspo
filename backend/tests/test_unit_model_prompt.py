"""模型级 Prompt 存储（`services/model_prompt.py`）单测：读写、缓存失效、版本历史、复制。

这块是 AI 分析的高频读路径（每分析一张图都要取一次 Prompt），所以实现里有两个
容易写错、写错了又不会报错的点，正是本文件要钉住的：

1. **按 mtime 缓存**：命中缓存必须返回同一份数据（否则每张图都读盘）；文件被改过
   （mtime 变化）必须重新读（否则界面保存了 Prompt，分析还在用旧的）；
   文件暂时不可 stat 时只能退回旧缓存，不能抛异常把分析链路打断。
2. **损坏文件必须降级为空配置**：`prompt_configs.json` 是运行时被后端重写的文件，
   断电/写一半就可能坏。此时要回退全局默认 Prompt 继续跑，而不是让整个分析失败。

版本历史（`prompt_versions.json`）同理：它由 AI 设置页写入，格式有两代（按模型隔离 /
旧的平铺数组），读的时候要兼容旧格式、坏文件返回空列表。
"""

import json

import pytest

from app.services import model_prompt as mp


@pytest.fixture(autouse=True)
def _isolated_files(tmp_path, monkeypatch):
    """两个存储文件都指到临时目录，并清空模块级缓存（缓存键是 mtime）。"""
    monkeypatch.setattr(mp, "_PROMPT_FILE", tmp_path / "prompt_configs.json")
    monkeypatch.setattr(mp, "_VERSIONS_FILE", tmp_path / "prompt_versions.json")
    monkeypatch.setattr(mp, "_cache", {"mtime": None, "data": None})
    return tmp_path


def _write(path, payload) -> None:
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class TestLoad:
    """`_load` 的四种状态：不存在 / 正常 / 损坏 / 暂时读不到 stat。"""

    def test_missing_file_returns_empty_and_clears_cache(self):
        # 先塞一个假缓存，确认「文件不存在」会把它清掉（否则删文件后仍读旧 Prompt）
        mp._cache["data"] = {"stale": "旧缓存"}
        assert mp._load() == {}
        assert mp._cache["data"] is None
        assert mp._cache["mtime"] is None

    def test_valid_file_is_loaded(self):
        _write(mp._PROMPT_FILE, {"qwen3-vl:8b-instruct": "自定义 Prompt"})
        assert mp._load() == {"qwen3-vl:8b-instruct": "自定义 Prompt"}
        assert mp._cache["data"] == {"qwen3-vl:8b-instruct": "自定义 Prompt"}

    def test_cached_value_is_reused_without_reading_disk(self, monkeypatch):
        """命中缓存时只 stat 比对 mtime，**不读文件内容**（高频路径，每张图都会调）。"""
        _write(mp._PROMPT_FILE, {"m": "v"})
        first = mp._load()

        def _boom_read(*_args, **_kwargs):
            raise AssertionError("缓存命中时不该读盘")

        monkeypatch.setattr(mp.Path, "read_text", _boom_read)
        assert mp._load() == first

    def test_stat_failure_falls_back_to_previous_cache(self, monkeypatch):
        """文件存在但 stat 报错（权限/占用）：退回上次缓存，不能抛异常。"""

        class _StatBoom:
            def exists(self):
                return True

            def stat(self):
                raise OSError("boom")

        mp._cache["data"] = {"m": "缓存中的值"}
        monkeypatch.setattr(mp, "_PROMPT_FILE", _StatBoom())
        assert mp._load() == {"m": "缓存中的值"}

    def test_corrupt_json_returns_empty(self):
        _write(mp._PROMPT_FILE, '{"m": "写了一半就被断电')
        assert mp._load() == {}

    def test_non_dict_json_returns_empty(self):
        _write(mp._PROMPT_FILE, ["不是字典"])
        assert mp._load() == {}

    def test_mtime_change_invalidates_cache(self, monkeypatch):
        _write(mp._PROMPT_FILE, {"m": "旧"})
        assert mp._load() == {"m": "旧"}

        # 直接改缓存里的 mtime，模拟「文件被重写」（不依赖文件系统时间精度）
        mp._cache["mtime"] = -1
        _write(mp._PROMPT_FILE, {"m": "新"})
        assert mp._load() == {"m": "新"}


class TestGetPrompt:
    def test_returns_custom_prompt(self):
        _write(mp._PROMPT_FILE, {"qwen3-vl:8b-instruct": "这个模型的自定义 Prompt"})
        assert mp.get_model_prompt("qwen3-vl:8b-instruct") == "这个模型的自定义 Prompt"

    def test_falls_back_to_global_default(self, monkeypatch):
        monkeypatch.setattr(mp.settings, "ai_analysis_prompt", "全局默认 Prompt")
        _write(mp._PROMPT_FILE, {"other-model": "别的模型的"})
        assert mp.get_model_prompt("未配置的模型") == "全局默认 Prompt"

    def test_empty_string_also_falls_back(self, monkeypatch):
        """配置成空串等于没配：不能让分析拿到空 Prompt 去问模型。"""
        monkeypatch.setattr(mp.settings, "ai_analysis_prompt", "全局默认 Prompt")
        _write(mp._PROMPT_FILE, {"m": ""})
        assert mp.get_model_prompt("m") == "全局默认 Prompt"

    def test_get_all_returns_only_configured(self):
        _write(mp._PROMPT_FILE, {"a": "pa", "b": "pb"})
        assert mp.get_all_model_prompts() == {"a": "pa", "b": "pb"}


class TestSetPrompt:
    async def test_set_persists_and_keeps_other_models(self):
        _write(mp._PROMPT_FILE, {"existing": "原值"})
        await mp.set_model_prompt("new-model", "新 Prompt")

        saved = json.loads(mp._PROMPT_FILE.read_text(encoding="utf-8"))
        assert saved == {"existing": "原值", "new-model": "新 Prompt"}

    async def test_set_overwrites_same_model(self):
        await mp.set_model_prompt("m", "第一版")
        await mp.set_model_prompt("m", "第二版")

        saved = json.loads(mp._PROMPT_FILE.read_text(encoding="utf-8"))
        assert saved["m"] == "第二版"

    async def test_set_from_scratch_creates_file(self):
        assert not mp._PROMPT_FILE.exists()
        await mp.set_model_prompt("m", "p")
        assert mp._PROMPT_FILE.exists()


class TestCopyPrompt:
    async def test_copies_source_to_destination(self):
        _write(mp._PROMPT_FILE, {"src": "源 Prompt"})
        await mp.copy_model_prompt("src", "dst")

        saved = json.loads(mp._PROMPT_FILE.read_text(encoding="utf-8"))
        assert saved["dst"] == "源 Prompt"
        assert saved["src"] == "源 Prompt"  # 源保留

    async def test_missing_source_writes_nothing(self):
        _write(mp._PROMPT_FILE, {"src": "源 Prompt"})
        before = mp._PROMPT_FILE.read_text(encoding="utf-8")
        await mp.copy_model_prompt("不存在的模型", "dst")

        assert mp._PROMPT_FILE.read_text(encoding="utf-8") == before


class TestPromptVersions:
    """版本历史的两代格式与坏文件降级。"""

    def test_missing_file_returns_empty(self):
        assert mp.get_prompt_versions("m") == []

    def test_model_isolated_versions(self):
        _write(mp._VERSIONS_FILE, {"m": [{"prompt": "v1", "saved_at": "t1", "length": 2}]})
        versions = mp.get_prompt_versions("m")
        assert versions == [{"prompt": "v1", "saved_at": "t1", "length": 2}]
        assert mp.get_prompt_versions("其他模型") == []

    def test_legacy_flat_list_is_ignored(self):
        """旧的平铺格式（不按模型隔离）已废弃：不能把别人的历史当成本模型的。"""
        _write(mp._VERSIONS_FILE, [{"prompt": "旧格式"}])
        assert mp.get_prompt_versions("m") == []

    def test_non_list_value_returns_empty(self):
        _write(mp._VERSIONS_FILE, {"m": "不是列表"})
        assert mp.get_prompt_versions("m") == []

    def test_corrupt_file_returns_empty(self):
        _write(mp._VERSIONS_FILE, "{坏掉的 JSON")
        assert mp.get_prompt_versions("m") == []
