/** inspirations store 单测：加载/防乱序/收藏/移垃圾桶/加载更多。 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api/inspirations', () => ({
  fetchInspirations: vi.fn(),
  fetchInspiration: vi.fn(),
  uploadInspiration: vi.fn(),
  toggleFavorite: vi.fn(),
  moveToTrash: vi.fn(),
}))

import {
  fetchInspirations,
  toggleFavorite,
  moveToTrash,
} from '@/api/inspirations'
import { useInspirationsStore } from '@/stores/inspirations'

const mockFetch = fetchInspirations as unknown as ReturnType<typeof vi.fn>
const mockToggle = toggleFavorite as unknown as ReturnType<typeof vi.fn>
const mockTrash = moveToTrash as unknown as ReturnType<typeof vi.fn>

function makeItem(id: string, isFavorite = false) {
  return { id, is_favorite: isFavorite }
}

function list(items: unknown[], total = items.length, page = 1, size = 50) {
  return { items, total, page, size }
}

describe('inspirations store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockFetch.mockReset()
    mockToggle.mockReset()
    mockTrash.mockReset()
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('load 填充列表与总数', async () => {
    mockFetch.mockResolvedValue(list([makeItem('a'), makeItem('b')]))
    const store = useInspirationsStore()
    await store.load()

    expect(store.items).toHaveLength(2)
    expect(store.total).toBe(2)
    expect(mockFetch).toHaveBeenCalledWith(expect.objectContaining({ page: 1, size: 50 }))
  })

  it('load 传递筛选与排序参数', async () => {
    mockFetch.mockResolvedValue(list([]))
    const store = useInspirationsStore()
    await store.load({ source_type: 'xiaohongshu', include_tags: '法式', sort: 'tag_count' })

    expect(mockFetch).toHaveBeenCalledWith(
      expect.objectContaining({ source_type: 'xiaohongshu', include_tags: '法式', sort: 'tag_count' }),
    )
  })

  it('load 防乱序：过期响应不覆盖新数据', async () => {
    const store = useInspirationsStore()
    let resolveOld!: (v: unknown) => void
    mockFetch
      .mockImplementationOnce(() => new Promise((r) => { resolveOld = r }))
      .mockResolvedValueOnce(list([makeItem('new')], 1))

    const p1 = store.load()
    await store.load()
    await new Promise((r) => setTimeout(r, 0))

    resolveOld(list([makeItem('old')], 1))
    await p1

    expect(store.items[0].id).toBe('new')
  })

  it('loadMore 追加下一页', async () => {
    mockFetch.mockResolvedValueOnce(list([makeItem('a')], 3))
    const store = useInspirationsStore()
    await store.load()

    mockFetch.mockResolvedValueOnce(list([makeItem('b')], 3, 2))
    await store.loadMore()

    expect(store.items.map((i) => i.id)).toEqual(['a', 'b'])
    expect(store.page).toBe(2)
  })

  it('loadMore 已满载时不请求', async () => {
    mockFetch.mockResolvedValueOnce(list([makeItem('a')], 1))
    const store = useInspirationsStore()
    await store.load()

    mockFetch.mockClear()
    await store.loadMore()
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('toggleFavorite 切换收藏并同步详情', async () => {
    mockFetch.mockResolvedValueOnce(list([makeItem('a')]))
    const store = useInspirationsStore()
    await store.load()
    mockToggle.mockResolvedValue({})

    await store.toggleFavorite('a')

    expect(mockToggle).toHaveBeenCalledWith('a', true)
    expect(store.items[0].is_favorite).toBe(true)
  })

  it('remove 移入垃圾桶后从列表剔除并减总数', async () => {
    mockFetch.mockResolvedValueOnce(list([makeItem('a'), makeItem('b')]))
    const store = useInspirationsStore()
    await store.load()
    mockTrash.mockResolvedValue({})

    await store.remove('a')

    expect(store.items.map((i) => i.id)).toEqual(['b'])
    expect(store.total).toBe(1)
  })
})
