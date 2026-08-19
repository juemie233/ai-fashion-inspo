/** useTagManage 标签管理 composable 单测：聚焦 URL 浏览状态持久化（刷新 / 详情页返回后恢复）。 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'

// 共享路由 mock（hoisted 避免 vi.mock 提升导致的 TDZ 问题）
const routeState = vi.hoisted(() => ({
  query: {} as Record<string, string>,
  replace: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: routeState.query }),
  useRouter: () => ({ replace: routeState.replace }),
}))

vi.mock('@arco-design/web-vue', () => ({
  Message: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}))

vi.mock('@/api/tags', () => ({
  fetchTagsGrouped: vi.fn(),
  fetchTagStats: vi.fn(),
  updateTag: vi.fn(),
  mergeTags: vi.fn(),
  batchDeleteTags: vi.fn(),
  deleteUnusedTags: vi.fn(),
  findDuplicates: vi.fn(),
  reorderTags: vi.fn(),
  createAlias: vi.fn(),
  CATEGORY_LABELS: {},
  SOURCE_LABELS: {},
}))

import { fetchTagsGrouped, fetchTagStats } from '@/api/tags'
import { useTagManage } from '../useTagManage'

const mockFetchGroups = fetchTagsGrouped as unknown as ReturnType<typeof vi.fn>
const mockFetchStats = fetchTagStats as unknown as ReturnType<typeof vi.fn>

/** 构造标签分组数据（两个风格标签：id=1 法式 / id=2 韩系） */
function makeGroups() {
  return [
    {
      category: 'style',
      tags: [
        { id: 1, name: '法式', category: 'style', source: 'manual', pinned: false, sort_order: 0, description: null, usage_count: 5 },
        { id: 2, name: '韩系', category: 'style', source: 'ai_generated', pinned: false, sort_order: 1, description: null, usage_count: 3 },
      ],
    },
  ]
}

describe('useTagManage URL 浏览状态持久化', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    routeState.query = {}
    mockFetchGroups.mockResolvedValue(makeGroups())
    mockFetchStats.mockResolvedValue({ total: 2, unused: 0, total_links: 8, by_source: {}, by_category: {} })
  })

  it('初始筛选状态从 URL query 恢复（搜索/类别/来源/排序）', () => {
    routeState.query = { q: '法式', category: 'style', source: 'manual', sort: 'name' }
    const t = useTagManage()
    expect(t.searchQuery.value).toBe('法式')
    expect(t.filterCategory.value).toBe('style')
    expect(t.filterSource.value).toBe('manual')
    expect(t.sortMode.value).toBe('name')
  })

  it('URL 无状态时回退默认值（排序 usage）', () => {
    const t = useTagManage()
    expect(t.searchQuery.value).toBe('')
    expect(t.filterCategory.value).toBeNull()
    expect(t.filterSource.value).toBeNull()
    expect(t.sortMode.value).toBe('usage')
  })

  it('非法 sort 值回退默认 usage', () => {
    routeState.query = { sort: 'bogus' }
    const t = useTagManage()
    expect(t.sortMode.value).toBe('usage')
  })

  it('selectTag 后把标签 id 同步到 URL query', async () => {
    const t = useTagManage()
    t.selectTag(makeGroups()[0].tags[0])
    await nextTick()
    expect(routeState.replace).toHaveBeenCalledWith({ query: { tag: '1' } })
  })

  it('loadAll 后从 URL query.tag 恢复选中标签（详情页返回场景）', async () => {
    routeState.query = { tag: '2' }
    const t = useTagManage()
    await t.loadAll()
    expect(t.selectedTag.value?.id).toBe(2)
    expect(t.selectedTag.value?.name).toBe('韩系')
  })

  it('URL 中标签已不存在时保持空选中', async () => {
    routeState.query = { tag: '999' }
    const t = useTagManage()
    await t.loadAll()
    expect(t.selectedTag.value).toBeNull()
  })

  it('搜索与筛选变化时同步 URL，默认值不写入', async () => {
    const t = useTagManage()
    t.searchQuery.value = '连衣裙'
    await nextTick()
    expect(routeState.replace).toHaveBeenCalledWith({ query: { q: '连衣裙' } })

    t.filterCategory.value = 'style'
    t.sortMode.value = 'name'
    await nextTick()
    expect(routeState.replace).toHaveBeenCalledWith({ query: { q: '连衣裙', category: 'style', sort: 'name' } })

    // 全部回到默认：URL query 清空
    t.searchQuery.value = ''
    t.filterCategory.value = null
    t.sortMode.value = 'usage'
    await nextTick()
    expect(routeState.replace).toHaveBeenCalledWith({ query: {} })
  })

  it('loadAll 刷新后选中标签按 id 替换引用并保持 URL 同步', async () => {
    routeState.query = { tag: '1' }
    const t = useTagManage()
    await t.loadAll()
    expect(t.selectedTag.value?.id).toBe(1)
    // loadAll 内部恢复选中后，watch 会再次同步 URL（值不变，无副作用）
    expect(routeState.replace).toHaveBeenLastCalledWith({ query: { tag: '1' } })
  })
})
