/** tags store 单测：加载、选中/排除切换、清除筛选、类别映射。 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api/tags', () => ({
  fetchTagsGrouped: vi.fn(),
}))

import { fetchTagsGrouped } from '@/api/tags'
import { useTagsStore } from '@/stores/tags'

const mockFetch = fetchTagsGrouped as unknown as ReturnType<typeof vi.fn>

describe('tags store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockFetch.mockReset()
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('load 加载分组，已加载则跳过，force 强制刷新', async () => {
    mockFetch.mockResolvedValue([
      { category: 'style', tags: [{ id: 1, name: '法式', category: 'style' }] },
    ])
    const store = useTagsStore()
    await store.load()
    expect(store.groups).toHaveLength(1)

    mockFetch.mockClear()
    await store.load() // 已加载，跳过
    expect(mockFetch).not.toHaveBeenCalled()

    await store.load(true) // 强制刷新
    expect(mockFetch).toHaveBeenCalledTimes(1)
  })

  it('toggleTag 切换选中', () => {
    const store = useTagsStore()
    store.toggleTag('法式')
    expect(store.selectedTags.has('法式')).toBe(true)
    store.toggleTag('法式')
    expect(store.selectedTags.has('法式')).toBe(false)
  })

  it('toggleExcludeTag 切换排除', () => {
    const store = useTagsStore()
    store.toggleExcludeTag('白色')
    expect(store.excludedTags.has('白色')).toBe(true)
    store.toggleExcludeTag('白色')
    expect(store.excludedTags.has('白色')).toBe(false)
  })

  it('clearFilters 清空并重置为 AND', () => {
    const store = useTagsStore()
    store.toggleTag('法式')
    store.toggleExcludeTag('白色')
    store.combineMode = 'OR'

    store.clearFilters()

    expect(store.selectedTags.size).toBe(0)
    expect(store.excludedTags.size).toBe(0)
    expect(store.combineMode).toBe('AND')
  })

  it('getCategoryLabel 映射与未知回退', () => {
    const store = useTagsStore()
    expect(store.getCategoryLabel('style')).toBe('风格')
    expect(store.getCategoryLabel('unknown')).toBe('unknown')
  })
})
