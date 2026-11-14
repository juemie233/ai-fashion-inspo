/** 随机评分 composable：未评分优先、回退全部图片、删除当前图。 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  fetchInspirations: vi.fn(),
  updateRating: vi.fn(),
  moveToTrash: vi.fn(),
  message: { success: vi.fn(), error: vi.fn() },
}))

vi.mock('@/api/inspirations', () => ({
  fetchInspirations: mocks.fetchInspirations,
  updateRating: mocks.updateRating,
  moveToTrash: mocks.moveToTrash,
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { useRandomRating } from '../useRandomRating'

const ITEM = {
  id: 'img-1',
  file_path: 'images/a.jpg',
  media_type: 'image',
  source_type: 'douyin',
  is_favorite: false,
  rating: 0,
  created_at: '2026-11-10T12:00:00Z',
  tags: [],
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.moveToTrash.mockResolvedValue({})
})

describe('useRandomRating', () => {
  it('优先请求 unrated=true 的图片，并标记当前来自未评分池', async () => {
    mocks.fetchInspirations.mockResolvedValue({ items: [ITEM], total: 3 })
    const api = useRandomRating()

    await api.loadRandom()

    expect(mocks.fetchInspirations).toHaveBeenCalledWith(
      expect.objectContaining({ sort: 'random', media_type: 'image', unrated: true }),
    )
    expect(api.item.value?.id).toBe('img-1')
    expect(api.total.value).toBe(3)
    expect(api.showingUnrated.value).toBe(true)
  })

  it('未评分池为空时回退到全部图片，并去掉 unrated 参数', async () => {
    mocks.fetchInspirations
      .mockResolvedValueOnce({ items: [], total: 0 })
      .mockResolvedValueOnce({ items: [{ ...ITEM, rating: 4 }], total: 7 })
    const api = useRandomRating()

    await api.loadRandom()

    expect(mocks.fetchInspirations).toHaveBeenCalledTimes(2)
    expect(mocks.fetchInspirations.mock.calls[0][0]).toMatchObject({ unrated: true })
    expect(mocks.fetchInspirations.mock.calls[1][0]).not.toHaveProperty('unrated')
    expect(api.item.value?.id).toBe('img-1')
    expect(api.total.value).toBe(7)
    expect(api.showingUnrated.value).toBe(false)
  })

  it('删除当前图片会移入垃圾桶，并允许随后重新抽题', async () => {
    mocks.fetchInspirations
      .mockResolvedValueOnce({ items: [ITEM], total: 3 })
      .mockResolvedValueOnce({ items: [{ ...ITEM, id: 'img-2' }], total: 2 })
    const api = useRandomRating()
    await api.loadRandom()

    const ok = await api.removeCurrent()

    expect(ok).toBe(true)
    expect(mocks.moveToTrash).toHaveBeenCalledWith('img-1')
    expect(mocks.message.success).toHaveBeenCalledWith('已移入垃圾桶')

    await api.loadRandom()
    expect(api.item.value?.id).toBe('img-2')
    expect(api.total.value).toBe(2)
  })

  it('删除失败时保留当前图片并提示错误', async () => {
    mocks.fetchInspirations.mockResolvedValue({ items: [ITEM], total: 3 })
    mocks.moveToTrash.mockRejectedValue(new Error('500'))
    const api = useRandomRating()
    await api.loadRandom()

    expect(await api.removeCurrent()).toBe(false)
    expect(api.item.value?.id).toBe('img-1')
    expect(mocks.message.error).toHaveBeenCalled()
  })
})
