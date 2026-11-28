/**
 * AI 分析队列（useAnalysisQueue）**补齐用例**：既有两个测试文件覆盖列表过滤、批量创建、行级
 * 操作与轮询主路径，本文件补未覆盖的边界与竞态：
 *
 * - loadAnalysisTasks 并发加载时旧响应不能覆盖新响应；
 * - 取消 pending（deleted）与 404（已被其它入口删除）时被跟踪任务的清理；
 * - triggerBatchAnalyze 的「进行中」保护、Ollama 未就绪警告、失败提示、组合分析任务体；
 * - 批量任务轮询：404 静默停止、连续失败 5 次才报错、失败/取消终态文案；
 * - WS task_event 的无效载荷、迟到终态事件、终态补拉失败回退、非终态合并、列表行更新、
 *   未知任务事件去重刷新；
 * - effectScope 销毁时清理两个轮询定时器。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { effectScope } from 'vue'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  requestAndNotify: vi.fn(),
  checkFailureAlert: vi.fn(),
  isWsConnected: vi.fn(() => false),
  wsHandlers: {} as Record<string, (payload?: unknown) => void>,
}))

vi.mock('@/api/client', () => ({ default: { get: mocks.get, post: mocks.post } }))

vi.mock('@/composables/useNotification', () => ({
  useNotification: () => ({
    requestAndNotify: mocks.requestAndNotify,
    checkFailureAlert: mocks.checkFailureAlert,
  }),
}))

vi.mock('@/composables/useWebSocket', () => ({
  subscribeWs: (name: string, cb: (payload?: unknown) => void) => {
    mocks.wsHandlers[name] = cb
    return vi.fn()
  },
  isWsConnected: () => mocks.isWsConnected(),
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { useAnalysisQueue } from '../useAnalysisQueue'
import type { TaskInfo } from '@/types/analysis'

function task(id: number, overrides: Record<string, unknown> = {}): TaskInfo {
  return {
    id,
    type: 'batch_analyze',
    status: 'running',
    progress: 10,
    total: 100,
    done: 10,
    result: null,
    error: null,
    retry_count: 0,
    max_retries: 2,
    next_retry_at: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  } as TaskInfo
}

async function flush() {
  for (let index = 0; index < 10; index += 1) await Promise.resolve()
}

/** /tasks 列表接口返回空列表，其余接口返回指定数据 */
function mockEmptyLists() {
  mocks.get.mockImplementation((url: string) => {
    if (url === '/tasks') return Promise.resolve({ data: { items: [] } })
    if (url === '/ai/queue') {
      return Promise.resolve({ data: { total: 0, analyzed: 0, unanalyzed: 0, failed: 0 } })
    }
    return Promise.resolve({ data: {} })
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  Object.values(mocks.message).forEach((fn) => fn.mockReset())
  mocks.isWsConnected.mockReturnValue(false)
  mocks.wsHandlers = {}
  mocks.post.mockResolvedValue({ data: {} })
  mockEmptyLists()
})

afterEach(() => {
  vi.clearAllTimers()
  vi.useRealTimers()
})

describe('loadAnalysisTasks 并发竞态', () => {
  it('慢的旧响应不能覆盖新响应', async () => {
    const resolvers: Array<(value: unknown) => void> = []
    let firstBatch = true
    mocks.get.mockImplementation((url: string) => {
      if (url !== '/tasks') return Promise.resolve({ data: {} })
      if (firstBatch) {
        return new Promise((resolve) => {
          resolvers.push(resolve)
        })
      }
      return Promise.resolve({ data: { items: [task(2, { status: 'running' })] } })
    })
    const api = useAnalysisQueue()

    const oldLoad = api.loadAnalysisTasks()
    firstBatch = false
    await api.loadAnalysisTasks()
    expect(api.analysisTasks.value.map((item) => item.id)).toEqual([2])

    // 旧请求后到：应被序号守卫丢弃
    for (const resolve of resolvers) resolve({ data: { items: [task(1, { status: 'running' })] } })
    await oldLoad
    expect(api.analysisTasks.value.map((item) => item.id)).toEqual([2])
  })
})

describe('取消被跟踪任务', () => {
  it('pending 取消=物理删除：列表移除、被跟踪 batchTask 清空并停轮询', async () => {
    vi.useFakeTimers()
    const api = useAnalysisQueue()
    mocks.get.mockImplementation((url: string) => {
      if (url === '/ai/unanalyzed-ids') {
        return Promise.resolve({ data: { ids: ['a'], count: 1 } })
      }
      if (url === '/ai/queue') {
        return Promise.resolve({ data: { total: 1, analyzed: 0, unanalyzed: 1, failed: 0 } })
      }
      if (url === '/tasks/9') return Promise.resolve({ data: task(9, { status: 'running' }) })
      return Promise.resolve({ data: { items: [] } })
    })
    mocks.post.mockImplementation((url: string) => {
      if (url === '/ai/batch-analyze') {
        return Promise.resolve({ data: { task_id: 9, message: 'ok', count: 1 } })
      }
      if (url === '/tasks/9/cancel') return Promise.resolve({ data: { deleted: true } })
      return Promise.resolve({ data: {} })
    })
    await api.triggerBatchAnalyze()
    expect(api.batchTask.value?.id).toBe(9)

    await api.cancelTaskById(9)
    await vi.advanceTimersByTimeAsync(2000)

    expect(api.batchTask.value).toBeNull()
    expect(api.analysisTasks.value.find((item) => item.id === 9)).toBeUndefined()
  })

  it('取消返回 404（已被其它入口删除）：静默移除，不提示失败', async () => {
    const api = useAnalysisQueue()
    api.analysisTasks.value = [task(9, { status: 'running' })] as never
    mocks.post.mockRejectedValueOnce({ response: { status: 404 } })

    await api.cancelTaskById(9)

    expect(api.analysisTasks.value).toEqual([])
    expect(mocks.message.error).not.toHaveBeenCalled()
  })
})

describe('triggerBatchAnalyze 边界', () => {
  it('进行中再次触发：直接返回，不重复请求', async () => {
    const api = useAnalysisQueue()
    api.batchAnalyzing.value = true

    await api.triggerBatchAnalyze()

    expect(mocks.get).not.toHaveBeenCalled()
    expect(mocks.post).not.toHaveBeenCalled()
  })

  it('Ollama 未就绪：warning 提示但任务已创建并接管轮询', async () => {
    vi.useFakeTimers()
    const api = useAnalysisQueue()
    mocks.get.mockImplementation((url: string) => {
      if (url === '/ai/unanalyzed-ids') {
        return Promise.resolve({ data: { ids: ['a', 'b'], count: 2 } })
      }
      if (url === '/tasks/11') return Promise.resolve({ data: task(11, { status: 'running' }) })
      if (url === '/ai/queue') {
        return Promise.resolve({ data: { total: 2, analyzed: 0, unanalyzed: 2, failed: 0 } })
      }
      return Promise.resolve({ data: { items: [] } })
    })
    mocks.post.mockResolvedValueOnce({
      data: {
        task_id: 11,
        message: 'Ollama 未运行，任务已创建',
        count: 2,
        ollama_will_start: true,
      },
    })

    await api.triggerBatchAnalyze()
    await vi.advanceTimersByTimeAsync(0)

    expect(mocks.message.warning).toHaveBeenCalledWith('Ollama 未运行，任务已创建')
    expect(api.batchTask.value?.id).toBe(11)
    expect(api.analysisTasks.value[0]?.id).toBe(11)
    expect(mocks.get).toHaveBeenCalledWith('/tasks/11')
  })

  it('组合分析：body 用对象格式并提示组合数', async () => {
    const api = useAnalysisQueue()
    mocks.get.mockImplementation((url: string) => {
      if (url === '/ai/unanalyzed-ids') {
        return Promise.resolve({ data: { ids: ['a'], count: 1 } })
      }
      if (url === '/ai/queue') {
        return Promise.resolve({ data: { total: 1, analyzed: 0, unanalyzed: 1, failed: 0 } })
      }
      if (url === '/tasks/12') {
        return Promise.resolve({
          data: task(12, { type: 'multi_analyze', status: 'running', total: 3 }),
        })
      }
      return Promise.resolve({ data: { items: [] } })
    })
    mocks.post.mockResolvedValueOnce({
      data: { task_id: 12, message: 'ok', count: 1, combinations: 3 },
    })

    await api.triggerBatchAnalyze(
      { models: ['m1'], promptIds: ['p1'], applyTags: true } as never,
      undefined,
    )
    await flush()

    expect(mocks.post).toHaveBeenCalledWith('/ai/batch-analyze', {
      inspiration_ids: ['a'],
      models: ['m1'],
      prompt_ids: ['p1'],
      apply_tags: true,
    })
    expect(api.batchTask.value?.type).toBe('multi_analyze')
    expect(api.batchTask.value?.total).toBe(3)
    expect(mocks.message.success).toHaveBeenCalledWith(expect.stringContaining('组合分析任务 #12'))
  })

  it('提交失败：提示「批量分析失败」', async () => {
    const api = useAnalysisQueue()
    mocks.get.mockRejectedValueOnce({ response: { data: {} } })

    await api.triggerBatchAnalyze()

    expect(mocks.message.error).toHaveBeenCalledWith('批量分析失败')
    expect(api.batchAnalyzing.value).toBe(false)
  })
})

describe('批量任务轮询边界', () => {
  async function startTrackedTask() {
    vi.useFakeTimers()
    const api = useAnalysisQueue()
    mocks.get.mockImplementation((url: string) => {
      if (url === '/ai/unanalyzed-ids') {
        return Promise.resolve({ data: { ids: ['a'], count: 1 } })
      }
      if (url === '/ai/queue') {
        return Promise.resolve({ data: { total: 1, analyzed: 0, unanalyzed: 1, failed: 0 } })
      }
      if (url === '/tasks/9') return Promise.resolve({ data: task(9, { status: 'running' }) })
      return Promise.resolve({ data: { items: [] } })
    })
    mocks.post.mockResolvedValueOnce({ data: { task_id: 9, message: 'ok', count: 1 } })
    await api.triggerBatchAnalyze()
    await vi.advanceTimersByTimeAsync(0)
    return api
  }

  it('轮询 404：静默停止并清空被跟踪任务', async () => {
    const api = await startTrackedTask()
    mocks.get.mockImplementation((url: string) => {
      if (url === '/tasks/9') return Promise.reject({ response: { status: 404 } })
      return Promise.resolve({ data: { items: [] } })
    })

    await vi.advanceTimersByTimeAsync(1000)

    expect(api.batchTask.value).toBeNull()
    expect(mocks.message.error).not.toHaveBeenCalled()
  })

  it('连续失败 5 次才停止并报错一次', async () => {
    const api = await startTrackedTask()
    mocks.get.mockImplementation((url: string) => {
      if (url === '/tasks/9') return Promise.reject(new Error('network'))
      return Promise.resolve({ data: { items: [] } })
    })

    for (let index = 0; index < 5; index += 1) {
      await vi.advanceTimersByTimeAsync(3000)
    }

    expect(api.batchTask.value).not.toBeNull()
    expect(mocks.message.error).toHaveBeenCalledWith(
      '获取任务状态多次失败，已停止轮询，请稍后手动刷新',
    )
  })

  it('失败终态：error 提示；取消终态：info 提示', async () => {
    const api = await startTrackedTask()
    mocks.get.mockImplementation((url: string) => {
      if (url === '/tasks/9') {
        return Promise.resolve({ data: task(9, { status: 'failed', error: 'OOM' }) })
      }
      return Promise.resolve({ data: { items: [] } })
    })
    await vi.advanceTimersByTimeAsync(1000)
    expect(mocks.message.error).toHaveBeenCalledWith('批量分析失败：OOM')
    void api

    const api2 = await startTrackedTask()
    mocks.get.mockImplementation((url: string) => {
      if (url === '/tasks/9') return Promise.resolve({ data: task(9, { status: 'cancelled' }) })
      return Promise.resolve({ data: { items: [] } })
    })
    await vi.advanceTimersByTimeAsync(1000)
    expect(mocks.message.info).toHaveBeenCalledWith('批量分析任务已取消')
    void api2
  })
})

describe('WS task_event 合流的边界', () => {
  async function setupWithBatchTask() {
    const api = useAnalysisQueue({ loadHistory: vi.fn() })
    mocks.get.mockImplementation((url: string) => {
      if (url === '/ai/unanalyzed-ids') {
        return Promise.resolve({ data: { ids: ['a'], count: 1 } })
      }
      if (url === '/tasks/9') return Promise.resolve({ data: task(9, { status: 'running' }) })
      if (url === '/ai/queue') {
        return Promise.resolve({ data: { total: 1, analyzed: 0, unanalyzed: 1, failed: 0 } })
      }
      return Promise.resolve({ data: { items: [] } })
    })
    mocks.post.mockResolvedValueOnce({ data: { task_id: 9, message: 'ok', count: 1 } })
    await api.triggerBatchAnalyze()
    await flush()
    return api
  }

  it('无效载荷（null / 无 task_id）直接忽略', async () => {
    await setupWithBatchTask()
    mocks.message.success.mockClear()
    mocks.message.warning.mockClear()
    const handler = mocks.wsHandlers['task_event']!
    handler(null)
    handler({ status: 'running' })
    expect(mocks.message.success).not.toHaveBeenCalled()
    expect(mocks.message.error).not.toHaveBeenCalled()
  })

  it('非终态事件：合并进度/状态到 batchTask', async () => {
    vi.useFakeTimers()
    const api = await setupWithBatchTask()
    const handler = mocks.wsHandlers['task_event']!

    handler({ task_id: 9, status: 'running', progress: 55, done: 55, total: 100 })

    expect(api.batchTask.value?.progress).toBe(55)
    expect(api.batchTask.value?.done).toBe(55)
  })

  it('终态事件先到：补拉完整快照（取到 result 后按成功提示）', async () => {
    const api = await setupWithBatchTask()
    const handler = mocks.wsHandlers['task_event']!
    const loadHistory = vi.fn()
    void loadHistory
    mocks.get.mockImplementation((url: string) => {
      if (url === '/tasks/9') {
        return Promise.resolve({
          data: task(9, {
            status: 'success',
            result: { success_count: 3, failed_count: 1 },
          }),
        })
      }
      return Promise.resolve({ data: { items: [] } })
    })

    handler({ task_id: 9, status: 'success' })
    await flush()

    expect(mocks.message.success).toHaveBeenCalledWith('批量分析完成：成功 3，失败 1')
    expect(api.batchTask.value?.status).toBe('success')
  })

  it('终态事件补拉失败：用事件里的 status/error 兜底结算', async () => {
    await setupWithBatchTask()
    const handler = mocks.wsHandlers['task_event']!
    mocks.get.mockImplementation((url: string) => {
      if (url === '/tasks/9') return Promise.reject(new Error('network'))
      return Promise.resolve({ data: { items: [] } })
    })

    handler({ task_id: 9, status: 'failed', error: 'GPU 崩溃' })
    await flush()

    expect(mocks.message.error).toHaveBeenCalledWith('批量分析失败：GPU 崩溃')
  })

  it('迟到/重复的终态事件在已结算后丢弃', async () => {
    await setupWithBatchTask()
    const handler = mocks.wsHandlers['task_event']!
    handler({ task_id: 9, status: 'success' })
    await flush()
    const successCalls = mocks.message.success.mock.calls.length

    handler({ task_id: 9, status: 'failed', error: 'late' })
    await flush()

    expect(mocks.message.success.mock.calls.length).toBe(successCalls)
    expect(mocks.message.error).not.toHaveBeenCalled()
  })

  it('列表里的其它任务：就地更新行；终态时移除并刷新统计/历史', async () => {
    const loadHistory = vi.fn()
    const api = useAnalysisQueue({ loadHistory })
    api.analysisTasks.value = [task(3, { status: 'running', progress: 10 })] as never
    const handler = mocks.wsHandlers['task_event']!

    handler({ task_id: 3, status: 'running', progress: 40, total: 200, error: 'retry later' })
    expect(api.analysisTasks.value[0]?.progress).toBe(40)
    expect(api.analysisTasks.value[0]?.total).toBe(200)
    expect(api.analysisTasks.value[0]?.error).toBe('retry later')

    handler({ task_id: 3, status: 'success' })
    await flush()
    expect(api.analysisTasks.value).toEqual([])
    expect(loadHistory).toHaveBeenCalled()
  })

  it('未知任务事件（batch_analyze）：只拉一次任务列表（在途去重）', async () => {
    const api = useAnalysisQueue()
    api.analysisTasks.value = []
    const handler = mocks.wsHandlers['task_event']!
    mocks.get.mockClear()
    let resolveTasks!: (value: unknown) => void
    mocks.get.mockImplementation((url: string) => {
      if (url === '/tasks') {
        return new Promise((resolve) => {
          resolveTasks = resolve
        })
      }
      return Promise.resolve({ data: {} })
    })

    handler({ task_id: 99, task_type: 'batch_analyze' })
    handler({ task_id: 98, task_type: 'batch_analyze' })
    const calls = mocks.get.mock.calls.filter((call) => call[0] === '/tasks').length
    expect(calls).toBe(4) // 一次 loadAnalysisTasks = 4 个 /tasks 查询

    resolveTasks({ data: { items: [] } })
    await flush()
  })
})

describe('effectScope 销毁清理', () => {
  it('scope.stop() 后两个轮询都不再续排', async () => {
    vi.useFakeTimers()
    const scope = effectScope()
    let api!: ReturnType<typeof useAnalysisQueue>
    scope.run(() => {
      api = useAnalysisQueue()
    })
    mockEmptyLists()

    api.startPolling()
    await vi.advanceTimersByTimeAsync(0)
    mocks.get.mockClear()

    scope.stop()
    await vi.advanceTimersByTimeAsync(60000)

    expect(mocks.get.mock.calls.filter((call) => call[0] === '/tasks')).toHaveLength(0)
  })
})
