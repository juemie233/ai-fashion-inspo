/**
 * 博主评分补全（`useRatingBackfill`）测试。
 *
 * 这条链路是「提交后台任务 → 轮询 → 刷新列表」，写错的典型表现：
 * - 星级非法也发任务（给全部未评分素材写 0 分/错分）；
 * - 提交成功后按钮空窗期不转圈，连点建出多个任务；
 * - 任务失败却弹「完成」提示（假装成功）；
 * - 轮询回调执行后不刷新列表（均分永远不变）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  backfillRatings: vi.fn(),
  pollTask: vi.fn(),
  stopPolling: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  onFinished: vi.fn(),
  task: null as unknown,
}))

vi.mock('@/api/persons', () => ({
  bloggersApi: { backfillRatings: mocks.backfillRatings },
}))

vi.mock('@/api/client', () => ({ default: { get: vi.fn() } }))

// useTaskPolling 在模块里会订阅 WS：这里换成纯桩，测试只验证「用没用、怎么用」
vi.mock('@/composables/useTaskPolling', async () => {
  const { ref } = await import('vue')
  const task = ref(null)
  mocks.task = task
  return {
    useTaskPolling: () => ({ task, pollTask: mocks.pollTask, stopPolling: mocks.stopPolling }),
  }
})

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { useRatingBackfill } from '../useRatingBackfill'

/** 取轮询成功回调：pollTask(taskId, onDone) 的第二个参数 */
function pollCallback(): (result: Record<string, unknown> | null) => void {
  return mocks.pollTask.mock.calls[0]![1] as (result: Record<string, unknown> | null) => void
}

function setup() {
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
  const api = useRatingBackfill(mocks.onFinished)
  warn.mockRestore()
  // 上一个用例残留的任务快照不能带进下一个
  ;(mocks.task as { value: unknown }).value = null
  return api
}

describe('useRatingBackfill', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.backfillRatings.mockResolvedValue({ task_id: 42, total: 10, message: '已提交补全任务' })
  })

  it.each([0, 6, 1.5, Number.NaN])('星级 %s 非法：警告且不发任务', async (rating) => {
    const api = setup()

    await api.start(rating)

    expect(mocks.message.warning).toHaveBeenCalledWith('请选择 1~5 星评分')
    expect(mocks.backfillRatings).not.toHaveBeenCalled()
    expect(mocks.pollTask).not.toHaveBeenCalled()
  })

  it('提交成功后占位任务快照、启动轮询并显示进度', async () => {
    let resolveSubmit!: (value: unknown) => void
    mocks.backfillRatings.mockReturnValue(
      new Promise((resolve) => {
        resolveSubmit = resolve
      }),
    )
    const api = setup()

    const pending = api.start(5)
    // 提交中：按钮应转圈（防连点建出多个任务）
    expect(api.running.value).toBe(true)

    resolveSubmit({ task_id: 42, total: 10, message: '已提交补全任务' })
    await pending

    expect(mocks.backfillRatings).toHaveBeenCalledWith(5, undefined)
    expect(mocks.message.success).toHaveBeenCalledWith('已提交补全任务')
    expect(mocks.pollTask).toHaveBeenCalledWith(42, expect.any(Function))
    expect((mocks.task as { value: { type: string; total: number } }).value.type).toBe(
      'blogger_rating_backfill',
    )
    expect(api.progressText.value).toBe('0/10')
    expect(api.running.value).toBe(true)
  })

  it('轮询到成功：提示已补素材数并刷新列表，running 结束', async () => {
    const api = setup()
    await api.start(4, [1, 2])

    expect(mocks.backfillRatings).toHaveBeenCalledWith(4, [1, 2])

    // 模拟任务进入终态（useTaskPolling 由桩控制，这里直接改快照模拟轮询结果）
    ;(mocks.task as { value: unknown }).value = {
      id: 42,
      type: 'blogger_rating_backfill',
      status: 'success',
      progress: 100,
      total: 10,
      done: 10,
      error: null,
      result: { updated_materials: 8, skipped: 2 },
    }
    pollCallback()({ updated_materials: 8, skipped: 2 })

    expect(mocks.message.success).toHaveBeenCalledWith(
      '评分补全完成：已补 8 个素材 · 跳过 2 位博主',
    )
    expect(mocks.onFinished).toHaveBeenCalledTimes(1)
    expect(api.progressText.value).toBe('') // 终态不再显示进度
    expect(api.running.value).toBe(false)
  })

  it('一个都没补上：提示 warning 而不是假装成功', async () => {
    const api = setup()
    await api.start(3)

    pollCallback()({ updated: 0, skipped: 5, failed: 1 })

    expect(mocks.message.success).not.toHaveBeenCalledWith(expect.stringContaining('评分补全完成'))
    expect(mocks.message.warning).toHaveBeenCalledWith(
      '没有补上评分（跳过 5 位博主、失败 1 位博主）：这些博主的素材可能都已经评过分',
    )
    expect(mocks.onFinished).toHaveBeenCalledTimes(1)
  })

  it('提交失败：提示错误、不启动轮询、running 复位', async () => {
    // 与 axios 错误同形：后端 detail 在 response.data 里
    mocks.backfillRatings.mockRejectedValue({ response: { data: { detail: '已有补全任务在跑' } } })
    const api = setup()

    await api.start(5)

    expect(mocks.message.error).toHaveBeenCalledWith('已有补全任务在跑')
    expect(mocks.pollTask).not.toHaveBeenCalled()
    expect(api.running.value).toBe(false)
  })

  it('stop 会停止轮询（组件卸载时避免回调打到已销毁界面）', () => {
    const api = setup()
    api.stop()
    expect(mocks.stopPolling).toHaveBeenCalledTimes(1)
  })
})
