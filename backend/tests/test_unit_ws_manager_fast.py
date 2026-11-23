"""WebSocket 连接管理器的快速用例（不标 slow，进入默认门禁）。

``test_unit_video_ws.py`` 整文件标了 ``slow``（含 ffmpeg 抽帧用例），默认门禁
``-m "not slow and not system"`` 不跑，导致 ``app/routers/ws.py`` 在覆盖率里长期
显示未覆盖。这里只保留纯内存 / 假 socket 的快速部分：连接管理、广播容错、
单发容错与端点 ping/pong，保证默认门禁真正覆盖到实时推送基础设施。
"""

from app.routers import ws as ws_module
from app.routers.ws import ConnectionManager


class _FakeWS:
    """最小 WebSocket 替身：记录 accept/send；fail 时模拟连接已断。"""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.accepted = False
        self.sent: list[dict] = []
        self.texts: list[str] = []

    async def accept(self):
        self.accepted = True

    async def send_json(self, message: dict):
        if self.fail:
            raise RuntimeError("连接已断")
        self.sent.append(message)

    async def send_text(self, text: str):
        self.texts.append(text)


async def test_connect_and_disconnect_is_idempotent():
    manager = ConnectionManager()
    websocket = _FakeWS()

    await manager.connect(websocket)
    assert websocket.accepted is True
    assert manager.active_connections == [websocket]

    manager.disconnect(websocket)
    assert manager.active_connections == []
    # 并发断开时重复移除不能抛错
    manager.disconnect(websocket)


async def test_broadcast_drops_broken_connection_only():
    manager = ConnectionManager()
    good, bad = _FakeWS(), _FakeWS(fail=True)
    await manager.connect(good)
    await manager.connect(bad)

    await manager.broadcast({"type": "progress", "value": 1})

    assert good.sent == [{"type": "progress", "value": 1}]
    assert manager.active_connections == [good]  # 坏连接被移除，不反复失败


async def test_send_to_removes_broken_connection():
    manager = ConnectionManager()
    bad = _FakeWS(fail=True)
    await manager.connect(bad)

    await manager.send_to(bad, {"type": "x"})

    assert manager.active_connections == []


async def test_send_to_ok_connection_delivers():
    manager = ConnectionManager()
    websocket = _FakeWS()
    await manager.connect(websocket)

    await manager.send_to(websocket, {"type": "y"})

    assert websocket.sent == [{"type": "y"}]


class _ScriptedSocket:
    """按脚本返回 receive_text：先 ping，第二次触发断开。"""

    def __init__(self, *, second_event: BaseException | None = None):
        self.accepted = False
        self.texts: list[str] = []
        self._received = 0
        self._second = second_event

    async def accept(self):
        self.accepted = True

    async def receive_text(self) -> str:
        self._received += 1
        if self._received == 1:
            return "ping"
        raise self._second  # type: ignore[misc]

    async def send_text(self, text: str):
        self.texts.append(text)

    async def send_json(self, message: dict):  # pragma: no cover - 本端点不走
        pass


async def test_endpoint_replies_pong_then_disconnects():
    from fastapi import WebSocketDisconnect

    socket = _ScriptedSocket(second_event=WebSocketDisconnect())
    await ws_module.websocket_endpoint(socket)  # type: ignore[arg-type]

    assert socket.accepted is True
    assert socket.texts == ["pong"]
    assert socket not in ws_module.manager.active_connections


async def test_endpoint_disconnects_on_unexpected_error():
    socket = _ScriptedSocket(second_event=TimeoutError("心跳超时"))
    await ws_module.websocket_endpoint(socket)  # type: ignore[arg-type]

    assert socket not in ws_module.manager.active_connections
