"""ollama_utils 回归测试：端口探测，以及「已有实例在跑时不再拉起第二个」。

背景（2026-09-27 排查「Ollama 端口被反复抢占」）：Ollama 忙时（加载模型 / 被
质量审核的 VLM、人脸匹配抢占）``/api/version`` 会在超时内答不上来，旧逻辑据此
再拉起一个 ``ollama serve``——新实例 bind 失败并把错误刷进
``%LOCALAPPDATA%\\Ollama\\server.log``（本机实测自 8/11 起累积 3200+ 行，全是
同一个 bind 错误），而抢到端口的那个实例还成了孤儿进程（父 PowerShell 已退出），
托盘 App 再也管不到它。这里锁住新判据：端口已有人监听就只等就绪，不再起第二个。
"""

import asyncio

from app.services import ollama_utils


async def test_port_listening_follows_real_listener(monkeypatch):
    """端口有人监听时为 True（哪怕它不回答 HTTP）；关掉后为 False。"""

    async def handle(reader, writer):
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setattr(
        ollama_utils.settings, "ollama_base_url", f"http://127.0.0.1:{port}"
    )
    try:
        assert await ollama_utils.is_ollama_port_listening() is True
    finally:
        server.close()
        await server.wait_closed()

    assert await ollama_utils.is_ollama_port_listening() is False


async def test_start_ollama_does_not_spawn_when_port_busy(monkeypatch):
    """端口已被占用（Ollama 正在加载模型/繁忙）：只等就绪，绝不启动第二个实例。"""
    spawned: list[tuple] = []

    async def fake_exec(*args, **kwargs):
        spawned.append(args)
        raise AssertionError("端口已被占用时不应再启动 ollama serve")

    async def never_running() -> bool:
        return False

    async def always_listening() -> bool:
        return True

    monkeypatch.setattr(ollama_utils, "is_ollama_running", never_running)
    monkeypatch.setattr(ollama_utils, "is_ollama_port_listening", always_listening)
    monkeypatch.setattr(ollama_utils.asyncio, "create_subprocess_exec", fake_exec)
    # 指向必然连不上的端口：只验证「没去启动新实例」+ 返回等待提示，不依赖真实 Ollama
    monkeypatch.setattr(ollama_utils.settings, "ollama_base_url", "http://127.0.0.1:1")

    message = await ollama_utils.start_ollama()

    assert spawned == []
    assert message == "Ollama 正在启动中，请稍后重试分析任务"


async def test_start_ollama_returns_running_when_api_answers(monkeypatch):
    """HTTP 健康检查通过：直接返回「已在运行」，不探端口也不启动。"""

    async def always_running() -> bool:
        return True

    async def boom() -> bool:
        raise AssertionError("已在运行时不应探测端口")

    monkeypatch.setattr(ollama_utils, "is_ollama_running", always_running)
    monkeypatch.setattr(ollama_utils, "is_ollama_port_listening", boom)

    assert await ollama_utils.start_ollama() == "Ollama 已在运行"
