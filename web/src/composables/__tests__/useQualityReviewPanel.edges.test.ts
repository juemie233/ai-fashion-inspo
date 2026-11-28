/**
 * 质量审核面板（useQualityReviewPanel）**补齐用例**：既有用例覆盖批量审核/随机审核/开关回滚，
 * 本文件补：
 *
 * - 展示计算：batchSubmitCount / batchOverflowHint / batchCostHint（后端均值优先）/
 *   batchButtonLabel（审核中 / 有待审核 / 全部审核完）；
 * - 「手动上传免审核」配置读取成功与失败静默；
 * - 恢复进行中的审核任务（refresh 后接管轮询）；
 * - 「重新审核」提交成功/失败；
 * - 审核任务被取消时的提示；
 * - 轮询停止后在途响应不写状态、不续排；
 * - 随机审核提交失败提示。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  loadRejectedItems: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  default: { get: mocks.get, post: mocks.post, put: mocks.put },
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { useQualityReviewPanel } from '../useQualityReviewPanel'
import { QUALITY_BATCH_MAX, qualityBatchHint } from '@/utils/qualityBatch'

function stats(overrides: Record<string, unknown> = {}) {
  return {
    total: 100,
    pending: 40,
    approved: 50,
    rejected: 10,
    pass_rate: 0.83,
    ai_generated: 5,
    avg_seconds: 12,
    ...overrides,
  }
}

function reviewTaskSnapshot(overrides: Record<string, unknown> = {}) {
  return {
    id: 5,
    type: 'quality_check',
    status: 'running',
    progress: 10,
    total: 40,
    done: 4,
    result: null,
    error: null,
    retry_count: 0,
    max_retries: 2,
    next_retry_at: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

function setup() {
  return useQualityReviewPanel({ loadRejectedItems: mocks.loadRejectedItems })
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.useFakeTimers()
  localStorage.clear()
  mocks.loadRejectedItems.mockResolvedValue(undefined)
  mocks.get.mockImplementation((url: string) => {
    if (url.startsWith('/tasks/')) {
      const id = Number(url.split('/')[2])
      return Promise.resolve({ data: reviewTaskSnapshot({ id }) })
    }
    return Promise.resolve({ data: stats() })
  })
  mocks.post.mockResolvedValue({ data: { message: 'ok', count: 10, task_id: 5 } })
  mocks.put.mockResolvedValue({ data: {} })
})

afterEach(() => {
  vi.clearAllTimers()
  vi.useRealTimers()
})

describe('展示计算', () => {
  it('batchSubmitCount 取 min(待审核, 上限)；超上限给出溢出提示与耗时提示', async () => {
    const api = setup()
    mocks.get.mockResolvedValue({ data: stats({ pending: QUALITY_BATCH_MAX + 30 }) })
    await api.loadQualityReview()

    expect(api.batchSubmitCount.value).toBe(QUALITY_BATCH_MAX)
    expect(api.batchOverflowHint.value).toContain(`剩余 ${30} 条`)
    expect(api.batchCostHint.value).toBe(
      qualityBatchHint(QUALITY_BATCH_MAX, 1, stats().avg_seconds),
    )

    // 未超上限：无溢出提示，提交数=待审核数
    mocks.get.mockResolvedValue({ data: stats({ pending: 5 }) })
    await api.loadQualityReview()
    expect(api.batchSubmitCount.value).toBe(5)
    expect(api.batchOverflowHint.value).toBe('')
  })

  it('batchButtonLabel：审核中 / 有待审核 / 全部已审核 三种文案', async () => {
    const api = setup()
    await api.loadQualityReview()
    expect(api.batchButtonLabel.value).toBe('审核全部待审核 (40)')

    api.reviewTask.value = reviewTaskSnapshot({ status: 'running' }) as never
    expect(api.batchButtonLabel.value).toBe('审核进行中…')

    api.reviewTask.value = null
    mocks.get.mockResolvedValue({ data: stats({ pending: 0 }) })
    await api.loadQualityReview()
    expect(api.batchButtonLabel.value).toBe('全部已审核')
  })
})

describe('手动上传免审核配置读取', () => {
  it('成功：写入 enabled；失败：静默保留默认值', async () => {
    const api = setup()
    mocks.get.mockResolvedValueOnce({ data: { enabled: false } })
    await api.loadAutoApprove()
    expect(api.autoApproveEnabled.value).toBe(false)

    mocks.get.mockRejectedValueOnce(new Error('500'))
    await api.loadAutoApprove()
    expect(api.autoApproveEnabled.value).toBe(false) // 不被失败清掉
  })
})

describe('恢复进行中的审核任务', () => {
  it('找到 pending/running 任务：写入并接管轮询', async () => {
    const api = setup()
    mocks.get.mockImplementation((url: string) => {
      if (url === '/tasks') {
        return Promise.resolve({ data: { items: [reviewTaskSnapshot({ id: 9 })] } })
      }
      return Promise.resolve({ data: reviewTaskSnapshot({ id: 9 }) })
    })

    await api.resumeReviewTask()
    await vi.advanceTimersByTimeAsync(0)

    expect(mocks.get).toHaveBeenCalledWith('/tasks', {
      params: { type: 'quality_check', size: 20 },
    })
    expect(api.reviewTask.value?.id).toBe(9)
    expect(mocks.get).toHaveBeenCalledWith('/tasks/9')
  })

  it('没有进行中的任务：不改状态；请求失败静默', async () => {
    const api = setup()
    mocks.get.mockResolvedValueOnce({
      data: { items: [reviewTaskSnapshot({ status: 'success' })] },
    })
    await api.resumeReviewTask()
    expect(api.reviewTask.value).toBeNull()

    mocks.get.mockRejectedValueOnce(new Error('500'))
    await expect(api.resumeReviewTask()).resolves.toBeUndefined()
  })
})

describe('重新审核', () => {
  it('成功：提交 /ai/quality-recheck，提示任务号并开始轮询', async () => {
    const api = setup()
    mocks.post.mockResolvedValueOnce({
      data: { message: '已提交', count: 12, task_id: 33 },
    })

    await api.recheckQuality()
    await vi.advanceTimersByTimeAsync(0)

    expect(mocks.post).toHaveBeenCalledWith('/ai/quality-recheck')
    expect(mocks.message.success).toHaveBeenCalledWith('已提交重新审核任务 #33，共 12 个素材')
    expect(api.rechecking.value).toBe(false)
    expect(api.reviewTask.value?.id).toBe(33)
    expect(mocks.get).toHaveBeenCalledWith('/tasks/33')
  })

  it('失败：提示「重新审核提交失败」，rechecking 复位、不建任务', async () => {
    const api = setup()
    mocks.post.mockRejectedValueOnce({ response: { data: { detail: '队列不可用' } } })

    await api.recheckQuality()

    expect(mocks.message.error).toHaveBeenCalledWith('队列不可用')
    expect(api.rechecking.value).toBe(false)
    expect(api.reviewTask.value).toBeNull()
  })
})

describe('审核任务轮询的取消与停止', () => {
  it('任务被取消：info 提示「审核任务已取消」，并刷新统计与未通过列表', async () => {
    const api = setup()
    mocks.get.mockImplementation((url: string) => {
      if (url === '/tasks') {
        return Promise.resolve({ data: { items: [reviewTaskSnapshot({ id: 7 })] } })
      }
      if (url === '/tasks/7') {
        return Promise.resolve({ data: reviewTaskSnapshot({ id: 7, status: 'cancelled' }) })
      }
      return Promise.resolve({ data: stats() })
    })

    await api.resumeReviewTask()
    await vi.advanceTimersByTimeAsync(0)

    expect(mocks.message.info).toHaveBeenCalledWith('审核任务已取消')
    expect(mocks.loadRejectedItems).toHaveBeenCalled()
  })

  it('stopReviewPolling 后：在途轮询响应被丢弃，不再续排', async () => {
    const api = setup()
    let resolvePoll!: (value: unknown) => void
    let pollCalls = 0
    mocks.get.mockImplementation((url: string) => {
      if (url === '/tasks') {
        return Promise.resolve({ data: { items: [reviewTaskSnapshot({ id: 7 })] } })
      }
      if (url === '/tasks/7') {
        pollCalls += 1
        return new Promise((resolve) => {
          resolvePoll = resolve
        })
      }
      return Promise.resolve({ data: stats() })
    })
    await api.resumeReviewTask()
    await vi.advanceTimersByTimeAsync(0)
    expect(pollCalls).toBe(1)

    api.stopReviewPolling()
    resolvePoll({ data: reviewTaskSnapshot({ id: 7, status: 'running' }) })
    await Promise.resolve()
    await vi.advanceTimersByTimeAsync(5000)

    // 停止后不再发起新的轮询请求
    expect(pollCalls).toBe(1)
  })
})

describe('随机审核失败', () => {
  it('提交失败：提示「随机审核提交失败」并复位 randomChecking', async () => {
    const api = setup()
    mocks.post.mockRejectedValueOnce({ response: { data: { detail: 'random 500' } } })

    await api.randomQualityCheck()

    expect(mocks.message.error).toHaveBeenCalledWith('random 500')
    expect(api.randomChecking.value).toBe(false)
  })
})
