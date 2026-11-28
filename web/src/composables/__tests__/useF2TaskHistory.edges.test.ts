/**
 * 抖音采集历史（`useF2TaskHistory`）补测（原 72.91%）。
 *
 * 既有 `useF2TaskHistory.test.ts` 覆盖了类型过滤/归一化/操作委托；本文件补：
 * - 分页 pageCount 与翻页请求、删除后页码越界回退；
 * - 首次加载失败提示 vs 静默刷新失败不提示；
 * - 轮询回调三态：无进行中任务→停、有任务→继续、刷新失败→停（避免每 5s 空转）；
 * - `hasActive` 监听自动启停轮询；
 * - WS task_event 去抖刷新（300ms 合并多次事件、无 task_id 忽略）；
 * - useTaskActions 的 onQueueTaskDeleted/reload 回调都走静默刷新。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  startPoll: vi.fn(),
  stopPoll: vi.fn(),
  pollOptions: null as null | { callback: () => Promise<void>; immediate?: boolean },
  taskActionsOptions: null as null | { onQueueTaskDeleted?: () => void; reload?: () => void },
  cancelTask: vi.fn(),
  deleteTask: vi.fn(),
  pauseTask: vi.fn(),
  resumeTask: vi.fn(),
  wsHandlers: new Map<string, (raw: unknown) => void>(),
}))

vi.mock('@/api/client', () => ({ default: { get: mocks.get } }))
vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})
vi.mock('@/composables/usePolling', () => ({
  usePolling: (options: { callback: () => Promise<void>; immediate?: boolean }) => {
    mocks.pollOptions = options
    return { start: mocks.startPoll, stop: mocks.stopPoll }
  },
}))
vi.mock('@/composables/useTaskActions', () => ({
  useTaskActions: (options: { onQueueTaskDeleted?: () => void; reload?: () => void }) => {
    mocks.taskActionsOptions = options
    return {
      cancelTask: mocks.cancelTask,
      deleteTask: mocks.deleteTask,
      pauseTask: mocks.pauseTask,
      resumeTask: mocks.resumeTask,
    }
  },
}))
vi.mock('@/composables/useWebSocket', () => ({
  subscribeWs: (type: string, handler: (raw: unknown) => void) => {
    mocks.wsHandlers.set(type, handler)
    return () => mocks.wsHandlers.delete(type)
  },
}))

import { useF2TaskHistory, F2_HISTORY_PAGE_SIZE } from '../useF2TaskHistory'
import { normalizeQueueTask, type QueueTask } from '@/utils/taskPresentation'

function rawTask(over: Partial<QueueTask> = {}): QueueTask {
  return {
    id: 338,
    type: 'f2_import',
    status: 'success',
    progress: 100,
    total: 1,
    done: 1,
    result: {
      import: { imported: 1, batch_file: '/tmp/import_batches/f2-338.json' },
    },
    error: null,
    created_at: '2026-11-20T01:41:19Z',
    updated_at: '2026-11-20T01:47:33Z',
    ...over,
  }
}

function payload(items: QueueTask[] = [], total = items.length) {
  return { data: { items, total } }
}

async function flush() {
  for (let i = 0; i < 6; i += 1) await Promise.resolve()
}

function wsHandler(): (raw: unknown) => void {
  const handler = mocks.wsHandlers.get('task_event')
  if (!handler) throw new Error('useF2TaskHistory 未订阅 task_event')
  return handler
}

describe('useF2TaskHistory 边界补充', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.pollOptions = null
    mocks.taskActionsOptions = null
    mocks.get.mockResolvedValue(payload([rawTask()], 1))
  })

  it('分页：pageCount 至少为 1，翻页带 page 参数并重新加载', async () => {
    mocks.get.mockResolvedValueOnce(payload([], 25))
    const history = useF2TaskHistory()
    await history.loadTasks()

    expect(history.total.value).toBe(25)
    expect(history.pageCount.value).toBe(Math.ceil(25 / F2_HISTORY_PAGE_SIZE))

    mocks.get.mockResolvedValueOnce(payload([], 25))
    history.onPageChange(2)
    await flush()
    expect(history.page.value).toBe(2)
    expect(mocks.get).toHaveBeenLastCalledWith('/tasks', {
      params: { type: 'f2_import', page: 2, size: F2_HISTORY_PAGE_SIZE },
    })
  })

  it('结果减少导致页码越界时回退到最后一页（不留在空页）', async () => {
    mocks.get.mockResolvedValueOnce(payload([], 0))
    const history = useF2TaskHistory()
    history.page.value = 3

    await history.loadTasks()

    expect(history.page.value).toBe(1)
  })

  it('首次加载失败：提示 detail 并返回 false（loading 复位）', async () => {
    mocks.get.mockRejectedValueOnce({ response: { data: { detail: '任务列表不可用' } } })
    const history = useF2TaskHistory()

    await expect(history.loadTasks()).resolves.toBe(false)

    expect(mocks.message.error).toHaveBeenCalledWith('任务列表不可用')
    expect(history.loading.value).toBe(false)
  })

  it('静默刷新失败：不弹错、不 loading，也返回 false', async () => {
    mocks.get.mockRejectedValueOnce(new Error('500'))
    const history = useF2TaskHistory()

    await expect(history.loadTasks({ silent: true })).resolves.toBe(false)

    expect(mocks.message.error).not.toHaveBeenCalled()
    expect(history.loading.value).toBe(false)
  })

  it('hasActive：pending/running 才算进行中，终态不算', async () => {
    mocks.get
      .mockResolvedValueOnce(
        payload([rawTask({ id: 1, status: 'running' }), rawTask({ id: 2, status: 'success' })], 2),
      )
      .mockResolvedValueOnce(payload([rawTask({ id: 1, status: 'success' })], 1))
    const history = useF2TaskHistory()

    await history.loadTasks()
    expect(history.hasActive.value).toBe(true)

    await history.loadTasks()
    expect(history.hasActive.value).toBe(false)
  })

  it('resultTaskIds：只有「批次清单存在且确实入库」的任务才给结果入口', async () => {
    mocks.get.mockResolvedValueOnce(
      payload(
        [
          rawTask({ id: 1 }),
          rawTask({ id: 2, result: { import: { imported: 0, batch_file: 'x.json' } } }),
          rawTask({ id: 3, result: { import: { imported: 3 } } }),
          rawTask({ id: 4, result: null }),
        ],
        4,
      ),
    )
    const history = useF2TaskHistory()

    await history.loadTasks()

    expect([...history.resultTaskIds.value]).toEqual([1])
  })

  it('轮询回调：无进行中任务→停；有任务刷新成功→继续；刷新失败→停', async () => {
    const history = useF2TaskHistory()
    const callback = mocks.pollOptions!.callback

    // 空闲：直接停
    history.tasks.value = []
    await callback()
    expect(mocks.stopPoll).toHaveBeenCalledTimes(1)

    // 进行中 + 成功：不停
    mocks.stopPoll.mockClear()
    mocks.get.mockResolvedValueOnce(payload([rawTask({ id: 1, status: 'running' })], 1))
    history.tasks.value = [normalizeQueueTask(rawTask({ id: 1, status: 'running' }))]
    await callback()
    expect(mocks.message.error).not.toHaveBeenCalled()
    // 刷新后仍是 running，不会停
    expect(mocks.stopPoll).not.toHaveBeenCalled()

    // 进行中 + 刷新失败：停，且静默
    mocks.get.mockRejectedValueOnce(new Error('down'))
    await callback()
    expect(mocks.stopPoll).toHaveBeenCalledTimes(1)
    expect(mocks.message.error).not.toHaveBeenCalled()
  })

  it('hasActive 变化自动启停轮询', async () => {
    const history = useF2TaskHistory()
    expect(mocks.pollOptions?.immediate).toBe(false)

    history.tasks.value = [normalizeQueueTask(rawTask({ id: 1, status: 'running' }))]
    await nextTick()
    expect(mocks.startPoll).toHaveBeenCalledTimes(1)

    history.tasks.value = [normalizeQueueTask(rawTask({ id: 1, status: 'success' }))]
    await nextTick()
    expect(mocks.stopPoll).toHaveBeenCalledTimes(1)
  })

  it('WS task_event：300ms 去抖合并刷新，无 task_id 的事件被忽略', async () => {
    vi.useFakeTimers()
    try {
      const history = useF2TaskHistory()
      const handler = wsHandler()

      handler({})
      handler({ task_id: 'x' })
      await vi.advanceTimersByTimeAsync(500)
      expect(mocks.get).not.toHaveBeenCalled()

      handler({ task_id: 1 })
      handler({ task_id: 2 })
      await vi.advanceTimersByTimeAsync(299)
      expect(mocks.get).not.toHaveBeenCalled()

      await vi.advanceTimersByTimeAsync(1)
      expect(mocks.get).toHaveBeenCalledTimes(1)
      expect(mocks.get).toHaveBeenCalledWith('/tasks', {
        params: { type: 'f2_import', page: 1, size: F2_HISTORY_PAGE_SIZE },
      })
      // 静默刷新：失败不弹错
      expect(mocks.message.error).not.toHaveBeenCalled()
      expect(history.tasks.value).toHaveLength(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('useTaskActions 的两个刷新回调都走静默刷新', async () => {
    useF2TaskHistory()
    const options = mocks.taskActionsOptions!

    options.onQueueTaskDeleted?.()
    await flush()
    expect(mocks.get).toHaveBeenCalledTimes(1)

    options.reload?.()
    await flush()
    expect(mocks.get).toHaveBeenCalledTimes(2)
    expect(mocks.message.error).not.toHaveBeenCalled()
  })
})
