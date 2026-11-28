/**
 * 任务 API（`tasks.ts`）补测：`fetchTaskTypes` 的契约拉取与错误透传。
 *
 * 既有 `apiWrappers*.test.ts` 已覆盖 `fetchTaskRuns`（含非数组兜底），本文件补：
 * - 启动时同步任务类型/动作契约的 GET 路径；
 * - 返回值原样透传、错误原样抛出（调用方回退前端内置默认清单）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ get: vi.fn() }))

vi.mock('@/api/client', () => ({ default: { get: mocks.get } }))

import { fetchTaskRuns, fetchTaskTypes } from '../tasks'

describe('tasks API 补充', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('fetchTaskTypes：GET /tasks/types 并原样返回契约', async () => {
    const contract = {
      types: [{ type: 'f2_import', label: '抖音增量' }],
      actions: { cancel: true },
    }
    mocks.get.mockResolvedValueOnce({ data: contract })

    await expect(fetchTaskTypes()).resolves.toEqual(contract)
    expect(mocks.get).toHaveBeenCalledWith('/tasks/types')
  })

  it('fetchTaskTypes 失败原样抛出（调用方回退内置清单）', async () => {
    const err = new Error('503')
    mocks.get.mockRejectedValueOnce(err)

    await expect(fetchTaskTypes()).rejects.toBe(err)
  })

  it('fetchTaskRuns：非数组响应回退空数组，避免调用方对 null 做 map', async () => {
    mocks.get.mockResolvedValueOnce({ data: null })
    await expect(fetchTaskRuns(7)).resolves.toEqual([])

    mocks.get.mockResolvedValueOnce({ data: { items: [] } })
    await expect(fetchTaskRuns(7)).resolves.toEqual([])
  })
})
