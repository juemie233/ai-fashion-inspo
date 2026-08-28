/**
 * 标签分析任务 composable（useTagAnalysisTask）暂停/恢复回归测试：
 * - paused 派生状态：仅任务状态为 paused 时为真（running 随之失效）；
 * - pause() / resume() 调用对应接口并展示后端消息；
 * - 状态不符时静默不调用（按钮不可达场景的兜底）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { useTagAnalysisTask } from '../useTagAnalysisTask'
import type { TaskStatus } from '@/types/tagAdvanced'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  // 惰性初始化：vi.hoisted 阶段不能引用 vue 的 ref（import 尚未就绪），
  // 由 beforeEach 赋值为 ref；mock 工厂每次调用时读取当前值
  taskRef: null as { value: TaskStatus | null } | null,
}))

vi.mock('@/api/client', () => ({ default: mocks }))
vi.mock('@/composables/useTaskPolling', () => ({
  useTaskPolling: () => ({
    task: mocks.taskRef,
    pollTask: vi.fn(),
    stopPolling: vi.fn(),
  }),
}))

const msgMock = () => ({ close: () => {} })

function makeTask(over: Partial<TaskStatus> = {}): TaskStatus {
  return {
    id: 7,
    type: 'tag_network_analyze',
    status: 'running',
    progress: 30,
    total: 100,
    done: 30,
    result: null,
    error: null,
    ...over,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.taskRef = ref<TaskStatus | null>(null)
})

describe('useTagAnalysisTask 暂停/恢复', () => {
  it('paused 派生：任务为 paused 时 paused=true 且 running=false', () => {
    const { running, paused } = useTagAnalysisTask<null>({
      submit: vi.fn(),
      transform: () => null,
    })
    expect(paused.value).toBe(false)
    expect(running.value).toBe(false)

    mocks.taskRef!.value = makeTask({ status: 'paused' })
    expect(paused.value).toBe(true)
    expect(running.value).toBe(false)

    mocks.taskRef!.value = makeTask({ status: 'running' })
    expect(paused.value).toBe(false)
    expect(running.value).toBe(true)
  })

  it('pause()：运行中任务调用 /tasks/{id}/pause 并展示后端消息', async () => {
    const successSpy = vi.spyOn(Message, 'success').mockImplementation(msgMock)
    mocks.taskRef!.value = makeTask({ status: 'running' })
    mocks.post.mockResolvedValue({ data: { message: '任务已暂停' } })

    const { pause } = useTagAnalysisTask<null>({
      submit: vi.fn(),
      transform: () => null,
    })
    await pause()

    expect(mocks.post).toHaveBeenCalledWith('/tasks/7/pause')
    expect(successSpy).toHaveBeenCalledWith('任务已暂停')
  })

  it('pause()：非运行中任务静默不调用', async () => {
    mocks.taskRef!.value = makeTask({ status: 'paused' })

    const { pause } = useTagAnalysisTask<null>({
      submit: vi.fn(),
      transform: () => null,
    })
    await pause()

    expect(mocks.post).not.toHaveBeenCalled()
  })

  it('resume()：已暂停任务调用 /tasks/{id}/resume 并展示后端消息', async () => {
    const successSpy = vi.spyOn(Message, 'success').mockImplementation(msgMock)
    mocks.taskRef!.value = makeTask({ status: 'paused' })
    mocks.post.mockResolvedValue({ data: { message: '任务已恢复' } })

    const { resume } = useTagAnalysisTask<null>({
      submit: vi.fn(),
      transform: () => null,
    })
    await resume()

    expect(mocks.post).toHaveBeenCalledWith('/tasks/7/resume')
    expect(successSpy).toHaveBeenCalledWith('任务已恢复')
  })

  it('resume()：非暂停任务静默不调用', async () => {
    mocks.taskRef!.value = makeTask({ status: 'running' })

    const { resume } = useTagAnalysisTask<null>({
      submit: vi.fn(),
      transform: () => null,
    })
    await resume()

    expect(mocks.post).not.toHaveBeenCalled()
  })

  it('接口 400 拒绝时展示错误详情', async () => {
    const errorSpy = vi.spyOn(Message, 'error').mockImplementation(msgMock)
    mocks.taskRef!.value = makeTask({ status: 'running' })
    mocks.post.mockRejectedValue({
      response: {
        data: { detail: '仅运行中的 tag_network_analyze 任务可暂停（当前状态 running）' },
      },
    })

    const { pause } = useTagAnalysisTask<null>({
      submit: vi.fn(),
      transform: () => null,
    })
    await pause()

    expect(errorSpy).toHaveBeenCalledWith(
      '仅运行中的 tag_network_analyze 任务可暂停（当前状态 running）',
    )
  })
})
