/**
 * 首页筛选域（`useInspirationFilters`）补测（原 77% 覆盖）。
 *
 * 既有 `useInspirationFilters.test.ts` 覆盖了 URL 初始化/setter/清空；本文件补：
 * - 定位模式（`?focus=`）的进入/清除与 focus 参数变化监听（含重置筛选、同值不重复加载）；
 * - 标签下拉分组与全部标签名（编辑智能合集条件时的候选）；
 * - 主色调加载成功/失败静默；
 * - filtersActive 的真实口径（任何实质条件即为 true，决定「保存为合集」是否二次确认）；
 * - 排序偏好持久化（refresh 后保持）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick, reactive, ref } from 'vue'

const mocks = vi.hoisted(() => ({
  route: { query: {} as Record<string, string> } as { query: Record<string, string> },
  fetchDominantColors: vi.fn(),
  tagsStore: null as unknown,
}))

vi.mock('vue-router', () => ({ useRoute: () => mocks.route }))
vi.mock('@/api/inspirations', () => ({
  fetchDominantColors: mocks.fetchDominantColors,
}))
vi.mock('@/stores/tags', () => ({ useTagsStore: () => mocks.tagsStore }))

import { useInspirationFilters } from '../useInspirationFilters'

function makeTagsStore() {
  const groups = ref([
    {
      category: 'style',
      tags: [
        {
          id: 1,
          name: '法式',
          category: 'style',
          source: 'manual',
          pinned: false,
          sort_order: 0,
          description: null,
          usage_count: 1,
        },
      ],
    },
    {
      category: 'scene',
      tags: [
        {
          id: 2,
          name: '通勤',
          category: 'scene',
          source: 'manual',
          pinned: false,
          sort_order: 0,
          description: null,
          usage_count: 2,
        },
      ],
    },
  ])
  return {
    // Pinia store 会解包 ref；mock 的普通对象用 getter 暴露数组，computed 仍能追踪
    get groups() {
      return groups.value
    },
    getCategoryLabel: (c: string) => (c === 'style' ? '风格' : c === 'scene' ? '场景' : c),
  }
}

function setup(query: Record<string, string> = {}) {
  mocks.route.query = { ...query }
  const reloadFirstPage = vi.fn()
  const loadPage = vi.fn()
  const filters = useInspirationFilters({ reloadFirstPage, loadPage })
  return { filters, reloadFirstPage, loadPage }
}

describe('useInspirationFilters 边界补充', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    mocks.tagsStore = makeTagsStore()
    mocks.fetchDominantColors.mockResolvedValue([{ color: '#ffffff', count: 3 }])
  })

  it('resetFiltersForFocus：定位模式重置全部筛选与排序', () => {
    const { filters } = setup({
      source: 'douyin',
      media: 'video',
      status: 'untagged',
      quality: 'approved',
      tags: '法式',
      color: '#000000',
      rating_min: '4',
      sort: 'rating',
    })

    filters.resetFiltersForFocus()

    expect(filters.sourceFilter.value).toBe('all')
    expect(filters.mediaFilter.value).toBe('all')
    expect(filters.statusFilter.value).toBe('all')
    expect(filters.qualityFilter.value).toBe('all')
    expect(filters.selectedTags.value).toEqual([])
    expect(filters.colorFilter.value).toBe('')
    expect(filters.ratingMin.value).toBe('')
    expect(filters.sortMode.value).toBe('newest')
  })

  it('clearFocus：无定位时不加载；有定位时清空并按第 1 页加载', () => {
    const empty = setup()
    empty.filters.clearFocus()
    expect(empty.loadPage).not.toHaveBeenCalled()

    const { filters, loadPage } = setup({ focus: 'a,b' })
    expect(filters.focusedIds.value).toEqual(['a', 'b'])
    filters.clearFocus()
    expect(filters.focusedIds.value).toEqual([])
    expect(loadPage).toHaveBeenCalledWith(1)
  })

  it('focus 变化监听：有 ID 时重置筛选并 loadPage(1)，清空时只回第一页', async () => {
    const query = reactive<Record<string, string>>({})
    mocks.route.query = query
    const reloadFirstPage = vi.fn()
    const loadPage = vi.fn()
    const filters = useInspirationFilters({ reloadFirstPage, loadPage })
    // 预先设置一个非默认筛选
    filters.sourceFilter.value = 'douyin'

    query.focus = 'i1,i2'
    await nextTick()

    expect(filters.focusedIds.value).toEqual(['i1', 'i2'])
    expect(filters.sourceFilter.value).toBe('all')
    expect(loadPage).toHaveBeenCalledWith(1)

    loadPage.mockClear()
    query.focus = 'i1,i2'
    await nextTick()
    expect(loadPage).not.toHaveBeenCalled() // 同值不重复加载

    query.focus = ''
    await nextTick()
    expect(filters.focusedIds.value).toEqual([])
    expect(loadPage).toHaveBeenCalledWith(1)
  })

  it('标签下拉按类别分组，全部标签名扁平化', () => {
    const { filters } = setup()

    expect(filters.tagFilterOptions.value).toEqual([
      {
        type: 'group',
        label: '风格',
        key: 'style',
        children: [{ label: '法式', value: '法式' }],
      },
      {
        type: 'group',
        label: '场景',
        key: 'scene',
        children: [{ label: '通勤', value: '通勤' }],
      },
    ])
    expect(filters.allTagNames.value).toEqual(['法式', '通勤'])
  })

  it('主色调加载：成功回填；失败静默回退空数组', async () => {
    const { filters } = setup()

    await filters.loadDominantColors()
    expect(mocks.fetchDominantColors).toHaveBeenCalledWith(30)
    expect(filters.dominantColors.value).toEqual([{ color: '#ffffff', count: 3 }])

    mocks.fetchDominantColors.mockRejectedValueOnce(new Error('500'))
    await filters.loadDominantColors()
    expect(filters.dominantColors.value).toEqual([])
  })

  it('各 setter 更新状态并触发重新加载（首屏数据不静默失效）', () => {
    const { filters, reloadFirstPage } = setup()

    filters.setSourceFilter('douyin')
    expect(filters.sourceFilter.value).toBe('douyin')
    filters.setMediaFilter('video')
    filters.setStatusFilter('favorites')
    filters.setQualityFilter('pending')
    filters.setColorFilter('#abcdef')
    filters.setSortMode('rating')

    expect(filters.mediaFilter.value).toBe('video')
    expect(filters.statusFilter.value).toBe('favorites')
    expect(filters.qualityFilter.value).toBe('pending')
    expect(filters.colorFilter.value).toBe('#abcdef')
    expect(filters.sortMode.value).toBe('rating')
    expect(reloadFirstPage).toHaveBeenCalledTimes(6)
  })

  it('removeTagFilter 只移除指定标签；clearAllFilters 重置全部条件', () => {
    const { filters, reloadFirstPage } = setup({
      tags: '法式,通勤',
      source: 'douyin',
      media: 'image',
      status: 'done',
      quality: 'approved',
      color: '#fff',
      rating_min: '3',
      run_id: '9',
      sort: 'tag_count',
    })

    filters.removeTagFilter('法式')
    expect(filters.selectedTags.value).toEqual(['通勤'])

    filters.clearAllFilters()
    expect(filters.sourceFilter.value).toBe('all')
    expect(filters.mediaFilter.value).toBe('all')
    expect(filters.statusFilter.value).toBe('all')
    expect(filters.qualityFilter.value).toBe('all')
    expect(filters.selectedTags.value).toEqual([])
    expect(filters.colorFilter.value).toBe('')
    expect(filters.ratingMin.value).toBe('')
    expect(filters.generationRunId.value).toBe('')
    expect(filters.sortMode.value).toBe('newest')
    expect(reloadFirstPage).toHaveBeenCalledTimes(2)
  })

  it('filtersActive：来源/媒体/评分/仅收藏算实质条件；仅标签筛选当前不算（疑似缺陷）', () => {
    const { filters } = setup()
    expect(filters.filtersActive.value).toBe(false)

    filters.setSourceFilter('douyin')
    expect(filters.filtersActive.value).toBe(true)

    filters.clearAllFilters()
    filters.setMediaFilter('video')
    expect(filters.filtersActive.value).toBe(true)

    filters.clearAllFilters()
    filters.ratingMin.value = '3'
    expect(filters.filtersActive.value).toBe(true)

    filters.clearAllFilters()
    filters.setStatusFilter('favorites')
    expect(filters.filtersActive.value).toBe(true)

    // 已知现状：hasActiveFilters 的 nameToId 恒为 undefined，仅标签筛选判不出「有实质条件」。
    // 保存为智能合集时可能少一次二次确认——用例锁定现状，报告中作为疑似缺陷提示。
    filters.clearAllFilters()
    filters.selectedTags.value = ['法式']
    expect(filters.filtersActive.value).toBe(false)

    // 状态筛选非「仅收藏」、颜色、排序都不在智能合集条件内，不算实质条件
    filters.clearAllFilters()
    filters.setStatusFilter('untagged')
    expect(filters.filtersActive.value).toBe(false)
    filters.setColorFilter('#fff')
    expect(filters.filtersActive.value).toBe(false)
  })

  it('排序偏好写入 localStorage（刷新后保持上次选择）', async () => {
    const { filters } = setup()

    filters.setSortMode('largest')
    await nextTick()

    expect(localStorage.getItem('masonry-sort')).toBe('largest')
  })
})
