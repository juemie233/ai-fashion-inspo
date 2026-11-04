"""任务队列相关配置校验（2026-11-03 评审 P1-5）。

看门狗阈值配错（负数、超过 7 天）会在启动时静默关闭/形同关闭；现在 Pydantic
启动即拒绝，supervisor 的 stale 阈值也做了有限值与下限校验。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_task_stall_timeout_accepts_zero_and_default():
    assert Settings(_env_file=None, task_stall_timeout_seconds=0).task_stall_timeout_seconds == 0
    assert Settings(_env_file=None).task_stall_timeout_seconds == 1800


@pytest.mark.parametrize("bad", [-1, 7 * 24 * 3600 + 1])
def test_task_stall_timeout_rejects_out_of_range(bad: int):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, task_stall_timeout_seconds=bad)


def test_ollama_cooldown_default_and_custom():
    assert Settings(_env_file=None).ollama_embed_down_cooldown_seconds == 30.0
    custom = Settings(_env_file=None, ollama_embed_down_cooldown_seconds=12.5)
    assert custom.ollama_embed_down_cooldown_seconds == 12.5
