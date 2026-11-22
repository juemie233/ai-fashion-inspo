/**
 * 后台任务轮询（`useAdminTask`）的测试。
 *
 * 批量删除/去重/向量回填的进度条全靠这里，四个语义错了都表现为「一直转圈」或「回调跑两次」：
 *
 * 1. **终态副作用只执行一次**：WS 推送与 1s 轮询会同时到达终态，`settled` 必须保证
 *    `onDone` 只跑一次（否则刷新/完成提示会重复触发）；
 * 2. **代际号（seq）**：`stopAdminPolling` / 换任务后，在途请求返回时不能再续排定时器；
 * 3. **连续失败 5 次才停**：后端重启/网络抖动不能让进度条永久卡死，但也不能无限重试；
 * 4. **WS 已连接时放慢到 5s**：推送为主、轮询只作兜底，别把接口打成 1s 一次。
 *
 * 另外 `resumeAdminTask` 负责刷新页面后接续进行中的任务（取 100 条避免被已暂停任务挤出）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
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

import { useAdminTask } from '../useAdminTask'

function task(overrides: Record<string, unknown> = {}) {
  return {
    id: 5,
    type: 'batch_delete',
    status: 'running',
    progress: 10,
    total: 100,
    done: 10,
    result: null,
    error: null,
    ...overrides,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function wsHandler(): (raw: unknown) => void {
  const handler = mocks.handlers.get('task_event')
  if (!handler) throw new Error('useAdminTask 未订阅 task_event')
  return handler
}

describe('useAdminTask', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    mocks.handlers.clear()
    mocks.isWsConnected.mockReturnValue(false)
    mocks.get.mockResolvedValue({ data: task() })
  })

  afterEach(() => {
    vi.clearAllTimers()
    vi.useRealTimers()
  })

  it('初始状态：无任务', () => {
    const at = useAdminTask()

    expect(at.adminTask.value).toBeNull()
    expect(mocks.handlers.has('task_event')).toBe(true)
  })

  it('start 立即拉一次状态；运行中按 1s 间隔继续轮询', async () => {
    mocks.get.mockResolvedValue({ data: task({ progress: 10 }) })
    const at = useAdminTask()

    at.startAdminPolling(5, vi.fn())
    expect(mocks.get).toHaveBeenCalledWith('/tasks/5')

    await vi.advanceTimersByTimeAsync(0)
    expect(at.adminTask.value?.progress).toBe(10)

    await vi.advanceTimersByTimeAsync(1000)
    expect(mocks.get).toHaveBeenCalledTimes(2)
  })

  it('WS 已连接时保底轮询放慢到 5s', async () => {
    mocks.isWsConnected.mockReturnValue(true)
    const at = useAdminTask()

    at.startAdminPolling(5, vi.fn())
    await vi.advanceTimersByTimeAsync(0)
    expect(mocks.get).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(1000)
    expect(mocks.get).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(4000)
    expect(mocks.get).toHaveBeenCalledTimes(2)
  })

  it('成功终态：onDone 执行、停止轮询、之后不再请求', async () => {
    mocks.get
      .mockResolvedValueOnce({ data: task({ status: 'running' }) })
      .mockResolvedValueOnce({ data: task({ status: 'success', progress: 100 }) })
    const onDone = vi.fn()
    const at = useAdminTask()

    at.startAdminPolling(5, onDone)
    await vi.advanceTimersByTimeAsync(0)
    expect(onDone).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(1000)

    expect(onDone).toHaveBeenCalledTimes(1)
    expect(at.adminTask.value?.status).toBe('success')
    await vi.advanceTimersByTimeAsync(10000)
    expect(mocks.get).toHaveBeenCalledTimes(2)
  })

  it('失败终态：提示后端 error，不执行 onDone，并停止轮询', async () => {
    mocks.get
      .mockResolvedValueOnce({ data: task({ status: 'running' }) })
      .mockResolvedValueOnce({ data: task({ status: 'failed', error: '磁盘写入失败' }) })
    const onDone = vi.fn()
    const at = useAdminTask()

    at.startAdminPolling(5, onDone)
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(1000)

    expect(mocks.message.error).toHaveBeenCalledWith('任务失败：磁盘写入失败')
    expect(onDone).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(10000)
    expect(mocks.get).toHaveBeenCalledTimes(2)
  })

  it('失败终态无 error 时提示「未知错误」', async () => {
    mocks.get
      .mockResolvedValueOnce({ data: task({ status: 'running' }) })
      .mockResolvedValueOnce({ data: task({ status: 'failed', error: null }) })
    const at = useAdminTask()

    at.startAdminPolling(5, vi.fn())
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(1000)

    expect(mocks.message.error).toHaveBeenCalledWith('任务失败：未知错误')
  })

  it('取消终态：info 提示「任务已取消」，不执行 onDone', async () => {
    mocks.get
      .mockResolvedValueOnce({ data: task({ status: 'running' }) })
      .mockResolvedValueOnce({ data: task({ status: 'cancelled' }) })
    const onDone = vi.fn()
    const at = useAdminTask()

    at.startAdminPolling(5, onDone)
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(1000)

    expect(mocks.message.info).toHaveBeenCalledWith('任务已取消')
    expect(onDone).not.toHaveBeenCalled()
  })

  it('连续失败 5 次后停止轮询并提示；之后不再请求', async () => {
    mocks.get.mockRejectedValue(new Error('backend down'))
    const at = useAdminTask()

    at.startAdminPolling(5, vi.fn())
    await vi.advanceTimersByTimeAsync(0) // 第 1 次失败 → 3s 后重试
    for (let i = 0; i < 4; i += 1) {
      await vi.advanceTimersByTimeAsync(3000) // 第 2~5 次失败
    }

    expect(mocks.get).toHaveBeenCalledTimes(5)
    expect(mocks.message.error).toHaveBeenCalledWith(
      '获取任务状态多次失败，已停止轮询，请稍后手动刷新',
    )

    await vi.advanceTimersByTimeAsync(30000)
    expect(mocks.get).toHaveBeenCalledTimes(5)
  })

  it('连续失败 4 次后恢复：计数清零并正常走到终态', async () => {
    mocks.get
      .mockRejectedValueOnce(new Error('1'))
      .mockRejectedValueOnce(new Error('2'))
      .mockRejectedValueOnce(new Error('3'))
      .mockRejectedValueOnce(new Error('4'))
      .mockResolvedValueOnce({ data: task({ status: 'success', progress: 100 }) })
    const onDone = vi.fn()
    const at = useAdminTask()

    at.startAdminPolling(5, onDone)
    await vi.advanceTimersByTimeAsync(0)
    for (let i = 0; i < 3; i += 1) await vi.advanceTimersByTimeAsync(3000)
    await vi.advanceTimersByTimeAsync(3000)

    expect(mocks.get).toHaveBeenCalledTimes(5)
    expect(onDone).toHaveBeenCalledTimes(1)
    expect(mocks.message.error).not.toHaveBeenCalled()
  })

  it('stopAdminPolling：清掉定时器，在途结果被丢弃且不再续排', async () => {
    const pending = deferred<{ data: unknown }>()
    mocks.get.mockReturnValueOnce(pending.promise)
    const at = useAdminTask()

    at.startAdminPolling(5, vi.fn())
    at.stopAdminPolling()
    pending.resolve({ data: task() })
    await vi.advanceTimersByTimeAsync(0)

    expect(at.adminTask.value).toBeNull()
    expect(mocks.get).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(10000)
    expect(mocks.get).toHaveBeenCalledTimes(1)
  })

  it('停止后在途请求失败：静默丢弃，不提示也不续排', async () => {
    const pending = deferred<{ data: unknown }>()
    mocks.get.mockReturnValueOnce(pending.promise)
    const at = useAdminTask()

    at.startAdminPolling(5, vi.fn())
    at.stopAdminPolling()
    pending.reject(new Error('backend down'))
    await vi.advanceTimersByTimeAsync(0)

    expect(mocks.message.error).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(30000)
    expect(mocks.get).toHaveBeenCalledTimes(1)
  })

  it('开始跟踪新任务会取代旧任务：旧任务的在途响应被丢弃', async () => {
    const oldTask = deferred<{ data: unknown }>()
    mocks.get.mockImplementation((url: string) => {
      if (url === '/tasks/1') return oldTask.promise
      return Promise.resolve({ data: task({ id: 2, progress: 20 }) })
    })
    const at = useAdminTask()

    at.startAdminPolling(1, vi.fn())
    at.startAdminPolling(2, vi.fn())
    await vi.advanceTimersByTimeAsync(0)
    expect(at.adminTask.value?.id).toBe(2)

    oldTask.resolve({ data: task({ id: 1, progress: 99 }) })
    await vi.advanceTimersByTimeAsync(0)

    expect(at.adminTask.value?.id).toBe(2)
    expect(at.adminTask.value?.progress).toBe(20)
  })

  describe('WS 推送合流', () => {
    it('进度事件即时合并；缺失字段保留旧值', async () => {
      mocks.get.mockResolvedValue({ data: task({ progress: 10, done: 10, total: 100 }) })
      const at = useAdminTask()
      at.startAdminPolling(5, vi.fn())
      await vi.advanceTimersByTimeAsync(0)

      wsHandler()({ task_id: 5, progress: 55, done: 55 })

      expect(at.adminTask.value?.progress).toBe(55)
      expect(at.adminTask.value?.done).toBe(55)
      expect(at.adminTask.value?.total).toBe(100)
      expect(at.adminTask.value?.status).toBe('running')
    })

    it('事件带 error 字段时也会合并（进度事件可刷新错误信息）', async () => {
      mocks.get.mockResolvedValue({ data: task({ status: 'running', error: null }) })
      const at = useAdminTask()
      at.startAdminPolling(5, vi.fn())
      await vi.advanceTimersByTimeAsync(0)

      wsHandler()({ task_id: 5, status: 'running', error: '限流重试中' })

      expect(at.adminTask.value?.error).toBe('限流重试中')
      expect(at.adminTask.value?.status).toBe('running')
    })

    it('非当前任务 / 无当前任务的事件直接忽略', async () => {
      const at = useAdminTask()
      wsHandler()({ task_id: 5, progress: 90 }) // 还没开始跟踪 → 安全忽略

      mocks.get.mockResolvedValue({ data: task({ progress: 10 }) })
      at.startAdminPolling(5, vi.fn())
      await vi.advanceTimersByTimeAsync(0)

      wsHandler()({ task_id: 99, progress: 90 })
      expect(at.adminTask.value?.progress).toBe(10)
    })

    it('WS 终态：执行 onDone 并停止轮询（后续同一终态事件不再触发）', async () => {
      mocks.get.mockResolvedValue({ data: task({ status: 'running' }) })
      const onDone = vi.fn()
      const at = useAdminTask()
      at.startAdminPolling(5, onDone)
      await vi.advanceTimersByTimeAsync(0)

      wsHandler()({ task_id: 5, status: 'success', progress: 100 })
      wsHandler()({ task_id: 5, status: 'success', progress: 100 })

      expect(onDone).toHaveBeenCalledTimes(1)
      expect(at.adminTask.value?.status).toBe('success')

      // 终态后定时器已清掉，不再发请求
      await vi.advanceTimersByTimeAsync(5000)
      expect(mocks.get).toHaveBeenCalledTimes(1)
    })

    it('WS 失败/取消终态走与轮询相同的提示', async () => {
      mocks.get.mockResolvedValue({ data: task({ status: 'running' }) })
      const at = useAdminTask()
      at.startAdminPolling(5, vi.fn())
      await vi.advanceTimersByTimeAsync(0)

      wsHandler()({ task_id: 5, status: 'failed', error: '写入失败' })
      expect(mocks.message.error).toHaveBeenCalledWith('任务失败：写入失败')

      const at2 = useAdminTask()
      // 新实例跟踪任务 6：轮询快照的 id 必须与事件一致才会被合并
      mocks.get.mockResolvedValue({ data: task({ id: 6, status: 'running' }) })
      at2.startAdminPolling(6, vi.fn())
      await vi.advanceTimersByTimeAsync(0)
      wsHandler()({ task_id: 6, status: 'cancelled' })
      expect(mocks.message.info).toHaveBeenCalledWith('任务已取消')
      expect(at2.adminTask.value?.status).toBe('cancelled')
    })
  })

  describe('resumeAdminTask', () => {
    it('找到进行中的删除/去重/向量回填任务并接续轮询', async () => {
      mocks.get.mockImplementation((url: string) => {
        if (url === '/tasks') {
          return Promise.resolve({
            data: {
              items: [
                task({ id: 1, type: 'batch_delete', status: 'success' }),
                task({ id: 2, type: 'deduplicate', status: 'running' }),
                task({ id: 3, type: 'scrape', status: 'running' }),
              ],
            },
          })
        }
        return Promise.resolve({ data: task({ id: 2, type: 'deduplicate' }) })
      })
      const at = useAdminTask()

      await at.resumeAdminTask(vi.fn())

      expect(mocks.get).toHaveBeenCalledWith('/tasks', { params: { size: 100 } })
      expect(at.adminTask.value?.id).toBe(2)
      expect(mocks.get).toHaveBeenCalledWith('/tasks/2')
    })

    it('没有符合条件的任务：保持空状态、不启动轮询', async () => {
      mocks.get.mockResolvedValue({
        data: {
          items: [
            task({ id: 1, type: 'batch_delete', status: 'success' }),
            task({ id: 2, type: 'scrape', status: 'running' }),
            task({ id: 3, type: 'deduplicate', status: 'cancelled' }),
          ],
        },
      })
      const at = useAdminTask()

      await at.resumeAdminTask(vi.fn())
      await vi.advanceTimersByTimeAsync(0)

      expect(at.adminTask.value).toBeNull()
      expect(mocks.get).toHaveBeenCalledTimes(1)
    })

    it('恢复进行中任务后到达成功终态：onDone 执行', async () => {
      mocks.get.mockImplementation((url: string) => {
        if (url === '/tasks') {
          return Promise.resolve({
            data: { items: [task({ id: 7, type: 'vector_backfill', status: 'pending' })] },
          })
        }
        return Promise.resolve({
          data: task({ id: 7, type: 'vector_backfill', status: 'success', progress: 100 }),
        })
      })
      const onDone = vi.fn()
      const at = useAdminTask()

      await at.resumeAdminTask(onDone)
      await vi.advanceTimersByTimeAsync(0)

      expect(at.adminTask.value?.status).toBe('success')
      expect(onDone).toHaveBeenCalledTimes(1)
    })

    it('查询失败静默：不抛错、不启动轮询', async () => {
      mocks.get.mockRejectedValue(new Error('500'))
      const at = useAdminTask()

      await expect(at.resumeAdminTask(vi.fn())).resolves.toBeUndefined()
      await vi.advanceTimersByTimeAsync(0)

      expect(at.adminTask.value).toBeNull()
      expect(mocks.message.error).not.toHaveBeenCalled()
      expect(mocks.get).toHaveBeenCalledTimes(1)
    })
  })
})
