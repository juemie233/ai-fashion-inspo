/** 全局 WebSocket 客户端（模块级单例）：连接 /ws 端点，接收后端实时推送。

 * 职责：
 * - 建立并维持唯一一条 WS 连接（任意组件首次调用 connectTaskWebSocket 即接通）；
 * - 断线自动重连（指数退避 1s→30s 封顶），重连成功后广播 reconnected
 *   （消费方借此全量刷新一次，补齐断线期间漏掉的事件，保证状态不丢）；
 * - 定期发送 "ping" 保活（后端约定，25s 一次，避开常见中间层 30s 空闲超时）；
 * - 按 message.type 分发事件给订阅者（参照 useTagEvents 的发布订阅模式）；
 * - 连接状态写入 ui store 的 wsConnected（真实接通/断开），UI 可据此展示徽标。

 * 降级约定：WS 未连接时消费方回退到既有轮询；收到无法识别的 type 直接忽略，
 * 保证后端新增事件时前端向前兼容。
 */

import { getCurrentInstance, onBeforeUnmount } from 'vue'
import { useUiStore } from '@/stores/ui'

/** 后端推送的消息类型（已知类型集中定义，未知类型由分发器忽略） */
export type WsMessageType =
  | 'task_event' // 任务队列生命周期事件（worker/task_runners 广播，契约见后端 services/task_events.py）
  | 'ai_analysis_done' // 单素材 AI 分析完成（ai_shared 广播）
  | (string & {}) // 向前兼容：允许后端新增类型

export type WsMessage = { type: WsMessageType } & Record<string, unknown>

type WsHandler = (payload: Record<string, unknown>) => void

// ── 模块级单例状态 ──
let socket: WebSocket | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let pingTimer: ReturnType<typeof setInterval> | null = null
let reconnectAttempt = 0
let manuallyClosed = false

const handlers = new Map<string, Set<WsHandler>>()
const reconnectedHandlers = new Set<() => void>()

/** 重连退避（毫秒）：1s 起步指数翻倍，封顶 30s */
function backoffMs(): number {
  return Math.min(1000 * 2 ** reconnectAttempt, 30000)
}

/** 计算 WS 端点 URL：开发环境走 vite 代理（/ws → 后端），支持 VITE_WS_URL 覆盖 */
function resolveWsUrl(): string {
  const override = import.meta.env.VITE_WS_URL as string | undefined
  if (override) return override
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${protocol}://${location.host}/ws`
}

function setConnected(connected: boolean) {
  try {
    const ui = useUiStore()
    ui.wsConnected = connected
  } catch {
    // 无活动 Pinia（如纯函数单测环境）：跳过状态写入
  }
}

/** 读取 WS 连接状态（无活动 Pinia 的测试环境等场景安全降级为 false） */
export function isWsConnected(): boolean {
  try {
    return useUiStore().wsConnected
  } catch {
    return false
  }
}

function startPing() {
  stopPing()
  pingTimer = setInterval(() => {
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send('ping')
    }
  }, 25000)
}

function stopPing() {
  if (pingTimer) {
    clearInterval(pingTimer)
    pingTimer = null
  }
}

function dispatch(message: WsMessage) {
  const set = handlers.get(message.type)
  if (!set) return // 未知类型忽略（向前兼容）
  for (const h of [...set]) h(message)
}

function connect() {
  // 测试环境不建立真实连接（无后端可连，避免悬挂定时器与未处理异常）
  if (import.meta.env.MODE === 'test') return
  if (
    socket &&
    (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)
  ) {
    return
  }
  manuallyClosed = false
  try {
    socket = new WebSocket(resolveWsUrl())
  } catch {
    scheduleReconnect()
    return
  }

  socket.onopen = () => {
    const wasReconnect = reconnectAttempt > 0
    reconnectAttempt = 0
    setConnected(true)
    startPing()
    // 重连成功：通知消费方全量刷新一次，补齐断线期间漏掉的事件
    if (wasReconnect) {
      for (const h of [...reconnectedHandlers]) h()
    }
  }

  socket.onmessage = (evt) => {
    if (typeof evt.data !== 'string' || evt.data === 'pong') return
    try {
      const parsed = JSON.parse(evt.data) as WsMessage
      if (parsed && typeof parsed.type === 'string') dispatch(parsed)
    } catch {
      // 非 JSON 消息忽略
    }
  }

  socket.onclose = () => {
    setConnected(false)
    stopPing()
    socket = null
    scheduleReconnect()
  }

  socket.onerror = () => {
    // onclose 会紧随触发并统一处理重连，这里不重复调度
  }
}

function scheduleReconnect() {
  if (manuallyClosed || reconnectTimer) return
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    reconnectAttempt += 1
    connect()
  }, backoffMs())
}

/** 显式关闭连接（应用卸载等场景；一般无需调用） */
export function closeTaskWebSocket() {
  manuallyClosed = true
  stopPing()
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  socket?.close()
  socket = null
  setConnected(false)
}

/**
 * 订阅指定类型的 WS 推送事件。
 * 在组件 setup 中调用时自动随组件卸载注销；非组件环境需手动调用返回的取消函数。
 */
export function subscribeWs(type: WsMessageType, handler: WsHandler): () => void {
  connect() // 首个订阅者触发建连（幂等）
  let set = handlers.get(type)
  if (!set) {
    set = new Set()
    handlers.set(type, set)
  }
  set.add(handler)
  const off = () => {
    set!.delete(handler)
    if (set!.size === 0) handlers.delete(type)
  }
  if (getCurrentInstance()) {
    onBeforeUnmount(off)
  }
  return off
}

/** 订阅「断线重连成功」事件（消费方借此全量刷新，补齐漏掉的事件） */
export function onWsReconnected(handler: () => void): () => void {
  connect() // 幂等
  reconnectedHandlers.add(handler)
  const off = () => reconnectedHandlers.delete(handler)
  if (getCurrentInstance()) {
    onBeforeUnmount(off)
  }
  return off
}

/** 确保 WS 连接已启动（不订阅事件、只想要连接状态徽标时使用；幂等） */
export function connectTaskWebSocket() {
  connect()
}
