/**
 * 后台任务轮询（`useTaskPolling`）的测试。
 *
 * 这是「创建任务 → 等结果」的唯一通道（出图、标签分析、质量审核、向量回填都用它），
 * 四个语义写错了都表现为「界面一直转圈」或「回调执行两次」：
 *
 * 1. **终态副作用只执行一次**：WS 推送与轮询会同时到达终态，`settled` 必须保证
 *    `onDone` 只跑一次（否则「出图 4 张」会被插两次库 / 弹两次提示）；
 * 2. **代际号（seq）**：`stopPolling` 后，在途请求返回时不能再续排定时器
 *    （否则页面已经关了还在打接口）；
 * 3. **连续失败 5 次就停**：静默无限重试会让一个坏任务把页面拖成「一直在请求」；
 * 4. **WS 已连接时放慢到 5s**：推送为主、轮询兜底，别把接口打成 1s 一次。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  subscribeWs: vi.fn(),
  isWsConnected: vi.fn(() => false),
  handlers: new Map<string, (raw: unknown) => void>(),
}))

vi.mock('@/api/client', () => ({ default: { get: mocks.get } }))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

vi.mock('@/composables/useWebSocket', () => ({
  subscribeWs: (type: string, handler: (raw: unknown) => void) => {
    mocks.handlers.set(type, handler)
    return () => mocks.handlers.delete(type)
  },
  isWsConnected: () => mocks.isWsConnected(),
}))

import { useTaskPolling } from '../useTaskPolling'

function status(overrides: Record<string, unknown> = {}) {
  return {
    id: 5,
    type: 'image_gen',
    status: 'running',
    progress: 10,
    done: 1,
    total: 4,
    error: null,
    result: null,
    ...overrides,
  }
}

describe('useTaskPolling', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    mocks.isWsConnected.mockReturnValue(false)
  })

  it('运行中持续轮询，成功时以结果调用 onDone', async () => {
    mocks.get.mockResolvedValueOnce({ data: status({ progress: 25 }) }).mockResolvedValueOnce({
      data: status({ status: 'success', progress: 100, result: { files: 4 } }),
    })
    const onDone = vi.fn()
    const { task, pollTask } = useTaskPolling()

    pollTask(5, onDone)
    await vi.advanceTimersByTimeAsync(0)
    expect(task.value?.progress).toBe(25)
    expect(onDone).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(1000) // 下一次轮询
    expect(onDone).toHaveBeenCalledWith({ files: 4 })
    expect(task.value?.status).toBe('success')
  })

  it('终态副作用只执行一次（WS 推送与轮询同时到达）', async () => {
    mocks.get.mockResolvedValue({
      data: status({ status: 'success', progress: 100, result: { files: 1 } }),
    })
    const onDone = vi.fn()
    const { pollTask } = useTaskPolling()

    pollTask(5, onDone)
    await vi.advanceTimersByTimeAsync(0)

    // 再模拟一次 WS 推送同样的终态
    const handler = mocks.handlers.get('task_event')!
    handler({ task_id: 5, status: 'success', progress: 100, result: { files: 1 } })

    expect(onDone).toHaveBeenCalledTimes(1)
  })

  it('失败任务提示后端给的错误原因，不调用 onDone', async () => {
    mocks.get.mockResolvedValue({
      data: status({ status: 'failed', error: '显存不足' }),
    })
    const onDone = vi.fn()
    const { pollTask } = useTaskPolling()

    pollTask(5, onDone)
    await vi.advanceTimersByTimeAsync(0)

    expect(mocks.message.error).toHaveBeenCalledWith('任务失败：显存不足')
    expect(onDone).not.toHaveBeenCalled()
  })

  it('取消的任务给 info 提示（不是错误色）', async () => {
    mocks.get.mockResolvedValue({ data: status({ status: 'cancelled' }) })
    const { pollTask } = useTaskPolling()

    pollTask(5, vi.fn())
    await vi.advanceTimersByTimeAsync(0)

    expect(mocks.message.info).toHaveBeenCalledWith('任务已取消')
    expect(mocks.message.error).not.toHaveBeenCalled()
  })

  it('连续失败 5 次后停止轮询并提示（不无限重试）', async () => {
    mocks.get.mockRejectedValue(new Error('502'))
    const { pollTask } = useTaskPolling()

    pollTask(5, vi.fn())
    await vi.advanceTimersByTimeAsync(0) // 第 1 次失败
    for (let i = 0; i < 4; i++) {
      await vi.advanceTimersByTimeAsync(3000) // 失败重试间隔 3s
    }

    expect(mocks.get).toHaveBeenCalledTimes(5)
    expect(mocks.message.error).toHaveBeenCalledWith(
      expect.stringContaining('获取任务状态多次失败'),
    )

    mocks.get.mockClear()
    await vi.advanceTimersByTimeAsync(10_000)
    expect(mocks.get).not.toHaveBeenCalled() // 已停，不再打接口
  })

  it('stopPolling 后在途响应不再续排（页面关了不该继续请求）', async () => {
    mocks.get.mockResolvedValue({ data: status({ progress: 30 }) })
    const { pollTask, stopPolling } = useTaskPolling()

    pollTask(5, vi.fn())
    stopPolling()
    await vi.advanceTimersByTimeAsync(0)
    mocks.get.mockClear()

    await vi.advanceTimersByTimeAsync(5000)
    expect(mocks.get).not.toHaveBeenCalled()
  })

  it('WS 已连接时轮询放慢到 5 秒（推送为主、轮询兜底）', async () => {
    mocks.isWsConnected.mockReturnValue(true)
    mocks.get.mockResolvedValue({ data: status({ progress: 40 }) })
    const { pollTask } = useTaskPolling()

    pollTask(5, vi.fn())
    await vi.advanceTimersByTimeAsync(0)
    mocks.get.mockClear()

    await vi.advanceTimersByTimeAsync(4000) // 4s：还没到 5s
    expect(mocks.get).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(1000)
    expect(mocks.get).toHaveBeenCalledTimes(1)
  })

  it('WS 推送只合并「当前跟踪任务」的事件（别的任务事件忽略）', async () => {
    mocks.get.mockResolvedValue({ data: status({ progress: 10 }) })
    const { task, pollTask } = useTaskPolling()

    pollTask(5, vi.fn())
    await vi.advanceTimersByTimeAsync(0)
    const handler = mocks.handlers.get('task_event')!

    handler({ task_id: 999, status: 'success', progress: 100 })
    expect(task.value?.status).toBe('running') // 别的任务：不合并

    handler({ task_id: 5, progress: 60 })
    expect(task.value?.progress).toBe(60) // 本任务：合并进度
  })

  it('WS 推送终态时直接走 onDone（不等下一次轮询）', async () => {
    mocks.get.mockResolvedValue({ data: status({ progress: 10 }) })
    const onDone = vi.fn()
    const { pollTask } = useTaskPolling()

    pollTask(5, onDone)
    await vi.advanceTimersByTimeAsync(0)
    mocks.handlers.get('task_event')!({
      task_id: 5,
      status: 'success',
      result: { files: 3 },
    })

    expect(onDone).toHaveBeenCalledWith({ files: 3 })
  })

  it('未开始轮询时收到 WS 事件不崩（task 为空）', async () => {
    const { task } = useTaskPolling()
    const handler = mocks.handlers.get('task_event')!

    expect(() => handler({ task_id: 5, status: 'success' })).not.toThrow()
    expect(task.value).toBeNull()
  })
})
