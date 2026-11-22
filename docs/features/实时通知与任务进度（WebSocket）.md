# 实时通知与任务进度（WebSocket）

> **状态**：现行（2026-11-22 按代码核对）。真源：`backend/app/routers/ws.py`（端点与广播）、
> `services/task_events.py`（事件契约）、`web/src/composables/useWebSocket.ts`（前端单例）、
> 5 个订阅方 composable + `stores/ui.ts`（`wsConnected`）。
> 任务队列本身见 [异步任务体系](异步任务体系.md)。

## 1. 一句话

任务进度有两条路：**WebSocket 是主通道，轮询是降级**。WS 断了不影响任务执行，
前端自动退化为轮询并在重连后**全量刷新一次**，所以不会出现「界面停在 40% 但任务已经跑完」。

## 2. 后端：只有一个端点、一种事件

| 组成 | 说明 |
|---|---|
| 端点 | `GET /ws`（`APIRouter(prefix="/ws")`，`@router.websocket("")`）——最终路径就是 `/ws` |
| 连接管理 | `ConnectionManager`：`active_connections` 列表 + `connect` / `disconnect` / `broadcast` / `send_to` |
| 保活 | 客户端发文本 `ping`，服务端回 `pong`（约定，非标准 ping 帧） |
| 广播入口 | `task_events.broadcast_task_event(payload)` —— **永不抛错**，异常只记 debug，绝不影响任务主流程 |
| 广播点 | 目前只有任务生命周期一处（`worker` / `task_runners` 在 创建/开始/进度/成功/失败/取消 时调用） |

### 2.1 事件契约（字段只增不改）

```json
{
  "type": "task_event",
  "event": "running | progress | success | failed | cancelled",
  "task_id": 123,
  "task_type": "batch_analyze / multi_analyze / vector_backfill / ...",
  "status": "pending | running | success | ...",
  "progress": 0,
  "done": 0,
  "total": 0,
  "error": null
}
```

- `progress` / `done` / `total` / `error` 是**可选**字段（未知时不带，前端按缺省处理）；
- 契约只在 `task_events.py` 的 docstring 里维护一份，前端按此消费；
- 新增字段向后兼容，前端遇到不认识的 `type` 直接忽略（不报错、不弹错）。

## 3. 前端：模块级单例

`useWebSocket.ts` 不是「每个组件一个连接」，而是**模块级单例**：任意组件第一次 `connectTaskWebSocket()`
即接通，之后所有消费方共用这一条连接。

| 机制 | 实现 |
|---|---|
| 连接地址 | 优先 `VITE_WS_URL`；否则同源 `ws(s)://{location.host}/ws`（开发环境走 Vite 代理） |
| 重连 | 指数退避 `min(1000 * 2^attempt, 30000)`——1s 起步，封顶 30s |
| 保活 | 每 25s 发一次 `ping`（避开常见反代 30s 空闲超时） |
| 分发 | `subscribeWs(type, handler)` 返回**取消订阅函数**；按 `message.type` 分发 |
| 重连通知 | `onWsReconnected(cb)`：重连成功后触发，消费方借此**全量刷新一次**，补齐断线期间漏掉的进度 |
| 连接状态 | 写入 `ui` store 的 `wsConnected`，UI 可据此显示徽标 / 决定是否启用轮询 |

生命周期：`useWebSocket` 内部用 `onBeforeUnmount` 清理计时器与订阅；页面切换不会把连接断掉
（单例常驻），但订阅会随组件卸载自动注销。

## 4. 谁在消费

| 消费方 | 场景 | 消费方式 |
|---|---|---|
| `useTaskCenter` | 全局任务中心（任务列表/进度条） | 订阅 `task_event` + `onWsReconnected` 全量刷新 |
| `useAdminTask` | 后台批量任务的进度 | 订阅 `task_event`，按 `task_id` 匹配自己的任务 |
| `useAnalysisQueue` | 标签分析队列概览 | 订阅 `task_event` |
| `useTaskPolling` | 标签高级管理（健康度/聚类/网络图）的通用任务轮询 | 订阅 `task_event`，未连接时回退轮询 |
| `useF2TaskHistory` | f2 采集任务历史 | 订阅 `task_event` |

**降级约定**：`isWsConnected()` 为 false 时，各消费方回退到既有轮询（间隔由各自实现决定），
WS 恢复后由 `onWsReconnected` 主动刷新，两条路不会同时高频跑。

## 5. 一个诚实说明：`ai_analysis_done` 是预留类型

`useWebSocket.ts` 的类型联合里声明了 `ai_analysis_done`（单素材 AI 分析完成），但**后端目前没有广播点**
（全仓 `grep ai_analysis_done` 只在类型声明与注释里出现）。保留它是为了向前兼容：后端将来加这个事件时，
前端不用改类型就能收到；现在不会有消息到达，消费方也不应只依赖它。

## 6. 常见坑

1. **进度条不动但任务在跑**：先看 `ui.wsConnected`。最常见原因是**代理没开**（Vite dev server 的
   `/ws` 代理配置丢失）或后端重启过——等一个退避周期（最长 30s）自动重连，或刷新页面。
2. **断线期间完成的任务看不到结果**：`onWsReconnected` 只在「重连成功」时触发；如果页面在断线期间
   离开了，回来时依赖页面自己的 `onMounted` 拉取，所以任务中心仍然要给「刷新」入口。
3. **多标签页各连一条**：单例是**每个页面实例一个**，多标签页 = 多条连接，后端广播会各发一份；
   这是预期行为（不是漏消息的原因）。
4. **后端 worker 里广播静默失效**：`broadcast_task_event` 刻意吞异常（worker 进程与 API 进程的
   `manager` 不是同一个对象时连接列表为空），此时**只能靠轮询**，日志里是 debug 级别，别指望在
   warning 里找到线索。
5. **自己加新事件类型**：后端加 `type` + 调用 `broadcast_task_event`，前端在 `WsMessageType` 联合里
   补一个字符串即可；不要改已有字段的含义（契约只增不改）。

## 7. 相关文档

- [异步任务体系](异步任务体系.md) — 任务类型、状态机、worker 模型、与轮询的关系
- [任务队列加固与代码评测](../ops/任务队列加固与代码评测-2026-11.md) — 队列加固的历史整改
- [前端架构](../overview/前端架构.md) — composables 目录与单例约定
- [本地运行与排障](../ops/本地运行与排障.md) — `/ws` 代理、端口与进程排查
