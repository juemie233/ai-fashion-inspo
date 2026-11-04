"""f2「我的喜欢 / 我的收藏」登录态防护（2026-11-04）。

背景：抖音登录 Cookie 过期后，f2 对点赞接口的 200 空响应会按「0 个作品」处理并以
0 退出，任务看似成功、实际什么都没采（任务 #791 就是这样）。这里锁死预检行为：
过期直接失败并提示更新 Cookie；配置缺失不阻断（临时目录/自定义配置路径照常跑）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.models.task import TaskQueue
from app.services.task_runners.f2_fetch_stages import _fetch_personal_stage
from app.services.task_runners.f2_personal import (
    ensure_personal_cookie,
    personal_cookie_status,
)


def _write_config(tmp_path: Path, cookie: str, relative: str = "f2/conf/app.yaml") -> Path:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"douyin:\n  cookie: {cookie}\n  naming: '{{create}}_{{desc}}'\ntiktok:\n  cookie: ''\n",
        encoding="utf-8",
    )
    return path


def _sid_guard(expiry: str) -> str:
    # 真实 Cookie 里的分隔符常被 URL 编码成 %7C / %3A / +，这里覆盖这种形态
    encoded = expiry.replace(" ", "+").replace(",", "%2C").replace(":", "%3A")
    return f'"sid_guard=abc|1700000000|60|{encoded}; ttwid=1%7Cx"'


def test_未过期_不阻断(tmp_path):
    expiry = datetime.now(UTC) + timedelta(days=30)
    _write_config(tmp_path, _sid_guard(expiry.strftime("%a, %d-%b-%Y %H:%M:%S GMT")))
    status = personal_cookie_status(tmp_path)
    assert status.config_found is True
    assert status.error is None
    assert status.expires_at is not None and status.expires_at > datetime.now(UTC)
    ensure_personal_cookie(tmp_path, "我的喜欢")  # 不抛


def test_已过期_带中文指引失败(tmp_path):
    _write_config(tmp_path, _sid_guard("Wed, 01-Jan-2020 00:00:00 GMT"))
    status = personal_cookie_status(tmp_path)
    assert status.expires_at is not None
    with pytest.raises(RuntimeError, match="登录 Cookie 已于"):
        ensure_personal_cookie(tmp_path, "我的喜欢")
    with pytest.raises(RuntimeError, match="更新 F2 登录态"):
        ensure_personal_cookie(tmp_path, "我的收藏")


def test_配置存在但_cookie_为空_报警不阻断(tmp_path):
    _write_config(tmp_path, "''")
    status = personal_cookie_status(tmp_path)
    assert status.config_found is True
    assert status.expires_at is None
    assert "未设置 douyin.cookie" in (status.error or "")
    # 配置为空时用户至少能看到 warning；不直接杀掉任务，避免误伤自定义配置路径
    ensure_personal_cookie(tmp_path, "我的喜欢")


def test_配置文件缺失_不阻断(tmp_path):
    status = personal_cookie_status(tmp_path)
    assert status.config_found is False
    assert "未找到 F2 配置文件" in (status.error or "")
    ensure_personal_cookie(tmp_path, "我的喜欢")


def test_旧版多行_cookie_也能解析(tmp_path):
    path = tmp_path / "f2" / "conf" / "app.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        "douyin:\n"
        "  cookie: sid_guard=abc|1700000000|60|Tue%2C+01-Jan-2030+00%3A00%3A00+GMT;\n"
        "    ttwid=1%7Cx; sessionid=abc\n"
        "  naming: '{create}_{desc}'\n",
        encoding="utf-8",
    )
    status = personal_cookie_status(tmp_path)
    assert status.expires_at is not None
    assert status.expires_at.year == 2030
    assert status.error is None


async def test_personal_stage_过期cookie提前失败(tmp_path):
    """真正的取数阶段在启动 f2 子进程前就会拒绝过期 Cookie，不再静默成功。"""
    _write_config(tmp_path, _sid_guard("Wed, 01-Jan-2020 00:00:00 GMT"))
    task = TaskQueue(type="f2_import", status="running")
    with pytest.raises(RuntimeError, match="登录 Cookie 已于"):
        await _fetch_personal_stage(
            None,  # 预检在第一次 db.commit 之前，不需要真实会话
            task,
            {},
            tmp_path,
            "like",
            "https://www.douyin.com/user/MS4wLjABAAAAexample",
            0,
            {},
            None,
        )
