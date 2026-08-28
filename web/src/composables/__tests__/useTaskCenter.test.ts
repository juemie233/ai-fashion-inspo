/**
 * 任务中心 composable（useTaskCenter）回归测试。
 *
 * 覆盖「取消任务」新语义：
 * - 队列任务 pending 取消 = 后端物理删除（deleted: true），前端本地移除该行
 *   并再次拉取列表校正页码；
 * - 采集任务取消仍走原逻辑（记录保留，仅提示「已取消」）；
 * - 后端以 400 拒绝（如任务已开始执行）时展示后端错误详情，列表不变。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Message } from '@arco-design/web-vue'
import { useTaskCenter } from '../useTaskCenter'
import type { UnifiedTask } from '@/types/task'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  delete: vi.fn(),
}))

vi.mock('@/api/client', () => ({ default: mocks }))

const msgMock = () => ({ close: () => {} })

function makeQueueTask(over: Partial<UnifiedTask> = {}): UnifiedTask {
  return {
    id: 5,
    source: 'queue',
    type: 'batch_analyze',
    platform: '',
    status: 'pending',
    progress: 0,
    total: 10,
    done: 0,
    target: 10,
    started_at: null,
    title: '批量 AI 分析',
    detail: '',
    error: null,
    created_at: '2026-01-01T00:00:00Z',
    finished_at: null,
    ...over,
  }
}

function makeScraperTask(over: Partial<UnifiedTask> = {}): UnifiedTask {
  return {
    id: 3,
    source: 'scraper',
    type: 'scraper',
    platform: 'xiaohongshu',
    status: 'pending',
    progress: -1,
    total: 0,
    done: 0,
    target: 10,
    started_at: null,
    title: '小红书采集',
    detail: '',
    error: null,
    created_at: '2026-01-01T00:00:00Z',
    finished_at: null,
    ...over,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.get.mockImplementation((url: string) => {
    if (url === '/tasks') return Promise.resolve({ data: { items: [] } })
    return Promise.resolve({ data: { items: [] } })
  })
})

describe('useTaskCenter.cancelTask 取消任务', () => {
  it('队列 pending 任务取消 = 物理删除：调用 /tasks/{id}/cancel 并本地移除该行', async () => {
    const successSpy = vi.spyOn(Message, 'success').mockImplementation(msgMock)
    const queueRow = {
      id: 5,
      type: 'batch_analyze',
      status: 'pending',
      progress: 0,
      total: 10,
      done: 0,
      result: null,
      error: null,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    }
    // 可变服务端列表：删除前含待删任务，删除后（再拉取）为空
    let queueItems: unknown[] = [queueRow]
    mocks.get.mockImplementation((url: string) => {
      if (url === '/tasks') return Promise.resolve({ data: { items: queueItems } })
      return Promise.resolve({ data: { items: [] } })
    })
    mocks.post.mockResolvedValue({ data: { message: '任务已删除', task_id: 5, deleted: true } })

    const tc = useTaskCenter()
    await tc.loadTasks()
    expect(tc.tasks.value).toHaveLength(1)

    const target = makeQueueTask()
    queueItems = [] // 服务端已物理删除
    await tc.cancelTask(target)

    expect(mocks.post).toHaveBeenCalledWith('/tasks/5/cancel')
    expect(successSpy).toHaveBeenCalledWith('任务已删除')
    expect(tc.tasks.value.find((x) => x.id === 5)).toBeUndefined()
  })

  it('采集任务取消：走 /scraper/tasks/{id}/cancel，记录保留、仅提示已取消', async () => {
    const successSpy = vi.spyOn(Message, 'success').mockImplementation(msgMock)
    const scraperRow = {
      id: 3,
      platform: 'xiaohongshu',
      status: 'cancelled',
      config: null,
      items_found: 0,
      items_added: 0,
      error: '用户手动取消',
      started_at: null,
      finished_at: null,
      created_at: '2026-01-01T00:00:00Z',
    }
    mocks.get.mockImplementation((url: string) => {
      if (url === '/scraper/tasks') return Promise.resolve({ data: { items: [scraperRow] } })
      return Promise.resolve({ data: { items: [] } })
    })
    // 采集取消响应不含 deleted 字段
    mocks.post.mockResolvedValue({ data: { message: '任务已取消' } })

    const tc = useTaskCenter()
    await tc.loadTasks()
    expect(tc.tasks.value).toHaveLength(1)

    await tc.cancelTask(makeScraperTask())

    expect(mocks.post).toHaveBeenCalledWith('/scraper/tasks/3/cancel')
    expect(successSpy).toHaveBeenCalledWith('任务已取消')
    // 采集任务记录保留在列表中（不应用删除语义）
    expect(tc.tasks.value.some((x) => x.source === 'scraper' && x.id === 3)).toBe(true)
  })

  it('后端 400 拒绝时展示错误详情，不改动列表', async () => {
    const errorSpy = vi.spyOn(Message, 'error').mockImplementation(msgMock)
    const queueTask = makeQueueTask({ status: 'running' })
    mocks.post.mockRejectedValue({
      response: { data: { detail: '仅等待中的任务可以取消并删除（当前状态 running）' } },
    })

    const tc = useTaskCenter()
    await tc.cancelTask(queueTask)

    expect(mocks.post).toHaveBeenCalledWith('/tasks/5/cancel')
    expect(errorSpy).toHaveBeenCalledWith('仅等待中的任务可以取消并删除（当前状态 running）')
    // 未触发删除语义（无成功提示）
    expect(Message.success).not.toHaveBeenCalled()
  })
})

describe('useTaskCenter.deleteTask 删除任务', () => {
  it('队列任务走通用删除接口 /tasks/{id}', async () => {
    const successSpy = vi.spyOn(Message, 'success').mockImplementation(msgMock)
    mocks.delete.mockResolvedValue({ data: { message: '任务已删除', task_id: 5, deleted: true } })

    const tc = useTaskCenter()
    await tc.deleteTask(makeQueueTask({ status: 'cancelled' }))

    expect(mocks.delete).toHaveBeenCalledWith('/tasks/5')
    expect(successSpy).toHaveBeenCalledWith('已删除')
  })

  it('采集任务走采集专用删除接口 /scraper/tasks/{id}，不误走通用接口', async () => {
    const successSpy = vi.spyOn(Message, 'success').mockImplementation(msgMock)
    mocks.delete.mockResolvedValue({ data: { message: '已删除' } })

    const tc = useTaskCenter()
    await tc.deleteTask(makeScraperTask({ status: 'cancelled' }))

    expect(mocks.delete).toHaveBeenCalledWith('/scraper/tasks/3')
    expect(successSpy).toHaveBeenCalledWith('已删除')
  })

  it('删除失败时提示错误并刷新列表', async () => {
    const errorSpy = vi.spyOn(Message, 'error').mockImplementation(msgMock)
    const queueTask = makeQueueTask({ status: 'running' })
    mocks.delete.mockRejectedValue({
      response: { data: { detail: '任务状态为 running，不能删除' } },
    })

    const tc = useTaskCenter()
    await tc.deleteTask(queueTask)

    expect(mocks.delete).toHaveBeenCalledWith('/tasks/5')
    expect(errorSpy).toHaveBeenCalledWith('任务状态为 running，不能删除')
  })
})

describe('useTaskCenter.pauseTask / resumeTask 标签网络分析任务暂停恢复', () => {
  it('暂停：调用 /tasks/{id}/pause 并刷新列表', async () => {
    const successSpy = vi.spyOn(Message, 'success').mockImplementation(msgMock)
    mocks.post.mockResolvedValue({ data: { message: '任务已暂停', task_id: 7 } })

    const tc = useTaskCenter()
    await tc.pauseTask(makeQueueTask({ id: 7, type: 'tag_network_analyze', status: 'running' }))

    expect(mocks.post).toHaveBeenCalledWith('/tasks/7/pause')
    expect(successSpy).toHaveBeenCalledWith('任务已暂停')
  })

  it('恢复：调用 /tasks/{id}/resume 并刷新列表', async () => {
    const successSpy = vi.spyOn(Message, 'success').mockImplementation(msgMock)
    mocks.post.mockResolvedValue({ data: { message: '任务已恢复', task_id: 7 } })

    const tc = useTaskCenter()
    await tc.resumeTask(makeQueueTask({ id: 7, type: 'tag_network_analyze', status: 'paused' }))

    expect(mocks.post).toHaveBeenCalledWith('/tasks/7/resume')
    expect(successSpy).toHaveBeenCalledWith('任务已恢复')
  })

  it('后端 400 拒绝（类型/状态不符）时展示错误详情', async () => {
    const errorSpy = vi.spyOn(Message, 'error').mockImplementation(msgMock)
    mocks.post.mockRejectedValue({
      response: {
        data: { detail: '仅运行中的 tag_network_analyze 任务可暂停（当前状态 running）' },
      },
    })

    const tc = useTaskCenter()
    await tc.pauseTask(makeQueueTask({ id: 7, type: 'batch_analyze', status: 'running' }))

    expect(mocks.post).toHaveBeenCalledWith('/tasks/7/pause')
    expect(errorSpy).toHaveBeenCalledWith(
      '仅运行中的 tag_network_analyze 任务可暂停（当前状态 running）',
    )
  })
})
