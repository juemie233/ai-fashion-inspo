/**
 * 任务操作 seam（`useTaskActions`）的测试。
 *
 * 任务中心与采集管理页共用这一套「取消 / 删除 / 暂停 / 恢复」，最容易出问题的不是接口
 * 调用，而是**分支判据**：
 *
 * 1. **接口按来源分流**：队列任务走 `/tasks/…`，采集任务走 `/scraper/tasks/…`；
 *    走错的后果是 404 或「取消了一个不存在的任务」，而界面只显示「取消失败」。
 * 2. **pending 取消 = 后端物理删除**（返回 `deleted: true`）：这类任务要从列表里
 *    就地移除并纠正页码，否则页码错位、行残留。这是「取消」唯一需要回调的分支。
 * 3. **失败只提示、不刷新**：刷新会把行状态覆盖回未操作，用户以为没生效。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  del: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}))

vi.mock('@/api/client', () => ({
  default: { post: mocks.post, delete: mocks.del },
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { useTaskActions } from '../useTaskActions'

type Task = Parameters<ReturnType<typeof useTaskActions>['cancelTask']>[0]

function task(overrides: Partial<Record<string, unknown>> = {}): Task {
  return {
    id: 7,
    type: 'batch_analyze',
    status: 'running',
    source: 'queue',
    ...overrides,
  } as unknown as Task
}

describe('useTaskActions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.post.mockResolvedValue({ data: {} })
    mocks.del.mockResolvedValue({ data: {} })
  })

  it('队列任务取消走 /tasks，采集任务取消走 /scraper/tasks', async () => {
    const { cancelTask } = useTaskActions()

    await cancelTask(task({ id: 1, source: 'queue' }))
    await cancelTask(task({ id: 2, source: 'scraper' }))

    expect(mocks.post).toHaveBeenNthCalledWith(1, '/tasks/1/cancel')
    expect(mocks.post).toHaveBeenNthCalledWith(2, '/scraper/tasks/2/cancel')
  })

  it('pending 任务被物理删除时触发 onQueueTaskDeleted（列表要就地移除并纠正页码）', async () => {
    mocks.post.mockResolvedValue({ data: { deleted: true, message: '任务已删除' } })
    const onQueueTaskDeleted = vi.fn()
    const reload = vi.fn()
    const { cancelTask } = useTaskActions({ onQueueTaskDeleted, reload })
    const t = task({ status: 'pending' })

    await cancelTask(t)

    expect(onQueueTaskDeleted).toHaveBeenCalledWith(t)
    expect(mocks.message.success).toHaveBeenCalledWith('任务已删除')
    expect(reload).toHaveBeenCalled()
  })

  it('运行中任务取消只标记 cancelled：不触发删除回调', async () => {
    mocks.post.mockResolvedValue({ data: { deleted: false } })
    const onQueueTaskDeleted = vi.fn()
    const { cancelTask } = useTaskActions({ onQueueTaskDeleted })

    await cancelTask(task({ status: 'running' }))

    expect(onQueueTaskDeleted).not.toHaveBeenCalled()
    expect(mocks.message.success).toHaveBeenCalledWith('已取消')
  })

  it('取消失败只提示，不刷新（刷新会让用户以为操作生效了）', async () => {
    mocks.post.mockRejectedValue(new Error('网络错误'))
    const reload = vi.fn()
    const { cancelTask } = useTaskActions({ reload })

    await cancelTask(task())

    expect(mocks.message.error).toHaveBeenCalled()
    expect(reload).not.toHaveBeenCalled()
  })

  it('删除任务同样按来源分流，成功后刷新', async () => {
    const reload = vi.fn()
    const { deleteTask } = useTaskActions({ reload })

    await deleteTask(task({ id: 3, source: 'scraper' }))
    await deleteTask(task({ id: 4, source: 'queue' }))

    expect(mocks.del).toHaveBeenNthCalledWith(1, '/scraper/tasks/3')
    expect(mocks.del).toHaveBeenNthCalledWith(2, '/tasks/4')
    expect(reload).toHaveBeenCalledTimes(2)
    expect(mocks.message.success).toHaveBeenCalledWith('已删除')
  })

  it('暂停/恢复固定走通用任务接口（即使来源是采集任务）', async () => {
    mocks.post.mockResolvedValue({ data: { message: '' } })
    const { pauseTask, resumeTask } = useTaskActions()

    await pauseTask(task({ id: 9, source: 'scraper' }))
    await resumeTask(task({ id: 9, source: 'scraper' }))

    expect(mocks.post).toHaveBeenNthCalledWith(1, '/tasks/9/pause')
    expect(mocks.post).toHaveBeenNthCalledWith(2, '/tasks/9/resume')
    // 后端没给 message 时用默认文案
    expect(mocks.message.success).toHaveBeenCalledWith('任务已暂停')
    expect(mocks.message.success).toHaveBeenCalledWith('任务已恢复')
  })

  it('暂停/恢复失败各自给出可读提示', async () => {
    mocks.post.mockRejectedValue(new Error('409 冲突'))
    const { pauseTask, resumeTask } = useTaskActions()

    await pauseTask(task())
    await resumeTask(task())

    expect(mocks.message.error).toHaveBeenCalledTimes(2)
  })
})
