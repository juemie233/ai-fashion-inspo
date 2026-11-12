"""`routers/ai_settings.py`（AI 设置页后端）接口测试。

设置页面上每个开关背后都是这里的接口，它们共同决定「分析用哪条 Prompt、超时多久、
采样参数多少」。这些接口以前基本没测：改坏了前端只会表现为「点了没反应」或
「保存后刷新又变回去了」，排查成本很高。

覆盖的语义：
- Prompt 版本历史：读/存/坏文件降级、最多保留 50 版、倒序返回、回滚越界 400；
- 当前 Prompt 的读/写（空文本拒绝）；
- 参数调优：置信度阈值写 `.env`、超时按模型写 `model_configs.json`，只传部分参数时
  其余保持原值；
- 模型配置总览/复制/重置（源与目标相同或缺参要 400）；
- 人物简介 Prompt 的读/改/恢复默认（非字符串/全空白要 400）。
"""

import json

import pytest
from fastapi import HTTPException

from app.routers import ai_settings
from app.services import person_bio_prompt as pbp

_DEFAULT_CFG = {
    "timeout": 300,
    "temperature": 0.7,
    "top_p": 0.9,
    "top_k": 40,
    "num_predict": 4096,
    "num_ctx": 16384,
    "think": False,
}
_ACTIVE = "test-model"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """隔离：版本文件与人物简介 Prompt 文件指到临时目录，模型配置服务换成记录桩。"""
    monkeypatch.setattr(ai_settings, "_prompt_versions_file", tmp_path / "prompt_versions.json")
    monkeypatch.setattr(pbp, "_PROMPT_FILE", tmp_path / "person_bio_prompt.json")
    monkeypatch.setattr(ai_settings.settings, "ollama_vision_model", _ACTIVE)
    monkeypatch.setattr(ai_settings.settings, "ai_low_confidence_threshold", 0.6)
    monkeypatch.setattr(ai_settings.settings, "ai_temperature", 0.7)
    monkeypatch.setattr(ai_settings.settings, "ai_top_p", 0.9)
    monkeypatch.setattr(ai_settings.settings, "ai_top_k", 40)
    monkeypatch.setattr(ai_settings.settings, "ai_num_predict", 4096)
    monkeypatch.setattr(ai_settings.settings, "ai_num_ctx", 16384)
    return tmp_path / "prompt_versions.json"


@pytest.fixture
def stubs(monkeypatch):
    """模型配置/Prompt 服务与 .env 写入的记录桩。"""
    record = {
        "set_prompt": [],
        "set_bio": [],
        "env": [],
        "update_cfg": [],
        "copied_cfg": [],
        "copied_prompt": [],
        "reset_cfg": [],
        "cfg": dict(_DEFAULT_CFG),
    }

    async def _set_prompt(model, prompt):
        record["set_prompt"].append((model, prompt))

    async def _update_env(updates):
        record["env"].append(updates)

    async def _update_cfg(_model, updates):
        record["update_cfg"].append((_model, updates))
        record["cfg"].update(updates)
        return dict(record["cfg"])

    async def _reset_cfg(_model):
        record["cfg"] = dict(_DEFAULT_CFG)
        return dict(record["cfg"])

    async def _copy_cfg(source, destination):
        record["copied_cfg"].append((source, destination))

    async def _copy_prompt(source, destination):
        record["copied_prompt"].append((source, destination))

    monkeypatch.setattr(ai_settings, "get_model_prompt", lambda _m: "当前 Prompt")
    monkeypatch.setattr(ai_settings, "set_model_prompt", _set_prompt)
    monkeypatch.setattr(ai_settings, "_update_env_file", _update_env)
    monkeypatch.setattr(ai_settings, "get_model_config", lambda _m: dict(record["cfg"]))
    monkeypatch.setattr(ai_settings, "update_model_config", _update_cfg)
    monkeypatch.setattr(ai_settings, "reset_model_config", _reset_cfg)
    monkeypatch.setattr(ai_settings, "get_all_model_configs", lambda: {"m1": {"timeout": 30}})
    monkeypatch.setattr(ai_settings, "get_all_model_prompts", lambda: {"m2": "abc"})
    monkeypatch.setattr(ai_settings, "copy_model_config", _copy_cfg)
    monkeypatch.setattr(ai_settings, "copy_model_prompt", _copy_prompt)
    return record


class TestPromptVersionsStorage:
    def test_load_missing_file_returns_empty(self, _isolated):
        assert ai_settings._load_prompt_versions(_ACTIVE) == []

    def test_load_model_isolated_versions(self, _isolated):
        versions = [{"prompt": "v1"}]
        _isolated.write_text(json.dumps({_ACTIVE: versions}), encoding="utf-8")
        assert ai_settings._load_prompt_versions(_ACTIVE) == versions
        assert ai_settings._load_prompt_versions("other") == []

    def test_load_legacy_flat_list(self, _isolated):
        """旧格式（顶层直接是列表）仍要读出来，不能让老用户的版本历史消失。"""
        versions = [{"prompt": "old"}]
        _isolated.write_text(json.dumps(versions), encoding="utf-8")
        assert ai_settings._load_prompt_versions(_ACTIVE) == versions

    def test_load_corrupt_file_returns_empty(self, _isolated):
        _isolated.write_text("{坏掉的", encoding="utf-8")
        assert ai_settings._load_prompt_versions(_ACTIVE) == []

    def test_save_keeps_only_last_50(self, _isolated):
        versions = [{"prompt": f"v{i}"} for i in range(60)]
        ai_settings._save_prompt_versions(_ACTIVE, versions)

        saved = json.loads(_isolated.read_text(encoding="utf-8"))
        assert len(saved[_ACTIVE]) == 50
        assert saved[_ACTIVE][0] == {"prompt": "v10"}  # 最早的 10 条被丢弃

    def test_save_resets_non_dict_or_corrupt_file(self, _isolated):
        _isolated.write_text(json.dumps(["旧格式"]), encoding="utf-8")
        ai_settings._save_prompt_versions(_ACTIVE, [{"prompt": "new"}])
        assert json.loads(_isolated.read_text(encoding="utf-8")) == {_ACTIVE: [{"prompt": "new"}]}

        _isolated.write_text("{坏", encoding="utf-8")
        ai_settings._save_prompt_versions(_ACTIVE, [{"prompt": "new2"}])
        assert json.loads(_isolated.read_text(encoding="utf-8")) == {_ACTIVE: [{"prompt": "new2"}]}


class TestPromptEndpoints:
    async def test_prompt_versions_returns_newest_first_and_truncated_current(
        self, _isolated, stubs, monkeypatch
    ):
        _isolated.write_text(
            json.dumps({_ACTIVE: [{"prompt": "v1"}, {"prompt": "v2"}]}), encoding="utf-8"
        )
        monkeypatch.setattr(ai_settings, "get_model_prompt", lambda _m: "x" * 150)

        result = await ai_settings.prompt_versions()

        assert result["versions"] == [{"prompt": "v2"}, {"prompt": "v1"}]
        assert result["current"] == "x" * 100 + "..."

    async def test_rollback_to_valid_index(self, _isolated, stubs):
        _isolated.write_text(
            json.dumps({_ACTIVE: [{"prompt": "旧版"}, {"prompt": "新版"}]}), encoding="utf-8"
        )

        result = await ai_settings.rollback_prompt({"index": 0})  # 0 = 最新

        assert stubs["set_prompt"] == [(_ACTIVE, "新版")]
        assert "已回滚到版本 #1" in result["message"]
        assert result["prompt"] == "新版"

    @pytest.mark.parametrize("index", [-1, 5])
    async def test_rollback_out_of_range_returns_400(self, _isolated, stubs, index):
        _isolated.write_text(json.dumps({_ACTIVE: [{"prompt": "v1"}]}), encoding="utf-8")

        with pytest.raises(HTTPException) as exc_info:
            await ai_settings.rollback_prompt({"index": index})

        assert exc_info.value.status_code == 400
        assert stubs["set_prompt"] == []

    async def test_rollback_when_no_versions_returns_400(self, _isolated, stubs):
        with pytest.raises(HTTPException) as exc_info:
            await ai_settings.rollback_prompt({"index": 0})
        assert exc_info.value.status_code == 400

    async def test_save_version_appends_current_prompt(self, _isolated, stubs):
        result = await ai_settings.save_prompt_version()

        saved = json.loads(_isolated.read_text(encoding="utf-8"))[_ACTIVE]
        assert saved[0]["prompt"] == "当前 Prompt"
        assert saved[0]["length"] == len("当前 Prompt")
        assert "saved_at" in saved[0]
        assert result["total_versions"] == 1

    async def test_get_prompt_returns_text_and_meta(self, stubs):
        result = await ai_settings.get_prompt()
        assert result == {"prompt": "当前 Prompt", "length": len("当前 Prompt"), "model": _ACTIVE}

    async def test_update_prompt_rejects_empty(self, stubs):
        with pytest.raises(HTTPException) as exc_info:
            await ai_settings.update_prompt({"prompt": ""})
        assert exc_info.value.status_code == 400
        assert stubs["set_prompt"] == []

    async def test_update_prompt_persists(self, stubs):
        result = await ai_settings.update_prompt({"prompt": "新 Prompt"})
        assert stubs["set_prompt"] == [(_ACTIVE, "新 Prompt")]
        assert f"{len('新 Prompt')} 字符" in result["message"]


class TestSettingsEndpoints:
    async def test_get_ai_settings_returns_defaults(self, stubs):
        result = await ai_settings.get_ai_settings()
        assert result["active_model"] == _ACTIVE
        assert result["analysis_timeout"] == 300
        assert result["defaults"]["analysis_timeout"] == ai_settings.settings.ai_analysis_timeout

    async def test_update_confidence_only_writes_env(self, stubs, monkeypatch):
        monkeypatch.setattr(ai_settings.settings, "ai_low_confidence_threshold", 0.6)

        result = await ai_settings.update_ai_settings(
            confidence_threshold=0.75, analysis_timeout=None
        )

        assert ai_settings.settings.ai_low_confidence_threshold == 0.75
        assert stubs["env"] == [{"AI_LOW_CONFIDENCE_THRESHOLD": "0.75"}]
        assert stubs["update_cfg"] == []  # 没传超时就不碰模型配置
        assert result["confidence_threshold"] == 0.75

    async def test_update_timeout_only_touches_model_config(self, stubs):
        result = await ai_settings.update_ai_settings(
            confidence_threshold=None, analysis_timeout=120
        )
        assert stubs["update_cfg"] == [(_ACTIVE, {"timeout": 120})]
        assert stubs["env"] == []
        assert result["analysis_timeout"] == 120

    async def test_update_both(self, stubs, monkeypatch):
        monkeypatch.setattr(ai_settings.settings, "ai_low_confidence_threshold", 0.6)
        await ai_settings.update_ai_settings(confidence_threshold=0.5, analysis_timeout=60)
        assert stubs["env"] and stubs["update_cfg"]

    async def test_get_sampling_params(self, stubs):
        result = await ai_settings.get_sampling_params()
        assert result["temperature"] == 0.7
        assert result["think"] is False
        assert result["defaults"]["think"] is False

    async def test_update_sampling_params_no_args_keeps_config(self, stubs):
        result = await ai_settings.update_sampling_params(
            temperature=None, top_p=None, top_k=None, num_predict=None, num_ctx=None, think=None
        )
        assert stubs["update_cfg"] == []
        assert result["num_ctx"] == 16384

    async def test_update_sampling_params_passes_only_provided(self, stubs):
        result = await ai_settings.update_sampling_params(
            temperature=1.2, top_p=None, top_k=None, num_predict=None, num_ctx=None, think=True
        )
        assert stubs["update_cfg"] == [(_ACTIVE, {"temperature": 1.2, "think": True})]
        assert result["temperature"] == 1.2
        assert result["think"] is True

    async def test_reset_model_config(self, stubs):
        await ai_settings.update_sampling_params(
            temperature=1.5, top_p=None, top_k=None, num_predict=None, num_ctx=None, think=None
        )
        result = await ai_settings.reset_model_config_endpoint()
        assert result["config"]["temperature"] == 0.7
        assert _ACTIVE in result["message"]

    async def test_model_config_overview_unions_configs_and_prompts(self, stubs):
        result = await ai_settings.model_config_overview()

        by_name = {item["name"]: item for item in result["models"]}
        assert set(by_name) == {"m1", "m2"}  # 配置与 Prompt 的并集
        assert by_name["m1"] == {
            "name": "m1",
            "has_config": True,
            "config_fields": ["timeout"],
            "has_prompt": False,
            "prompt_length": 0,
        }
        assert by_name["m2"]["has_prompt"] is True
        assert by_name["m2"]["prompt_length"] == 3

    @pytest.mark.parametrize(
        "payload",
        [{"source": "", "destination": "b"}, {"source": "a", "destination": ""}, {}],
    )
    async def test_copy_config_requires_both_names(self, stubs, payload):
        with pytest.raises(HTTPException) as exc_info:
            await ai_settings.copy_model_config_endpoint(payload)
        assert exc_info.value.status_code == 400

    async def test_copy_config_rejects_same_model(self, stubs):
        with pytest.raises(HTTPException) as exc_info:
            await ai_settings.copy_model_config_endpoint({"source": "a", "destination": "a"})
        assert exc_info.value.status_code == 400

    async def test_copy_config_copies_config_and_prompt(self, stubs):
        result = await ai_settings.copy_model_config_endpoint(
            {"source": "a", "destination": "b"}
        )
        assert stubs["copied_cfg"] == [("a", "b")]
        assert stubs["copied_prompt"] == [("a", "b")]
        assert "a" in result["message"] and "b" in result["message"]


class TestPersonBioPrompt:
    async def test_get_default_state(self):
        result = await ai_settings.get_person_bio_prompt_endpoint()
        assert result["is_default"] is True
        assert result["prompt"] == ai_settings.DEFAULT_PERSON_BIO_PROMPT
        assert result["length"] == len(ai_settings.DEFAULT_PERSON_BIO_PROMPT)

    async def test_update_persists_and_get_reports_custom(self):
        await ai_settings.update_person_bio_prompt_endpoint({"prompt": "  自定义简介模板  "})

        result = await ai_settings.get_person_bio_prompt_endpoint()
        assert result["prompt"] == "自定义简介模板"  # 去首尾空白
        assert result["is_default"] is False

    @pytest.mark.parametrize("payload", [{}, {"prompt": ""}, {"prompt": "   "}, {"prompt": 123}])
    async def test_update_rejects_empty_or_non_string(self, payload):
        with pytest.raises(HTTPException) as exc_info:
            await ai_settings.update_person_bio_prompt_endpoint(payload)
        assert exc_info.value.status_code == 400

    async def test_update_maps_setter_value_error_to_400(self, monkeypatch):
        async def _raise(_prompt):
            raise ValueError("Prompt 不能为空")

        monkeypatch.setattr(ai_settings, "set_person_bio_prompt", _raise)

        with pytest.raises(HTTPException) as exc_info:
            await ai_settings.update_person_bio_prompt_endpoint({"prompt": "x"})

        assert exc_info.value.status_code == 400
        assert "Prompt 不能为空" in exc_info.value.detail

    async def test_reset_restores_default(self):
        await ai_settings.update_person_bio_prompt_endpoint({"prompt": "自定义"})
        result = await ai_settings.reset_person_bio_prompt_endpoint()

        assert result["prompt"] == ai_settings.DEFAULT_PERSON_BIO_PROMPT
        assert (await ai_settings.get_person_bio_prompt_endpoint())["is_default"] is True
