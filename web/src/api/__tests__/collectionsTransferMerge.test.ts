/** 合集批量移动/复制/合并 API 封装：端点与请求体字段（后端契约）。 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ post: vi.fn() }))

vi.mock('../client', () => ({ default: { post: mocks.post } }))

import { mergeCollections, transferCollectionItems } from '../collections'

beforeEach(() => {
  vi.clearAllMocks()
  mocks.post.mockResolvedValue({ data: {} })
})

describe('collections 批量整理 API', () => {
  it('移动/复制：POST /collections/{source}/inspirations/transfer', async () => {
    await transferCollectionItems(3, ['insp-1', 'insp-2'], [7, 9], 'move')
    expect(mocks.post).toHaveBeenCalledWith('/collections/3/inspirations/transfer', {
      ids: ['insp-1', 'insp-2'],
      target_ids: [7, 9],
      mode: 'move',
    })

    await transferCollectionItems(3, ['insp-1'], [7], 'copy')
    expect(mocks.post).toHaveBeenLastCalledWith('/collections/3/inspirations/transfer', {
      ids: ['insp-1'],
      target_ids: [7],
      mode: 'copy',
    })
  })

  it('合并：POST /collections/{target}/merge，source_ids 为被删源', async () => {
    await mergeCollections(9, [3, 4])
    expect(mocks.post).toHaveBeenCalledWith('/collections/9/merge', { source_ids: [3, 4] })
  })
})
