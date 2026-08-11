"""WebSocket 端点：AI 分析和采集进度的实时推送。"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(prefix="/ws", tags=["websocket"])


class ConnectionManager:
    """管理 WebSocket 连接和广播消息。"""

    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        """接受新的 WebSocket 连接。"""
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        """移除断开的连接。"""
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        """向所有已连接的客户端广播消息。"""
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.append(connection)
        for conn in disconnected:
            if conn in self.active_connections:
                self.active_connections.remove(conn)

    async def send_to(self, websocket: WebSocket, message: dict):
        """向单个客户端发送消息。"""
        try:
            await websocket.send_json(message)
        except Exception:
            self.disconnect(websocket)


# 全局连接管理器实例
manager = ConnectionManager()


@router.websocket("")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 连接端点：实时推送分析进度和采集状态。

    客户端可发送 "ping" 来保持连接活跃。
    """
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)
