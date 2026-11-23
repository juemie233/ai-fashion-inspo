/**
 * 近似重复面板 API 层（useNearDuplicatesApi）：请求转发与错误透传。
 *
 * 组件只保留对比弹窗与决策状态，所有请求收敛在这个 composable：
 * 扫描/补齐任务走 @/api/admin 的既有封装（参数顺序不能错），任务进度与批量删除走
 * apiClient（URL `/tasks/{id}`、body `{ ids }`）。这里逐条锁定转发契约，
 * 避免以后再改参数名/路径时组件测试发现不了。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  scan: vi.fn(),
  backfill: vi.fn(),
}))

vi.mock('@/api/client', () => ({ default: { get: mocks.get, post: mocks.post } }))
vi.mock('@/api/admin', () => ({
  fetchNearDuplicates: mocks.scan,
  startPhashBackfill: mocks.backfill,
}))

import { useNearDuplicatesApi } from '../useNearDuplicatesApi'

beforeEach(() => {
  vi.clearAllMocks()
})

describe('useNearDuplicatesApi', () => {
  it('scan 原样转发 limit/threshold 并返回扫描结果', async () => {
    const result = { groups: [], total_groups: 0 }
    mocks.scan.mockResolvedValueOnce(result)
    const api = useNearDuplicatesApi()

    await expect(api.scan(50, 12)).resolves.toBe(result)
    expect(mocks.scan).toHaveBeenCalledWith(50, 12)
  })

  it('startBackfill 把 forceRehash 原样转发给 startPhashBackfill', async () => {
    mocks.backfill.mockResolvedValue({ data: { task_id: 9 } })
    const api = useNearDuplicatesApi()

    await api.startBackfill(true)
    expect(mocks.backfill).toHaveBeenLastCalledWith(true)

    await api.startBackfill(false)
    expect(mocks.backfill).toHaveBeenLastCalledWith(false)
  })

  it('fetchBackfillTask：GET /tasks/{id} 并解包 data', async () => {
    const task = { status: 'running', progress: 30, done: 3, total: 10, result: null }
    mocks.get.mockResolvedValueOnce({ data: task })
    const api = useNearDuplicatesApi()

    await expect(api.fetchBackfillTask(42)).resolves.toBe(task)
    expect(mocks.get).toHaveBeenCalledWith('/tasks/42')
  })

  it('submitBatchDelete：POST /admin/batch-delete，body 是 { ids }', async () => {
    const result = { message: '任务已创建', task_id: 7 }
    mocks.post.mockResolvedValueOnce({ data: result })
    const api = useNearDuplicatesApi()

    await expect(api.submitBatchDelete(['a', 'b'])).resolves.toBe(result)
    expect(mocks.post).toHaveBeenCalledWith('/admin/batch-delete', { ids: ['a', 'b'] })
  })

  it('请求失败原样 reject（由组件提示，composable 不吞错）', async () => {
    mocks.get.mockRejectedValueOnce(new Error('task 500'))
    mocks.post.mockRejectedValueOnce(new Error('delete 403'))
    const api = useNearDuplicatesApi()

    await expect(api.fetchBackfillTask(1)).rejects.toThrow('task 500')
    await expect(api.submitBatchDelete(['a'])).rejects.toThrow('delete 403')
  })
})
