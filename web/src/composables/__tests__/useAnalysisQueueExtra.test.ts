/**
 * AI 分析队列（`useAnalysisQueue`）的行级操作、轮询与 WS 合流测试。
 *
 * 已有 `useAnalysisQueue.test.ts` 覆盖「任务列表过滤 + 创建批量任务」，本文件补
 * 其余部分——它们是**页面上的按钮与后台轮询**，错了用户会以为操作没生效：
 *
 * - 行级暂停/恢复/取消：状态要就地更新，取消 pending 是物理删除（`deleted`）、
 *   running 取消是终态标记（从列表移除）；
 * - 2026-11-16（R2）起 API 内存分析队列下线：旧的「排队素材 / 活动分析 / 整体暂停 /
 *   按素材取消」接口已删除，对应函数只保留兼容占位（不发请求、按钮随空数据隐藏）；
 * - 单条重试（/ai/retry）现在也返回 task_id：要并入任务列表并接管轮询；
 * - 任务轮询：数据源为 `/tasks?type=batch_analyze|multi_analyze` + WS `task_event`，
 *   有未完成任务 3s、空闲 15s；stop 后不再续排；
 * - 批量任务轮询：终态副作用只执行一次、连续失败 5 次才停止（避免网络抖动卡死）；
 * - WS 推送 `task_event` 与轮询合流，不能重复触发副作用。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  requestAndNotify: vi.fn(),
  checkFailureAlert: vi.fn(),
  subscribeWs: vi.fn(),
  isWsConnected: vi.fn(() => false),
  wsHandlers: {} as Record<string, (payload?: unknown) => void>,
}))

vi.mock('@/api/client', () => ({
  default: { get: mocks.get, post: mocks.post },
}))

vi.mock('@/composables/useNotification', () => ({
  useNotification: () => ({
    requestAndNotify: mocks.requestAndNotify,
    checkFailureAlert: mocks.checkFailureAlert,
  }),
}))

vi.mock('@/composables/useWebSocket', () => ({
  subscribeWs: (name: string, cb: (payload?: unknown) => void) => {
    mocks.wsHandlers[name] = cb
    mocks.subscribeWs(name, cb)
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
  for (let i = 0; i < 8; i += 1) await Promise.resolve()
}

function setup(loadHistory = vi.fn()) {
  const api = useAnalysisQueue({ loadHistory })
  return { api, loadHistory }
}

describe('useAnalysisQueue（行级操作与轮询）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.isWsConnected.mockReturnValue(false)
    mocks.get.mockResolvedValue({ data: { items: [] } })
    mocks.post.mockResolvedValue({ data: {} })
  })

  describe('基础加载', () => {
    it('loadPendingQueue 兼容占位：不再请求已下线的排队素材接口', async () => {
      const { api } = setup()

      await api.loadPendingQueue()

      expect(mocks.get).not.toHaveBeenCalled()
      expect(api.pendingQueue.value).toEqual([])
      expect(api.queuePaused.value).toBe(false)
    })

    it('loadQueue 校验统计并触发失败告警；失败静默', async () => {
      mocks.get.mockResolvedValueOnce({
        data: { total: 10, analyzed: 5, unanalyzed: 4, failed: 1 },
      })
      const { api } = setup()

      await api.loadQueue()

      expect(api.queueStats.value).toEqual({ total: 10, analyzed: 5, unanalyzed: 4, failed: 1 })
      expect(mocks.checkFailureAlert).toHaveBeenCalledWith(1, 10)
    })

    it('loadActiveAnalyses 兼容占位：不再请求已下线的活动分析接口', async () => {
      const { api } = setup()

      await api.loadActiveAnalyses()

      expect(mocks.get).not.toHaveBeenCalledWith('/ai/active-analyses')
      expect(api.activeAnalyses.value).toEqual({})
    })
  })

  describe('行级暂停 / 恢复 / 取消', () => {
    it('暂停：提示后端文案，就地改行与 batchTask 状态', async () => {
      mocks.post.mockResolvedValueOnce({ data: { message: '已暂停（自定义）' } })
      const { api } = setup()
      api.analysisTasks.value = [task(7), task(8)]
      api.batchTask.value = task(7)

      await api.pauseTaskById(7)

      expect(mocks.post).toHaveBeenCalledWith('/tasks/7/pause')
      expect(mocks.message.success).toHaveBeenCalledWith('已暂停（自定义）')
      expect(api.analysisTasks.value.find((t: { id: number }) => t.id === 7)!.status).toBe('paused')
      expect(api.batchTask.value!.status).toBe('paused')
    })

    it('暂停失败提示「暂停失败」', async () => {
      mocks.post.mockRejectedValueOnce(null)
      const { api } = setup()
      await api.pauseTaskById(7)
      expect(mocks.message.error).toHaveBeenCalledWith('暂停失败')
    })

    it('恢复：重新拉列表；若恢复的是较新任务则接管轮询', async () => {
      mocks.post.mockResolvedValueOnce({ data: { message: '已恢复' } })
      mocks.get.mockImplementation((url: string) => {
        if (url === '/tasks') return Promise.resolve({ data: { items: [task(9)] } })
        if (url === '/tasks/9') return Promise.resolve({ data: task(9) })
        return Promise.resolve({ data: {} })
      })
      const { api } = setup()
      api.batchTask.value = null

      await api.resumeTaskById(9)

      expect(mocks.message.success).toHaveBeenCalledWith('已恢复')
      expect((api.batchTask.value as TaskInfo | null)?.id).toBe(9) // 接管轮询
      api.stopBatchPolling()
      api.stopPolling()
    })

    it('恢复失败提示「恢复失败」', async () => {
      mocks.post.mockRejectedValueOnce(null)
      const { api } = setup()
      await api.resumeTaskById(9)
      expect(mocks.message.error).toHaveBeenCalledWith('恢复失败')
    })

    it('取消 pending（后端物理删除）：从列表移除并刷新队列', async () => {
      mocks.post.mockResolvedValueOnce({ data: { message: '已删除', deleted: true } })
      const { api } = setup()
      api.analysisTasks.value = [task(1, { status: 'pending' }), task(2)]

      await api.cancelTaskById(1)

      expect(api.analysisTasks.value.map((t: { id: number }) => t.id)).toEqual([2])
      expect(mocks.get).toHaveBeenCalledWith('/ai/queue')
    })

    it('取消 running：标记 cancelled 并从列表移除（终态不展示）', async () => {
      mocks.post.mockResolvedValueOnce({ data: { message: '已取消' } })
      const { api } = setup()
      api.analysisTasks.value = [task(3)]

      await api.cancelTaskById(3)

      expect(api.analysisTasks.value).toHaveLength(0)
    })

    it('取消失败提示「取消失败」', async () => {
      mocks.post.mockRejectedValueOnce(null)
      const { api } = setup()
      await api.cancelTaskById(3)
      expect(mocks.message.error).toHaveBeenCalledWith('取消失败')
    })
  })

  describe('已下线接口的兼容占位（R2 内存队列删除）', () => {
    it('队列整体暂停/恢复：不再请求旧接口，只提示按任务暂停', async () => {
      const { api } = setup()

      await api.togglePauseQueue()

      expect(mocks.post).not.toHaveBeenCalled()
      expect(mocks.message.info).toHaveBeenCalledWith(
        '队列整体暂停已下线，请在任务列表中按任务暂停',
      )
      expect(api.queuePaused.value).toBe(false)
    })

    it('按素材取消排队：不再请求旧 DELETE 接口，只提示去任务列表取消', async () => {
      const { api } = setup()

      await api.cancelQueueItem('insp-1')

      expect(mocks.post).not.toHaveBeenCalled()
      expect(mocks.message.info).toHaveBeenCalledWith(
        '单图分析已并入任务队列，请在下方任务列表中取消',
      )
      expect(api.pendingQueue.value).toEqual([])
    })
  })

  describe('单条重试（/ai/retry 也走 worker 任务队列）', () => {
    it('成功：提示后端文案并把新任务并入任务列表轮询', async () => {
      mocks.post.mockResolvedValueOnce({
        data: {
          task_id: 77,
          message: '已重新加入分析队列（任务 #77）',
          status: 'pending',
        },
      })
      mocks.get.mockImplementation((url: string) => {
        if (url === '/tasks') return Promise.resolve({ data: { items: [task(77)] } })
        if (url === '/tasks/77') return Promise.resolve({ data: task(77) })
        return Promise.resolve({ data: {} })
      })
      const { api } = setup()

      await api.retryAnalysis('insp-1')

      expect(mocks.post).toHaveBeenCalledWith('/ai/retry/insp-1')
      expect(mocks.message.success).toHaveBeenCalledWith('已重新加入分析队列（任务 #77）')
      expect(api.batchTask.value?.id).toBe(77) // 接管轮询
      api.stopBatchPolling()
      api.stopPolling()
    })

    it('Ollama 启动中给警告（任务已入队）', async () => {
      mocks.post.mockResolvedValueOnce({
        data: { task_id: 78, ollama_will_start: true, message: 'Ollama 正在启动' },
      })
      const { api } = setup()

      await api.retryAnalysis('insp-2')

      expect(mocks.message.warning).toHaveBeenCalledWith('Ollama 正在启动')
      api.stopBatchPolling()
      api.stopPolling()
    })

    it('单条重试失败提示「重试失败」', async () => {
      mocks.post.mockRejectedValueOnce(null)
      const { api } = setup()
      await api.retryAnalysis('insp-1')
      expect(mocks.message.error).toHaveBeenCalledWith('重试失败')
    })
  })

  describe('任务轮询（/tasks + task_event）', () => {
    it('startPolling 先拉任务列表，空闲 15s；stopPolling 后不再续排', async () => {
      vi.useFakeTimers()
      try {
        const { api } = setup()

        api.startPolling()
        await flush()
        expect(mocks.get).toHaveBeenCalledWith('/tasks', {
          params: { type: 'batch_analyze', size: 50 },
        })
        expect(mocks.get).toHaveBeenCalledWith('/tasks', {
          params: { type: 'multi_analyze', size: 50 },
        })
        // 旧接口已删除：轮询不得再访问
        expect(mocks.get).not.toHaveBeenCalledWith('/ai/queue/pending')
        expect(mocks.get).not.toHaveBeenCalledWith('/ai/active-analyses')

        await vi.advanceTimersByTimeAsync(15000)
        await flush()
        expect(mocks.get).toHaveBeenCalledWith('/ai/queue')

        api.stopPolling()
        const calls = mocks.get.mock.calls.length
        await vi.advanceTimersByTimeAsync(60000)
        expect(mocks.get.mock.calls.length).toBe(calls)
      } finally {
        vi.useRealTimers()
      }
    })

    it('有未完成任务时用 3s 短轮询并刷新历史；停止后不再续排', async () => {
      vi.useFakeTimers()
      try {
        mocks.get.mockImplementation((url: string) => {
          if (url === '/tasks') {
            return Promise.resolve({ data: { items: [task(11, { status: 'running' })] } })
          }
          if (url === '/ai/queue') {
            return Promise.resolve({
              data: { total: 1, analyzed: 0, unanalyzed: 1, failed: 0 },
            })
          }
          return Promise.resolve({ data: {} })
        })
        const { api, loadHistory } = setup()
        api.analysisTasks.value = [task(11, { status: 'running' })] // wasActive → 3s

        api.startPolling()
        await vi.advanceTimersByTimeAsync(3000)
        await flush()
        expect(loadHistory).toHaveBeenCalled()
        expect(mocks.get).toHaveBeenCalledWith('/ai/queue')

        api.stopPolling()
        await vi.advanceTimersByTimeAsync(30000)
        expect(mocks.get.mock.calls.filter((c) => c[0] === '/ai/queue')).toHaveLength(1)
      } finally {
        vi.useRealTimers()
      }
    })
  })

  describe('批量任务终态合流', () => {
    it('任务列表任务收到 WS task_event 时就地更新并移除终态，同时刷新统计与历史', async () => {
      const { api, loadHistory } = setup()
      api.analysisTasks.value = [task(5, { status: 'running', progress: 10 })]

      mocks.wsHandlers['task_event']({
        task_id: 5,
        status: 'success',
        progress: 100,
        done: 100,
      })

      expect(api.analysisTasks.value).toHaveLength(0) // 终态即移除
      await flush()
      expect(mocks.get).toHaveBeenCalledWith('/ai/queue')
      expect(loadHistory).toHaveBeenCalled()
    })

    it('未知任务事件（其它页面刚创建的任务）会拉一次任务列表纳入展示', async () => {
      mocks.get.mockImplementation((url: string, opts?: { params?: { type?: string } }) => {
        if (url === '/tasks' && opts?.params?.type === 'batch_analyze') {
          return Promise.resolve({ data: { items: [task(99)] } })
        }
        return Promise.resolve({ data: { items: [] } })
      })
      const { api } = setup()

      mocks.wsHandlers['task_event']({
        task_id: 99,
        task_type: 'batch_analyze',
        status: 'running',
      })
      await flush()

      expect(mocks.get).toHaveBeenCalledWith('/tasks', {
        params: { type: 'batch_analyze', size: 50 },
      })
      expect(api.analysisTasks.value.map((t) => t.id)).toEqual([99])
    })

    it('不再订阅已下线的 ai_analysis_done 事件（单图分析改由 task_event 推送）', () => {
      setup()
      expect(mocks.wsHandlers['ai_analysis_done']).toBeUndefined()
    })

    it('批量任务终态副作用只执行一次（轮询 + WS 谁先到都行）', async () => {
      const { api } = setup()
      api.batchTask.value = task(6, { status: 'running' })

      mocks.wsHandlers['task_event']({ task_id: 6, status: 'success' })

      expect(api.batchTask.value!.status).toBe('success')
      expect(mocks.message.success).toHaveBeenCalledTimes(1)
    })
  })

  describe('恢复进行中的分析任务', () => {
    it('resumeBatchAnalyzeTask 拉列表并接管最新非终态任务', async () => {
      mocks.get.mockImplementation((url: string) => {
        if (url === '/tasks') return Promise.resolve({ data: { items: [task(11)] } })
        if (url === '/tasks/11') return Promise.resolve({ data: task(11) })
        return Promise.resolve({ data: {} })
      })
      const { api } = setup()

      await api.resumeBatchAnalyzeTask()

      expect(api.batchTask.value?.id).toBe(11)
      api.stopBatchPolling()
      api.stopPolling()
    })
  })
})
