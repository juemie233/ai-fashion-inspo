/** 出图 API：导出参数（C2）与统计/标注的类型契约。 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  default: { get: mocks.get, post: mocks.post, patch: vi.fn(), delete: vi.fn() },
}))

import { autoTitleRun, exportRuns } from '../generate'

describe('exportRuns', () => {
  beforeEach(() => {
    mocks.get.mockReset()
    mocks.get.mockResolvedValue({
      data: new Blob(['id,created_at']),
      headers: { 'content-disposition': 'attachment; filename="generate-history-20261028.csv"' },
    })
    // jsdom 没有 createObjectURL：给个桩，避免下载逻辑把测试打挂
    globalThis.URL.createObjectURL = vi.fn(() => 'blob:mock')
    globalThis.URL.revokeObjectURL = vi.fn()
  })

  it('剔除分页参数，只带筛选条件与 fmt', async () => {
    await exportRuns('csv', {
      page: 3,
      size: 20,
      limit: 20,
      q: '领口',
      favorite: true,
      reference_id: undefined,
    })

    const [url, config] = mocks.get.mock.calls[0]
    expect(url).toBe('/generate/runs/export')
    expect(config.responseType).toBe('blob')
    expect(config.params).toEqual({ fmt: 'csv', q: '领口', favorite: true })
  })

  it('json 也走同一个端点', async () => {
    await exportRuns('json', { q: '通勤' })
    expect(mocks.get.mock.calls[0][1].params).toEqual({ fmt: 'json', q: '通勤' })
  })
})

describe('autoTitleRun', () => {
  it('POST 到 auto-title，原样返回建议标题与来源', async () => {
    mocks.post.mockReset()
    mocks.post.mockResolvedValue({ data: { title: '通勤白衬衫', source: 'ai' } })

    const result = await autoTitleRun(12)

    expect(mocks.post).toHaveBeenCalledWith('/generate/runs/12/auto-title')
    expect(result).toEqual({ title: '通勤白衬衫', source: 'ai' })
  })
})
