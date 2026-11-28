/**
 * 合集 API（`collections.ts`）补测：半智能转换 / 扩充候选 / 按此扩充 / 撤销批次的封装。
 *
 * 既有 `collectionsP2` / `collectionsTransferMerge` 覆盖了主 CRUD 与移动/合并；
 * 本文件补 2026-11-22 新增的 4 个 wrapper（当前未覆盖）：
 * - 路径、方法、请求体必须与后端契约一致（尤其 batch_id 的 URL 编码）；
 * - 返回值原样透传、错误原样抛出（由弹窗/视图统一提示）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), delete: vi.fn(), patch: vi.fn() }))

vi.mock('@/api/client', () => ({
  default: { get: mocks.get, post: mocks.post, delete: mocks.delete, patch: mocks.patch },
}))

import {
  convertCollectionToSemi,
  expandCollection,
  suggestCollectionItems,
  undoCollectionExpansion,
  type CollectionOut,
} from '../collections'

function collection(over: Partial<CollectionOut> = {}): CollectionOut {
  return {
    id: 5,
    name: '通勤',
    description: null,
    kind: 'smart',
    position: 0,
    parent_id: null,
    auto_source: null,
    child_count: 0,
    cover_inspiration_id: null,
    cover_thumbnail_path: null,
    item_count: null,
    query_json: null,
    created_at: '',
    updated_at: '',
    ...over,
  }
}

describe('collections API 半智能/扩充补充', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.post.mockResolvedValue({ data: {} })
    mocks.delete.mockResolvedValue({ data: {} })
  })

  it('convertCollectionToSemi：POST /collections/{id}/to-semi 并返回更新后的合集', async () => {
    const updated = collection({ semi_manual: true })
    mocks.post.mockResolvedValueOnce({ data: updated })

    await expect(convertCollectionToSemi(5)).resolves.toEqual(updated)
    expect(mocks.post).toHaveBeenCalledWith('/collections/5/to-semi')
  })

  it('suggestCollectionItems：POST 候选参数原样透传（默认空对象）', async () => {
    const result = {
      items: [],
      total: 0,
      profile: { top_tags: [], outfit_tags: [], seeds_used: 0, vector_available: false },
    }
    mocks.post.mockResolvedValueOnce({ data: result })

    await expect(suggestCollectionItems(5)).resolves.toEqual(result)
    expect(mocks.post).toHaveBeenLastCalledWith('/collections/5/suggest-items', {})

    const params = { limit: 40, min_similarity: 0.6, outfit_only: true, exclude_other_auto: true }
    await suggestCollectionItems(5, params)
    expect(mocks.post).toHaveBeenLastCalledWith('/collections/5/suggest-items', params)
  })

  it('expandCollection：POST /collections/{id}/expansions，body 为 inspiration_ids', async () => {
    const result = {
      batch_id: 'batch-1',
      added: 2,
      skipped: 0,
      not_found: 0,
      added_ids: ['a', 'b'],
    }
    mocks.post.mockResolvedValueOnce({ data: result })

    await expect(expandCollection(5, ['a', 'b'])).resolves.toEqual(result)
    expect(mocks.post).toHaveBeenCalledWith('/collections/5/expansions', {
      inspiration_ids: ['a', 'b'],
    })
  })

  it('undoCollectionExpansion：DELETE 路径中的 batch_id 必须 URL 编码', async () => {
    mocks.delete.mockResolvedValueOnce({ data: { removed: 2, batch_id: 'batch/1 号' } })

    await expect(undoCollectionExpansion(5, 'batch/1 号')).resolves.toEqual({
      removed: 2,
      batch_id: 'batch/1 号',
    })
    expect(mocks.delete).toHaveBeenCalledWith('/collections/5/expansions/batch%2F1%20%E5%8F%B7')
  })

  it('半智能转换失败原样抛出（视图据此提示「转为半智能失败」）', async () => {
    const err = { response: { data: { detail: '合集类型不允许' } } }
    mocks.post.mockRejectedValueOnce(err)

    await expect(convertCollectionToSemi(5)).rejects.toBe(err)
  })

  it('撤销批次失败原样抛出（弹窗提示「撤销失败」）', async () => {
    const err = new Error('404')
    mocks.delete.mockRejectedValueOnce(err)

    await expect(undoCollectionExpansion(5, 'b1')).rejects.toBe(err)
  })
})
