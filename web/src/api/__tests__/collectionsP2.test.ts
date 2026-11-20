/** 合集 P2 API：四格式导出与 AI 整理预览的端点/请求体。 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ post: vi.fn() }))

vi.mock('../client', () => ({ default: { post: mocks.post } }))

import { aiSummarizeCollection, exportCollection } from '../collections'

beforeEach(() => {
  vi.clearAllMocks()
  mocks.post.mockResolvedValue({ data: {} })
})

describe('collections P2 API', () => {
  it('导出：POST /collections/{id}/exports，body 带四种格式之一', async () => {
    await exportCollection(5, 'pdf')
    expect(mocks.post).toHaveBeenCalledWith(
      '/collections/5/exports',
      { format: 'pdf' },
      { timeout: 600_000 },
    )

    await exportCollection(5, 'zip')
    expect(mocks.post).toHaveBeenLastCalledWith(
      '/collections/5/exports',
      { format: 'zip' },
      { timeout: 600_000 },
    )

    await exportCollection(5, 'csv')
    expect(mocks.post).toHaveBeenLastCalledWith(
      '/collections/5/exports',
      { format: 'csv' },
      { timeout: 600_000 },
    )

    await exportCollection(5, 'markdown')
    expect(mocks.post).toHaveBeenLastCalledWith(
      '/collections/5/exports',
      { format: 'markdown' },
      { timeout: 600_000 },
    )
  })

  it('AI 整理：POST /collections/{id}/ai-summary（无 body）', async () => {
    await aiSummarizeCollection(5)
    expect(mocks.post).toHaveBeenCalledWith('/collections/5/ai-summary', undefined, {
      timeout: 180_000,
    })
  })
})
