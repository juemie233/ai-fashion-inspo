/** M3 新增 API 的契约测试：列表筛选、批量、向量检索、合集。 */

import { apiClient } from '../services/api'
import {
  addToCollection,
  batchFavorite,
  batchTrash,
  createCollection,
  fetchCollectionInspirations,
  fetchCollections,
  fetchInspirationPage,
  removeFromCollection,
  vectorSearchByImage,
  vectorSearchByText,
} from '../services/api'

describe('列表筛选/排序', () => {
  it('把 page/size/sort 与筛选条件一起拼进查询参数', async () => {
    const spy = jest
      .spyOn(apiClient, 'get')
      .mockResolvedValue({ data: { items: [], total: 0, page: 1, size: 30 } })

    await fetchInspirationPage(2, 30, { is_favorite: true, tag_status: 'tagged' }, 'rating')

    expect(spy).toHaveBeenCalledWith('/inspirations', {
      params: { page: 2, size: 30, sort: 'rating', is_favorite: true, tag_status: 'tagged' },
    })
    spy.mockRestore()
  })

  it('无筛选时只带分页与排序，不发送空字段', async () => {
    const spy = jest
      .spyOn(apiClient, 'get')
      .mockResolvedValue({ data: { items: [], total: 0, page: 1, size: 30 } })

    await fetchInspirationPage(1, 30)

    expect(spy).toHaveBeenCalledWith('/inspirations', {
      params: { page: 1, size: 30, sort: 'newest' },
    })
    spy.mockRestore()
  })
})

describe('批量操作', () => {
  it('batchFavorite 打 batch-favorite 并带目标状态', async () => {
    const spy = jest.spyOn(apiClient, 'post').mockResolvedValue({ data: { updated: 2 } })
    const result = await batchFavorite(['a', 'b'], true)

    expect(spy).toHaveBeenCalledWith('/inspirations/batch-favorite', {
      ids: ['a', 'b'],
      is_favorite: true,
    })
    expect(result).toEqual({ updated: 2 })
    spy.mockRestore()
  })

  it('batchTrash 不传原因时只发 ids', async () => {
    const spy = jest.spyOn(apiClient, 'post').mockResolvedValue({ data: { trashed: 2 } })
    await batchTrash(['a', 'b'])

    expect(spy).toHaveBeenCalledWith('/inspirations/batch-trash', { ids: ['a', 'b'] })
    spy.mockRestore()
  })

  it('batchTrash 传原因时带上 reason 与 source', async () => {
    const spy = jest.spyOn(apiClient, 'post').mockResolvedValue({ data: { trashed: 1 } })
    await batchTrash(['a'], '重复')

    expect(spy).toHaveBeenCalledWith('/inspirations/batch-trash', {
      ids: ['a'],
      reason: '重复',
      source: 'manual',
    })
    spy.mockRestore()
  })
})

describe('向量检索', () => {
  it('以图搜图提交 file，multipart + 更长超时（要跑图像向量推理）', async () => {
    const spy = jest.spyOn(apiClient, 'post').mockResolvedValue({ data: { items: [] } })

    await vectorSearchByImage('file:///tmp/query.png')

    const [url, form, config] = spy.mock.calls[0]
    expect(url).toBe('/search/vector')
    expect(form).toBeInstanceOf(FormData)
    expect((config as { headers: Record<string, string> }).headers['Content-Type']).toBe(
      'multipart/form-data',
    )
    expect((config as { timeout: number }).timeout).toBeGreaterThanOrEqual(30000)
    spy.mockRestore()
  })

  it('后端没有 items 字段时返回空数组，不抛错', async () => {
    const spy = jest.spyOn(apiClient, 'post').mockResolvedValue({ data: {} })
    expect(await vectorSearchByImage('file:///tmp/query.png')).toEqual([])
    spy.mockRestore()
  })
})

describe('合集', () => {
  it('fetchCollections 返回数组；异常响应退化为空数组', async () => {
    const spy = jest.spyOn(apiClient, 'get').mockResolvedValue({ data: [{ id: 1, name: '春日' }] })
    expect(await fetchCollections()).toHaveLength(1)

    spy.mockResolvedValue({ data: null })
    expect(await fetchCollections()).toEqual([])
    spy.mockRestore()
  })

  it('createCollection 打 POST /collections', async () => {
    const spy = jest.spyOn(apiClient, 'post').mockResolvedValue({ data: { id: 7, name: '新合集' } })
    const created = await createCollection('新合集')

    expect(spy).toHaveBeenCalledWith('/collections', { name: '新合集' })
    expect(created.id).toBe(7)
    spy.mockRestore()
  })

  it('fetchCollectionInspirations 用 page_size（后端参数名与列表页不同）', async () => {
    const spy = jest
      .spyOn(apiClient, 'get')
      .mockResolvedValue({ data: { items: [{ id: 'a' }], total: 1 } })

    const page = await fetchCollectionInspirations(3, 2, 30)

    expect(spy).toHaveBeenCalledWith('/collections/3/inspirations', {
      params: { page: 2, page_size: 30 },
    })
    expect(page.total).toBe(1)
    expect(page.items).toHaveLength(1)
    spy.mockRestore()
  })

  it('加入/移出合集分别用 POST 与 DELETE（DELETE 带 body）', async () => {
    const post = jest.spyOn(apiClient, 'post').mockResolvedValue({ data: {} })
    const del = jest.spyOn(apiClient, 'delete').mockResolvedValue({ data: {} })

    await addToCollection(3, ['a', 'b'])
    expect(post).toHaveBeenCalledWith('/collections/3/inspirations', {
      inspiration_ids: ['a', 'b'],
    })

    await removeFromCollection(3, ['a'])
    expect(del).toHaveBeenCalledWith('/collections/3/inspirations', {
      data: { inspiration_ids: ['a'] },
    })

    post.mockRestore()
    del.mockRestore()
  })
})
