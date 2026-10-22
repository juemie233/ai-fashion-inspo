/**
 * 人脸扫描任务控制（`useFaceScanActions`）的测试。
 *
 * 这里是**写路径**：开始扫描 / 取消 / 全库重匹配。三个动作的共同风险是
 * 「以为做了但没做」——按钮转一下圈就恢复、没有任何数据变化：
 * - 创建任务失败必须提示（否则用户以为已排上队，等着一个不存在的任务）；
 * - 取消要能取消**正在跑的扫描或匹配**（两个任务并存时取哪个）；
 * - 创建成功后要刷新任务列表并开始轮询，否则界面永远显示「未开始」。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

const mocks = vi.hoisted(() => ({
  startFaceScan: vi.fn(),
  fetchFaceScanTask: vi.fn(),
  runFaceMatch: vi.fn(),
  clientPost: vi.fn(),
  pollTaskUntilIdle: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
}))

vi.mock('@/api/faceScan', () => ({
  startFaceScan: mocks.startFaceScan,
  fetchFaceScanTask: mocks.fetchFaceScanTask,
  runFaceMatch: mocks.runFaceMatch,
}))

vi.mock('@/api/client', () => ({
  default: { post: mocks.clientPost },
}))

vi.mock('@/utils/taskPollUntilIdle', () => ({
  pollTaskUntilIdle: mocks.pollTaskUntilIdle,
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: { success: mocks.success, error: mocks.error } }
})

import { useFaceScanActions } from '../useFaceScanActions'

function makeActions(over: Partial<Record<string, unknown>> = {}) {
  const scanTask = ref(over.scanTask ?? null)
  const matchTask = ref(over.matchTask ?? null)
  const refreshTasks = vi.fn().mockResolvedValue(undefined)
  const refreshAll = vi.fn().mockResolvedValue(undefined)
  const detailChecked = ref(new Set<number>([1, 2]))
  const api = useFaceScanActions({
    scanTask: scanTask as never,
    matchTask: matchTask as never,
    scope: ref('incremental'),
    autoMatch: ref(true),
    starting: ref(false),
    cancelling: ref(false),
    matching: ref(false),
    detailChecked: detailChecked as never,
    refreshTasks,
    refreshAll,
  })
  return { api, scanTask, matchTask, refreshTasks, refreshAll, detailChecked }
}

describe('useFaceScanActions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.startFaceScan.mockResolvedValue({ task_id: 7, total: 120 })
    mocks.runFaceMatch.mockResolvedValue({ task_id: 8 })
    mocks.fetchFaceScanTask.mockResolvedValue({ scan_task: null, match_task: null })
    mocks.clientPost.mockResolvedValue({ data: { message: '任务已取消' } })
    mocks.pollTaskUntilIdle.mockResolvedValue(undefined)
  })

  it('开始扫描：按当前口径创建任务、清空勾选、刷新列表并开始轮询', async () => {
    const { api, refreshTasks, detailChecked } = makeActions()

    await api.startScan()

    expect(mocks.startFaceScan).toHaveBeenCalledWith('incremental', true)
    expect(mocks.success).toHaveBeenCalledWith(expect.stringContaining('120'))
    expect(detailChecked.value.size).toBe(0) // 上一轮勾选不能带到新任务
    expect(refreshTasks).toHaveBeenCalled()
    expect(mocks.pollTaskUntilIdle).toHaveBeenCalledWith(
      expect.objectContaining({ taskId: 7, intervalMs: 3000 }),
    )
  })

  it('开始扫描失败：提示错误且不轮询（不能装成排上队）', async () => {
    mocks.startFaceScan.mockRejectedValue(new Error('人脸服务未启动'))
    const { api, refreshTasks } = makeActions()

    await api.startScan()

    expect(mocks.error).toHaveBeenCalled()
    expect(refreshTasks).not.toHaveBeenCalled()
    expect(mocks.pollTaskUntilIdle).not.toHaveBeenCalled()
  })

  it('取消：优先取消正在跑的扫描任务', async () => {
    const { api, refreshTasks } = makeActions({
      scanTask: { id: 11, status: 'running' },
      matchTask: { id: 12, status: 'running' },
    })

    await api.cancelTask()

    expect(mocks.clientPost).toHaveBeenCalledWith('/tasks/11/cancel')
    expect(mocks.success).toHaveBeenCalled()
    expect(refreshTasks).toHaveBeenCalled()
  })

  it('没有扫描任务时取消匹配任务', async () => {
    const { api } = makeActions({
      scanTask: null,
      matchTask: { id: 12, status: 'running' },
    })

    await api.cancelTask()

    expect(mocks.clientPost).toHaveBeenCalledWith('/tasks/12/cancel')
  })

  it('没有任务时取消是空操作（不发请求、不报错）', async () => {
    const { api } = makeActions()

    await api.cancelTask()

    expect(mocks.clientPost).not.toHaveBeenCalled()
    expect(mocks.error).not.toHaveBeenCalled()
  })

  it('取消失败：提示错误，状态保持（不能假装取消成功）', async () => {
    mocks.clientPost.mockRejectedValue(new Error('boom'))
    const { api, refreshTasks } = makeActions({ scanTask: { id: 11, status: 'running' } })

    await api.cancelTask()

    expect(mocks.error).toHaveBeenCalled()
    expect(refreshTasks).not.toHaveBeenCalled()
  })

  it('全库重匹配：创建任务并轮询', async () => {
    const { api, refreshTasks } = makeActions()

    await api.startMatch()

    expect(mocks.runFaceMatch).toHaveBeenCalledWith({ scope: 'all' })
    expect(refreshTasks).toHaveBeenCalled()
    expect(mocks.pollTaskUntilIdle).toHaveBeenCalledWith(expect.objectContaining({ taskId: 8 }))
  })

  it('重匹配失败：提示错误且不轮询', async () => {
    mocks.runFaceMatch.mockRejectedValue(new Error('提交失败'))
    const { api, refreshTasks } = makeActions()

    await api.startMatch()

    expect(mocks.error).toHaveBeenCalled()
    expect(refreshTasks).not.toHaveBeenCalled()
  })

  it('轮询：把最新任务状态写回 refs，终态时刷新结果区', async () => {
    mocks.fetchFaceScanTask.mockResolvedValue({
      scan_task: { id: 7, status: 'running' },
      match_task: null,
    })
    // 手动触发 pollTaskUntilIdle 的 refresh / onIdle 回调
    mocks.pollTaskUntilIdle.mockImplementation(async ({ refresh, onIdle }) => {
      const task = await refresh()
      expect(task?.id).toBe(7)
      await onIdle()
    })
    const { api, scanTask, refreshAll } = makeActions()

    await api.pollUntilIdle(7)

    expect(scanTask.value).toEqual({ id: 7, status: 'running' })
    expect(refreshAll).toHaveBeenCalled()
  })
})
