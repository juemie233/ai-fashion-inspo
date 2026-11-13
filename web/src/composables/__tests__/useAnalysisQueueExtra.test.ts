/**
 * AI 分析队列（`useAnalysisQueue`）的行级操作、轮询与 WS 合流测试。
 *
 * 已有 `useAnalysisQueue.test.ts` 覆盖「任务列表过滤 + 创建批量任务」，本文件补
 * 其余部分——它们是**页面上的按钮与后台轮询**，错了用户会以为操作没生效：
 *
 * - 行级暂停/恢复/取消：状态要就地更新，取消 pending 是物理删除（`deleted`）、
 *   running 取消是终态标记（从列表移除）；
 * - 队列暂停/恢复、取消排队素材、单条重试：提示文案与刷新；
 * - 活动分析轮询：有活动 3s、WS 已连接 15s、空闲 15s；stop 后不再续排；
 * - 批量任务轮询：终态副作用只执行一次、连续失败 5 次才停止（避免网络抖动卡死）；
 * - WS 推送 `task_event` / `ai_analysis_done` 与轮询合流，不能重复触发副作用。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  del: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  requestAndNotify: vi.fn(),
  checkFailureAlert: vi.fn(),
  subscribeWs: vi.fn(),
  isWsConnected: vi.fn(() => false),
  wsHandlers: {} as Record<string, (payload?: unknown) => void>,
}))

vi.mock('@/api/client', () => ({
  default: { get: mocks.get, post: mocks.post, delete: mocks.del },
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
    mocks.get.mockResolvedValue({ data: { items: [], paused: false } })
    mocks.post.mockResolvedValue({ data: {} })
    mocks.del.mockResolvedValue({ data: {} })
  })

  describe('基础加载', () => {
    it('loadPendingQueue 写入排队素材与暂停态；失败静默', async () => {
      mocks.get.mockResolvedValueOnce({
        data: { items: [{ inspiration_id: 'a' }], paused: true },
      })
      const { api } = setup()

      await api.loadPendingQueue()
      expect(api.pendingQueue.value).toHaveLength(1)
      expect(api.queuePaused.value).toBe(true)

      mocks.get.mockRejectedValueOnce(new Error('500'))
      await api.loadPendingQueue() // 不抛、不提示
      expect(mocks.message.error).not.toHaveBeenCalled()
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

    it('loadActiveAnalyses 写入活动分析；失败静默', async () => {
      mocks.get.mockResolvedValueOnce({ data: { active_analyses: { a: 'running' } } })
      const { api } = setup()

      await api.loadActiveAnalyses()
      expect(api.activeAnalyses.value).toEqual({ a: 'running' })
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
      expect(mocks.get).toHaveBeenCalledWith('/ai/active-analyses')
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

  describe('队列暂停 / 取消排队 / 单条重试', () => {
    it('暂停中的队列点恢复 → resume；否则 pause', async () => {
      const { api } = setup()
      api.queuePaused.value = false
      mocks.post.mockResolvedValue({ data: {} })

      await api.togglePauseQueue()
      expect(mocks.post).toHaveBeenCalledWith('/ai/queue/pause')
      expect(mocks.message.success).toHaveBeenCalledWith('队列已暂停')

      api.queuePaused.value = true
      await api.togglePauseQueue()
      expect(mocks.post).toHaveBeenCalledWith('/ai/queue/resume')
      expect(mocks.message.success).toHaveBeenCalledWith('队列已恢复')
    })

    it('队列暂停操作失败提示「操作失败」', async () => {
      mocks.post.mockRejectedValueOnce(new Error('500'))
      const { api } = setup()
      await api.togglePauseQueue()
      expect(mocks.message.error).toHaveBeenCalledWith('操作失败')
    })

    it('取消排队素材：提示并刷新排队与活动分析', async () => {
      const { api } = setup()
      await api.cancelQueueItem('insp-1')

      expect(mocks.del).toHaveBeenCalledWith('/ai/queue/insp-1')
      expect(mocks.message.success).toHaveBeenCalledWith('已取消')
      expect(mocks.get).toHaveBeenCalledWith('/ai/queue/pending')
      expect(mocks.get).toHaveBeenCalledWith('/ai/active-analyses')
    })

    it('取消排队失败优先用后端 detail', async () => {
      mocks.del.mockRejectedValueOnce({
        response: { data: { detail: '队列已暂停' } },
      })
      const { api } = setup()

      await api.cancelQueueItem('insp-1')

      expect(mocks.message.error).toHaveBeenCalledWith('队列已暂停')
    })

    it('单条重试：成功刷新；Ollama 启动中给警告', async () => {
      const { api } = setup()
      await api.retryAnalysis('insp-1')
      expect(mocks.post).toHaveBeenCalledWith('/ai/retry/insp-1')
      expect(mocks.message.success).toHaveBeenCalledWith('已重新加入队列')

      mocks.post.mockResolvedValueOnce({
        data: { ollama_will_start: true, message: 'Ollama 正在启动' },
      })
      await api.retryAnalysis('insp-2')
      expect(mocks.message.warning).toHaveBeenCalledWith('Ollama 正在启动')
    })

    it('单条重试失败提示「重试失败」', async () => {
      mocks.post.mockRejectedValueOnce(null)
      const { api } = setup()
      await api.retryAnalysis('insp-1')
      expect(mocks.message.error).toHaveBeenCalledWith('重试失败')
    })
  })

  describe('活动分析轮询', () => {
    it('startPolling 立即拉活动分析并按空闲 15s 调度；stopPolling 停止', async () => {
      vi.useFakeTimers()
      try {
        mocks.get.mockResolvedValue({ data: { active_analyses: {} } })
        const { api } = setup()

        api.startPolling()
        await flush()
        expect(mocks.get).toHaveBeenCalledWith('/ai/active-analyses')

        await vi.advanceTimersByTimeAsync(15000)
        // 定时回调里会刷新活动分析 / 排队 / 任务列表 / 统计
        expect(mocks.get).toHaveBeenCalledWith('/ai/queue/pending')

        api.stopPolling()
        const calls = mocks.get.mock.calls.length
        await vi.advanceTimersByTimeAsync(60000)
        expect(mocks.get.mock.calls.length).toBe(calls)
      } finally {
        vi.useRealTimers()
      }
    })

    it('有活动分析时用 3s 短轮询；停止后不再续排', async () => {
      vi.useFakeTimers()
      try {
        mocks.get.mockResolvedValue({ data: { active_analyses: { a: 'running' } } })
        const { api, loadHistory } = setup()
        api.activeAnalyses.value = { a: 'running' } // 让 wasActive=true → 3s

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
    it('任务列表任务收到 WS task_event 时就地更新并移除终态', async () => {
      const { api } = setup()
      api.analysisTasks.value = [task(5, { status: 'running', progress: 10 })]

      mocks.wsHandlers['task_event']({
        task_id: 5,
        status: 'success',
        progress: 100,
        done: 100,
      })

      expect(api.analysisTasks.value).toHaveLength(0) // 终态即移除
    })

    it('ai_analysis_done 推送触发队列/活动分析/历史刷新', async () => {
      const { loadHistory } = setup()

      mocks.wsHandlers['ai_analysis_done']({})

      expect(mocks.get).toHaveBeenCalledWith('/ai/active-analyses')
      expect(mocks.get).toHaveBeenCalledWith('/ai/queue')
      expect(loadHistory).toHaveBeenCalled()
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
