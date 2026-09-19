/**
 * 抖音采集历史 composable（useF2TaskHistory）测试。
 *
 * 关注三件事：
 * ① 只按 type=f2_import 拉取（与下方 CDP 采集历史分开）并归一化成统一任务视图；
 * ② 四个操作走的是队列任务接口（/tasks/{id}/...）而不是采集任务接口；
 * ③ 静默刷新失败不弹错（轮询/WS 驱动），首次加载失败才提示。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Message } from '@arco-design/web-vue'
import { useF2TaskHistory } from '../useF2TaskHistory'
import { normalizeQueueTask, type QueueTask } from '@/utils/taskPresentation'
import type { UnifiedTask } from '@/types/task'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  delete: vi.fn(),
}))

vi.mock('@/api/client', () => ({ default: mocks }))
// WS 订阅在 composable 里注册：测试只关心它不抛错（事件驱动刷新不在本用例范围）
vi.mock('@/composables/useWebSocket', () => ({
  subscribeWs: vi.fn(),
  onWsReconnected: vi.fn(),
  isWsConnected: vi.fn(() => false),
}))

function makeRawTask(over: Partial<QueueTask> = {}): QueueTask {
  return {
    id: 338,
    type: 'f2_import',
    status: 'success',
    progress: 100,
    total: 38,
    done: 38,
    result: {
      stage: 'done',
      plan: { files: 38 },
      import: { imported: 38, batch_file: '/tmp/import_batches/f2-338.json' },
    },
    error: null,
    created_at: '2026-09-15T01:41:19Z',
    updated_at: '2026-09-15T01:47:33Z',
    ...over,
  }
}

function runningTask(): UnifiedTask {
  return normalizeQueueTask(makeRawTask({ id: 7, status: 'running', progress: 40 }))
}

describe('useF2TaskHistory', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('只按 f2_import 类型拉取并归一化历史', async () => {
    mocks.get.mockResolvedValue({ data: { items: [makeRawTask()], total: 4 } })
    const { tasks, total, resultTaskIds, loadTasks } = useF2TaskHistory()

    await loadTasks()

    expect(mocks.get).toHaveBeenCalledWith('/tasks', {
      params: { type: 'f2_import', page: 1, size: 10 },
    })
    expect(total.value).toBe(4)
    expect(tasks.value).toHaveLength(1)
    expect(tasks.value[0].source).toBe('queue')
    expect(tasks.value[0].type).toBe('f2_import')
    expect(tasks.value[0].status).toBe('success')
    // 完成态展示汇总文案（复用任务中心的 summarizeResult）
    expect(tasks.value[0].detail).toBe('入库 38')
    // 有批次清单的任务才给「查看结果」入口
    expect([...resultTaskIds.value]).toEqual([338])
  })

  it('没有批次结果的任务不进结果入口（未导入/未落清单）', async () => {
    mocks.get.mockResolvedValue({
      data: {
        items: [
          makeRawTask({ id: 1, result: { import: { imported: 0, batch_file: '/tmp/x.json' } } }),
          makeRawTask({ id: 2, result: { stage: 'download' } }),
          makeRawTask({ id: 3, result: null }),
        ],
        total: 3,
      },
    })
    const { resultTaskIds, loadTasks } = useF2TaskHistory()

    await loadTasks()

    expect(resultTaskIds.value.size).toBe(0)
  })

  it('首次加载失败：清空列表并提示', async () => {
    const error = vi.spyOn(Message, 'error').mockImplementation((() => {}) as never)
    mocks.get.mockRejectedValue(new Error('boom'))
    const { tasks, loadTasks } = useF2TaskHistory()

    await expect(loadTasks()).rejects.toThrow()

    expect(tasks.value).toEqual([])
    expect(error).toHaveBeenCalled()
    error.mockRestore()
  })

  it('静默刷新失败不提示（轮询 / WS 事件驱动）', async () => {
    const error = vi.spyOn(Message, 'error').mockImplementation((() => {}) as never)
    mocks.get.mockRejectedValue(new Error('boom'))
    const { loadTasks } = useF2TaskHistory()

    await expect(loadTasks({ silent: true })).rejects.toThrow()

    expect(error).not.toHaveBeenCalled()
    error.mockRestore()
  })

  it('取消 / 暂停 / 恢复 / 删除都走队列任务接口', async () => {
    const success = vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
    mocks.post.mockResolvedValue({ data: { message: '已取消' } })
    mocks.delete.mockResolvedValue({ data: {} })
    mocks.get.mockResolvedValue({ data: { items: [], total: 0 } })
    const { cancelTask, pauseTask, resumeTask, deleteTask } = useF2TaskHistory()
    const task = runningTask()

    await cancelTask(task)
    await pauseTask(task)
    await resumeTask(task)
    await deleteTask(task)

    expect(mocks.post).toHaveBeenNthCalledWith(1, '/tasks/7/cancel')
    expect(mocks.post).toHaveBeenNthCalledWith(2, '/tasks/7/pause')
    expect(mocks.post).toHaveBeenNthCalledWith(3, '/tasks/7/resume')
    expect(mocks.delete).toHaveBeenCalledWith('/tasks/7')
    success.mockRestore()
  })

  it('翻页时带上页码重新拉取', async () => {
    mocks.get.mockResolvedValue({ data: { items: [], total: 30 } })
    const { page, onPageChange, loadTasks } = useF2TaskHistory()
    await loadTasks()

    onPageChange(2)

    expect(page.value).toBe(2)
    await vi.waitFor(() =>
      expect(mocks.get).toHaveBeenLastCalledWith('/tasks', {
        params: { type: 'f2_import', page: 2, size: 10 },
      }),
    )
  })
})
