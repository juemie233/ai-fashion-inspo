/** useAvatarFill 测试：提交头像补全任务 → 轮询 → 终态提示与列表刷新。
 *
 * 关注四件事：① 提交即开始轮询（任务 id 来自接口返回）；
 * ② 成功结果带 updated>0 时提示「成功 N 位」并刷新列表；
 * ③ updated=0（全是跳过/失败）时给出可操作的原因提示，而不是假装成功；
 * ④ 接口报错时不再轮询。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Ref } from 'vue'
import { Message } from '@arco-design/web-vue'

import { useAvatarFill } from '../useAvatarFill'

const mocks = vi.hoisted(() => ({
  fillMissingAvatars: vi.fn(),
  pollTask: vi.fn(),
  stopPolling: vi.fn(),
  // 真实 ref 由下面的异步 mock 工厂补上（hoisted 阶段拿不到 vue）
  taskRef: null as unknown as Ref<Record<string, unknown> | null>,
}))

vi.mock('@/api/persons', () => ({ fillMissingAvatars: mocks.fillMissingAvatars }))
vi.mock('@/composables/useTaskPolling', async () => {
  const { ref } = await import('vue')
  mocks.taskRef = ref(null)
  return {
    useTaskPolling: () => ({
      task: mocks.taskRef,
      pollTask: mocks.pollTask,
      stopPolling: mocks.stopPolling,
    }),
  }
})

/** 取出 pollTask 收到的 onDone 回调（提交后由它处理终态） */
function capturedOnDone(): (result: Record<string, unknown> | null) => void {
  const call = mocks.pollTask.mock.calls.at(0)
  expect(call).toBeTruthy()
  return call![1] as (result: Record<string, unknown> | null) => void
}

describe('useAvatarFill', () => {
  beforeEach(() => {
    mocks.fillMissingAvatars.mockReset()
    mocks.pollTask.mockReset()
    mocks.stopPolling.mockReset()
    mocks.taskRef.value = null
    vi.restoreAllMocks()
  })

  it('提交成功后按返回的任务 id 开始轮询', async () => {
    mocks.fillMissingAvatars.mockResolvedValue({
      task_id: 42,
      total: 386,
      message: '本次将为 386 位博主补全头像',
    })
    const success = vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
    const onFinished = vi.fn()
    const { fillAvatars, running } = useAvatarFill(onFinished)

    await fillAvatars()

    expect(mocks.fillMissingAvatars).toHaveBeenCalledWith(undefined, false)
    expect(success).toHaveBeenCalledWith('本次将为 386 位博主补全头像')
    expect(mocks.pollTask).toHaveBeenCalledWith(42, expect.any(Function))
    // 只提交、任务还没终态：列表先不刷新，等 onDone
    expect(onFinished).not.toHaveBeenCalled()
    // 提交后立刻进入 running（占位快照），否则首次轮询前的空窗期能再点一次建出两个任务
    expect(running.value).toBe(true)
  })

  it('终态 updated>0：提示成功计数并刷新列表', async () => {
    const success = vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
    const onFinished = vi.fn()
    const { fillAvatars } = useAvatarFill(onFinished)
    mocks.fillMissingAvatars.mockResolvedValue({ task_id: 1, total: 2, message: '开始' })
    await fillAvatars()

    capturedOnDone()({ updated: 380, skipped: 6, failed: 2 })

    expect(success).toHaveBeenCalledWith('头像补全完成：成功 380 位 · 跳过 6 · 失败 2')
    expect(onFinished).toHaveBeenCalledTimes(1)
  })

  it('终态 updated=0：提示「没有补上」并说明来源，不谎报成功', async () => {
    const warning = vi.spyOn(Message, 'warning').mockImplementation((() => {}) as never)
    const success = vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
    const onFinished = vi.fn()
    const { fillAvatars } = useAvatarFill(onFinished)
    mocks.fillMissingAvatars.mockResolvedValue({ task_id: 1, total: 2, message: '开始' })
    await fillAvatars()

    capturedOnDone()({ updated: 0, skipped: 6, failed: 0 })

    expect(success).not.toHaveBeenCalledWith(expect.stringContaining('完成'))
    expect(warning).toHaveBeenCalledWith(expect.stringContaining('没有补上头像'))
    expect(warning).toHaveBeenCalledWith(expect.stringContaining('素材'))
    expect(onFinished).toHaveBeenCalledTimes(1)
  })

  it('接口报错：提示错误且不启动轮询', async () => {
    const error = vi.spyOn(Message, 'error').mockImplementation((() => {}) as never)
    mocks.fillMissingAvatars.mockRejectedValue({
      response: { data: { detail: '没有可补头像的博主' } },
    })
    const { fillAvatars } = useAvatarFill(vi.fn())

    await fillAvatars()

    expect(error).toHaveBeenCalledWith('没有可补头像的博主')
    expect(mocks.pollTask).not.toHaveBeenCalled()
  })

  it('任务进行中时 running=true 且按钮文案按 done/total 显示；stop 会停轮询', async () => {
    const { fillAvatars, running, progressText, stop } = useAvatarFill(vi.fn())
    mocks.fillMissingAvatars.mockResolvedValue({ task_id: 7, total: 386, message: '开始' })
    await fillAvatars()

    mocks.taskRef.value = { id: 7, status: 'running', done: 12, total: 386 }
    expect(running.value).toBe(true)
    expect(progressText.value).toBe('12/386')

    mocks.taskRef.value = { id: 7, status: 'success', done: 386, total: 386 }
    expect(running.value).toBe(false)
    expect(progressText.value).toBe('')

    stop()
    expect(mocks.stopPolling).toHaveBeenCalled()
  })
})
