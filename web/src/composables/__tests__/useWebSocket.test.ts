/**
 * WebSocket 客户端（`useWebSocket`）的测试：建连、分发、断线重连与重连补偿。
 *
 * 这条链路决定「任务进度、通知是否实时到达」。它有两个必须锁住的语义：
 *
 * 1. **重连后要通知消费方全量刷新**：断线期间的事件是**丢掉的**（服务端不重放），
 *    只重连不刷新，界面会永远停在不完整的状态上（例如任务完成了但页面还在转圈）；
 * 2. **重连退避 1s 起步指数翻倍、封顶 30s**：固定 1s 猛重连会把后端打满；不退避则
 *    断网时永远恢复不了。
 *
 * 另外几条兼容性语义：非 JSON 消息、`pong` 心跳、未知事件类型都必须**静默忽略**
 * （否则协议一升级就前端报错）。测试里用假 WebSocket 类，进程内不建立真实连接。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const OPEN = 1
const CONNECTING = 0

class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  static OPEN = OPEN
  static CONNECTING = CONNECTING

  url: string
  readyState = CONNECTING
  sent: string[] = []
  onopen: (() => void) | null = null
  onmessage: ((evt: { data: unknown }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  send(data: string) {
    this.sent.push(data)
  }

  /** 测试辅助：模拟连接建立 */
  open() {
    this.readyState = OPEN
    this.onopen?.()
  }

  /** 测试辅助：模拟收到消息 */
  receive(data: unknown) {
    this.onmessage?.({ data })
  }

  /** 测试辅助：模拟连接断开 */
  close() {
    this.readyState = 3
    this.onclose?.()
  }
}

async function loadWs() {
  const mod = await import('../useWebSocket')
  return mod
}

describe('useWebSocket', () => {
  beforeEach(() => {
    vi.resetModules()
    // connect() 在 MODE === 'test' 时直接返回（避免真实连接），这里模拟开发环境
    vi.stubEnv('MODE', 'development')
    vi.useFakeTimers()
    setActivePinia(createPinia())
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
  })

  afterEach(() => {
    vi.unstubAllEnvs()
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('首个订阅者触发建连（幂等：多订阅者只连一次）', async () => {
    const ws = await loadWs()

    ws.subscribeWs('task_event', () => {})
    ws.subscribeWs('notification', () => {})
    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(ws.isWsConnected()).toBe(false) // 还没 open
  })

  it('连接建立后写入连接状态并开始心跳', async () => {
    const ws = await loadWs()
    ws.subscribeWs('task_event', () => {})
    const socket = FakeWebSocket.instances[0]

    socket.open()
    expect(ws.isWsConnected()).toBe(true)

    await vi.advanceTimersByTimeAsync(25_000) // 心跳间隔
    expect(socket.sent).toContain('ping')
  })

  it('按事件类型分发；未知类型/非 JSON/pong 都静默忽略', async () => {
    const ws = await loadWs()
    const taskHandler = vi.fn()
    const notifHandler = vi.fn()
    ws.subscribeWs('task_event', taskHandler)
    ws.subscribeWs('notification', notifHandler)
    const socket = FakeWebSocket.instances[0]
    socket.open()

    socket.receive(JSON.stringify({ type: 'task_event', task_id: 1, status: 'running' }))
    expect(taskHandler).toHaveBeenCalledWith(expect.objectContaining({ task_id: 1 }))

    socket.receive('pong') // 心跳回包
    socket.receive('not json at all')
    socket.receive(JSON.stringify({ type: '未定义的事件' }))
    socket.receive(JSON.stringify({ noType: true }))
    expect(taskHandler).toHaveBeenCalledTimes(1)
    expect(notifHandler).not.toHaveBeenCalled()
  })

  it('取消订阅后不再收到事件；同类型多订阅者互不影响', async () => {
    const ws = await loadWs()
    const a = vi.fn()
    const b = vi.fn()
    const offA = ws.subscribeWs('task_event', a)
    ws.subscribeWs('task_event', b)
    const socket = FakeWebSocket.instances[0]
    socket.open()

    socket.receive(JSON.stringify({ type: 'task_event', task_id: 1 }))
    expect(a).toHaveBeenCalledTimes(1)
    expect(b).toHaveBeenCalledTimes(1)

    offA()
    socket.receive(JSON.stringify({ type: 'task_event', task_id: 2 }))
    expect(a).toHaveBeenCalledTimes(1)
    expect(b).toHaveBeenCalledTimes(2)
  })

  it('断线后按 1s → 2s → 4s 指数退避重连，并封顶 30s', async () => {
    const ws = await loadWs()
    ws.subscribeWs('task_event', () => {})
    expect(FakeWebSocket.instances).toHaveLength(1)

    // 第一次断线：1s 后重连
    FakeWebSocket.instances[0].close()
    expect(ws.isWsConnected()).toBe(false)
    await vi.advanceTimersByTimeAsync(1000)
    expect(FakeWebSocket.instances).toHaveLength(2)

    // 第二次断线：2s 后重连（上次失败 → attempt+1）
    FakeWebSocket.instances[1].close()
    await vi.advanceTimersByTimeAsync(1999)
    expect(FakeWebSocket.instances).toHaveLength(2) // 还没到
    await vi.advanceTimersByTimeAsync(1)
    expect(FakeWebSocket.instances).toHaveLength(3)

    // 连续失败多次后间隔封顶 30s：再断 6 次，跳到 30s 上限
    for (let i = 0; i < 6; i++) {
      FakeWebSocket.instances[FakeWebSocket.instances.length - 1].close()
      await vi.advanceTimersByTimeAsync(30_000)
    }
    const before = FakeWebSocket.instances.length
    FakeWebSocket.instances[before - 1].close()
    await vi.advanceTimersByTimeAsync(29_999)
    expect(FakeWebSocket.instances).toHaveLength(before) // 封顶后不会更短
    await vi.advanceTimersByTimeAsync(1)
    expect(FakeWebSocket.instances).toHaveLength(before + 1)
  })

  it('重连成功后通知消费方全量刷新（补齐断线期间丢掉的事件）', async () => {
    const ws = await loadWs()
    ws.subscribeWs('task_event', () => {})
    const onReconnected = vi.fn()
    ws.onWsReconnected(onReconnected)

    // 首连：不算重连，不触发补偿
    FakeWebSocket.instances[0].open()
    expect(onReconnected).not.toHaveBeenCalled()

    // 断线 → 重连成功 → 触发一次
    FakeWebSocket.instances[0].close()
    await vi.advanceTimersByTimeAsync(1000)
    FakeWebSocket.instances[1].open()
    expect(onReconnected).toHaveBeenCalledTimes(1)
    expect(ws.isWsConnected()).toBe(true)

    // 再次重连成功 → 再触发一次
    FakeWebSocket.instances[1].close()
    await vi.advanceTimersByTimeAsync(1000)
    FakeWebSocket.instances[2].open()
    expect(onReconnected).toHaveBeenCalledTimes(2)
  })

  it('取消重连补偿订阅后不再收到通知', async () => {
    const ws = await loadWs()
    ws.subscribeWs('task_event', () => {})
    const handler = vi.fn()
    const off = ws.onWsReconnected(handler)
    off()

    FakeWebSocket.instances[0].close()
    await vi.advanceTimersByTimeAsync(1000)
    FakeWebSocket.instances[1].open()

    expect(handler).not.toHaveBeenCalled()
  })

  it('connectTaskWebSocket 幂等：已连上时不再新建连接', async () => {
    const ws = await loadWs()
    ws.connectTaskWebSocket()
    const socket = FakeWebSocket.instances[0]
    socket.open()

    ws.connectTaskWebSocket()
    expect(FakeWebSocket.instances).toHaveLength(1)
  })
})
