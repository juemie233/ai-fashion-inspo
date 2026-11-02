"""人物简介 Prompt 存储（`services/person_bio_prompt.py`）单测：降级、持久化、恢复默认。

与 `model_prompt` 的区别是「全局一条」，但降级语义一样关键：这个 JSON 是运行时被
接口重写的文件，坏了/空了都必须回退默认模板，否则「AI 生成简介」按钮会直接失败。
"""

import json

import pytest

from app.services import person_bio_prompt as pbp


@pytest.fixture(autouse=True)
def _isolated_file(tmp_path, monkeypatch):
    monkeypatch.setattr(pbp, "_PROMPT_FILE", tmp_path / "person_bio_prompt.json")
    return tmp_path / "person_bio_prompt.json"


def _write(path, payload):
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class TestReadPersisted:
    def test_missing_file_falls_back_to_default(self, _isolated_file):
        assert pbp.get_person_bio_prompt() == pbp.DEFAULT_PERSON_BIO_PROMPT

    def test_persisted_prompt_wins(self, _isolated_file):
        _write(_isolated_file, {"prompt": "自定义简介 Prompt"})
        assert pbp.get_person_bio_prompt() == "自定义简介 Prompt"

    @pytest.mark.parametrize(
        "payload",
        [
            "{坏掉的 JSON",  # 写一半断电
            ["不是字典"],  # 非 dict
            {"other": "没有 prompt 键"},
            {"prompt": ""},  # 空串
            {"prompt": "   "},  # 全空白
            {"prompt": 123},  # 非字符串
        ],
    )
    def test_bad_payloads_fall_back_to_default(self, _isolated_file, payload):
        _write(_isolated_file, payload)
        assert pbp.get_person_bio_prompt() == pbp.DEFAULT_PERSON_BIO_PROMPT

    def test_unreadable_file_falls_back_to_default(self, _isolated_file, monkeypatch):
        """读文件抛 OSError（权限/占用）时同样回退默认，不能把生成链路打断。"""
        _write(_isolated_file, {"prompt": "自定义"})

        def _boom(*_args, **_kwargs):
            raise OSError("读取失败")

        monkeypatch.setattr(pbp.Path, "read_text", _boom)
        assert pbp.get_person_bio_prompt() == pbp.DEFAULT_PERSON_BIO_PROMPT


class TestSetPrompt:
    async def test_saves_stripped_prompt(self, _isolated_file):
        await pbp.set_person_bio_prompt("  带首尾空白的 Prompt  \n")

        saved = json.loads(_isolated_file.read_text(encoding="utf-8"))
        assert saved == {"prompt": "带首尾空白的 Prompt"}

    @pytest.mark.parametrize("value", ["", "   ", "\n\t"])
    async def test_rejects_empty(self, _isolated_file, value):
        with pytest.raises(ValueError, match="Prompt 不能为空"):
            await pbp.set_person_bio_prompt(value)
        assert not _isolated_file.exists()  # 校验失败不应落盘


class TestReset:
    async def test_reset_removes_file_and_restores_default(self, _isolated_file):
        await pbp.set_person_bio_prompt("自定义")
        assert _isolated_file.exists()

        pbp.reset_person_bio_prompt()

        assert not _isolated_file.exists()
        assert pbp.get_person_bio_prompt() == pbp.DEFAULT_PERSON_BIO_PROMPT

    def test_reset_when_missing_is_noop(self, _isolated_file):
        """「恢复默认」按钮可重复点：文件不存在时不能抛异常。"""
        assert not _isolated_file.exists()
        pbp.reset_person_bio_prompt()  # 不抛即通过
        assert pbp.get_person_bio_prompt() == pbp.DEFAULT_PERSON_BIO_PROMPT
