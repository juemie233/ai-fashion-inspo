/**
 * 标签分析任务（`useTagAnalysisTask`）的补测：覆盖「提交 → 轮询 → 结果转型 → 回调」主链路。
 *
 * 既有 `useTagAnalysisTask.test.ts` 只测 pause/resume；本文件补：
 * - run 提交任务后把 task_id 交给 useTaskPolling，并在轮询回调里做结果转型；
 * - transform 返回 null（结构不符）时不写结果、不触发 onDone；
 * - 运行中重复 run 被忽略（防止连点起两个后台任务）；
 * - 提交失败走 onError、不进入轮询；submitting/running 状态随时间正确变化。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  pollTask: vi.fn(),
  stopPolling: vi.fn(),
  taskRef: null as { value: { id: number; status: string } | null } | null,
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))

vi.mock('@/api/client', () => ({ default: { post: mocks.post } }))
vi.mock('@/composables/useTaskPolling', () => ({
  useTaskPolling: () => ({
    task: mocks.taskRef,
    pollTask: mocks.pollTask,
    stopPolling: mocks.stopPolling,
  }),
}))
vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { useTagAnalysisTask } from '../useTagAnalysisTask'

interface HealthResult {
  total: number
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

/** 记录 pollTask 注册的回调，供用例手动触发（等价于 WS/轮询把任务快照推来） */
function capturePollCallback() {
  let callback: ((raw: Record<string, unknown> | null) => void) | null = null
  mocks.pollTask.mockImplementation(
    (_id: number, cb: (raw: Record<string, unknown> | null) => void) => {
      callback = cb
    },
  )
  return () => {
    if (!callback) throw new Error('pollTask 尚未注册回调')
    return callback
  }
}

const submitOk = () => ({ task_id: 42 })

describe('useTagAnalysisTask 运行主链路', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.taskRef = ref(null)
    mocks.pollTask.mockReset()
  })

  it('初始：running=false、paused=false、result=null', () => {
    const t = useTagAnalysisTask<HealthResult>({
      submit: vi.fn(),
      transform: () => null,
    })

    expect(t.running.value).toBe(false)
    expect(t.paused.value).toBe(false)
    expect(t.result.value).toBeNull()
  })

  it('run 成功：提交任务、按 task_id 启动轮询；回调转型后写结果并触发 onDone', async () => {
    const getCallback = capturePollCallback()
    const onDone = vi.fn()
    const submit = vi.fn().mockResolvedValue(submitOk())
    const transform = vi.fn((raw: Record<string, unknown> | null) =>
      raw ? ({ total: Number(raw.total) } as HealthResult) : null,
    )
    const t = useTagAnalysisTask<HealthResult>({ submit, transform, onDone })

    await t.run()

    expect(submit).toHaveBeenCalledTimes(1)
    expect(mocks.pollTask).toHaveBeenCalledWith(42, expect.any(Function))

    getCallback()!({ total: 7 })
    expect(transform).toHaveBeenCalledWith({ total: 7 })
    expect(t.result.value).toEqual({ total: 7 })
    expect(onDone).toHaveBeenCalledWith({ total: 7 })
  })

  it('transform 返回 null（结果结构不符）：不写结果、不触发 onDone', async () => {
    const getCallback = capturePollCallback()
    const onDone = vi.fn()
    const t = useTagAnalysisTask<HealthResult>({
      submit: vi.fn().mockResolvedValue(submitOk()),
      transform: () => null,
      onDone,
    })
    await t.run()

    getCallback()!(null)

    expect(t.result.value).toBeNull()
    expect(onDone).not.toHaveBeenCalled()
  })

  it('提交失败：走 onError、不启动轮询、submitting 复位', async () => {
    const onError = vi.fn()
    const err = new Error('提交失败')
    const t = useTagAnalysisTask<HealthResult>({
      submit: vi.fn().mockRejectedValue(err),
      transform: () => null,
      onError,
    })

    await t.run()

    expect(onError).toHaveBeenCalledWith(err)
    expect(mocks.pollTask).not.toHaveBeenCalled()
    expect(t.running.value).toBe(false)
  })

  it('运行中重复 run 被忽略（只提交一次）', async () => {
    const pending = deferred<{ task_id: number }>()
    const submit = vi.fn().mockReturnValue(pending.promise)
    const t = useTagAnalysisTask<HealthResult>({
      submit,
      transform: () => null,
    })

    const first = t.run()
    expect(t.running.value).toBe(true) // submitting 期间即视为运行中
    await t.run() // 第二次应直接返回
    expect(submit).toHaveBeenCalledTimes(1)

    pending.resolve(submitOk())
    await first
    expect(mocks.pollTask).toHaveBeenCalledTimes(1)
  })

  it('running 随任务状态变化：pending/running 为真，终态为假', async () => {
    const t = useTagAnalysisTask<HealthResult>({
      submit: vi.fn().mockResolvedValue(submitOk()),
      transform: () => null,
    })

    mocks.taskRef!.value = { id: 42, status: 'pending' }
    expect(t.running.value).toBe(true)

    mocks.taskRef!.value = { id: 42, status: 'running' }
    expect(t.running.value).toBe(true)

    mocks.taskRef!.value = { id: 42, status: 'success' }
    expect(t.running.value).toBe(false)

    mocks.taskRef!.value = { id: 42, status: 'paused' }
    expect(t.running.value).toBe(false)
    expect(t.paused.value).toBe(true)
  })

  it('resume 失败：透出后端 detail（补既有测试未覆盖的恢复失败分支）', async () => {
    mocks.taskRef!.value = { id: 42, status: 'paused' }
    mocks.post.mockRejectedValue({ response: { data: { detail: '断点已失效' } } })
    const t = useTagAnalysisTask<HealthResult>({
      submit: vi.fn(),
      transform: () => null,
    })

    await t.resume()

    expect(mocks.post).toHaveBeenCalledWith('/tasks/42/resume')
    expect(mocks.message.error).toHaveBeenCalledWith('断点已失效')
  })

  it('暴露的 stopPolling 就是 useTaskPolling 的 stopPolling（供面板卸载时清理）', () => {
    const t = useTagAnalysisTask<HealthResult>({
      submit: vi.fn(),
      transform: () => null,
    })

    expect(t.stopPolling).toBe(mocks.stopPolling)
  })

  it('轮询回调可被多次调用：结果以最后一次转型为准（任务快照推进）', async () => {
    const getCallback = capturePollCallback()
    const t = useTagAnalysisTask<HealthResult>({
      submit: vi.fn().mockResolvedValue(submitOk()),
      transform: (raw) => (raw ? ({ total: Number(raw.total) } as HealthResult) : null),
    })
    await t.run()

    getCallback()!({ total: 1 })
    getCallback()!({ total: 9 })

    expect(t.result.value).toEqual({ total: 9 })
  })
})
