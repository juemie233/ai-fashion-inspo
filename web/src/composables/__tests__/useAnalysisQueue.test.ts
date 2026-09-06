/**
 * useAnalysisQueue 回归测试：标签分析页「分析任务」列表只保留未完成任务——
 * 已完成的批量分析（success/failed/cancelled）不再展示，避免挤占队列区。
 * 完成后的进度与结果可在「任务管理」页 / 分析历史查看。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useAnalysisQueue } from '../useAnalysisQueue'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  delete: vi.fn(),
}))

vi.mock('@/api/client', () => ({ default: mocks }))

interface TaskLike {
  id: number
  type: string
  status: string
  progress: number
  total: number
  done: number
  result: Record<string, unknown> | null
  error: string | null
  retry_count: number
  max_retries: number
  next_retry_at: string | null
  created_at: string
  updated_at: string
}

function makeTask(id: number, status: string, type = 'batch_analyze'): TaskLike {
  return {
    id,
    type,
    status,
    progress: status === 'running' ? 40 : status === 'paused' ? 30 : 100,
    total: 10,
    done: 6,
    result: null,
    error: null,
    retry_count: 0,
    max_retries: 2,
    next_retry_at: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  }
}

function mockTaskFetch(legacy: TaskLike[], multi: TaskLike[] = []) {
  mocks.get.mockImplementation(
    (url: string, opts?: { params?: { type?: string; size?: number } }) => {
      if (url === '/tasks' && opts?.params?.type === 'batch_analyze') {
        return Promise.resolve({ data: { items: legacy } })
      }
      if (url === '/tasks' && opts?.params?.type === 'multi_analyze') {
        return Promise.resolve({ data: { items: multi } })
      }
      return Promise.resolve({ data: { items: [], total: 0 } })
    },
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('useAnalysisQueue.loadAnalysisTasks（已完成批量分析不再展示）', () => {
  it('终态任务（success/failed/cancelled）不进入列表，未完成的任务保留', async () => {
    mockTaskFetch([
      makeTask(1, 'success'),
      makeTask(2, 'running'),
      makeTask(3, 'failed'),
      makeTask(4, 'paused'),
      makeTask(5, 'pending'),
      makeTask(6, 'cancelled'),
    ])

    const { analysisTasks, loadAnalysisTasks } = useAnalysisQueue()
    await loadAnalysisTasks()

    const ids = analysisTasks.value.map((t) => t.id).sort((a, b) => a - b)
    expect(ids).toEqual([2, 4, 5]) // 只剩 running / paused / pending
  })

  it('全部任务都已完成时列表为空', async () => {
    mockTaskFetch([makeTask(1, 'success'), makeTask(2, 'failed')])

    const { analysisTasks, loadAnalysisTasks } = useAnalysisQueue()
    await loadAnalysisTasks()

    expect(analysisTasks.value).toEqual([])
  })

  it('multi_analyze 任务同样只保留未完成项，按 id 倒序', async () => {
    mockTaskFetch(
      [makeTask(1, 'success')],
      [makeTask(7, 'running', 'multi_analyze'), makeTask(8, 'success', 'multi_analyze')],
    )

    const { analysisTasks, loadAnalysisTasks } = useAnalysisQueue()
    await loadAnalysisTasks()

    expect(analysisTasks.value.map((t) => t.id)).toEqual([7])
  })
})
