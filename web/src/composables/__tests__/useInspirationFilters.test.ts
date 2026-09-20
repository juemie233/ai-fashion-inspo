/**
 * useInspirationFilters 测试：URL 初始化、setter 回调、清空/移除、filtersActive。
 *
 * 为什么锁这些：它是首页筛选的唯一入口，setter 忘了回调「重新加载」会让界面
 * 改了筛选但列表不动（静默失效）；filtersActive 判断错会让「保存为合集」少一次
 * 二次确认，把全库动态合集当普通合集存下来。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useInspirationFilters } from '../useInspirationFilters'

/** 模拟当前路由 query（模块级可变，供 useRoute 返回） */
const route = vi.hoisted(() => ({ query: {} as Record<string, string> }))

vi.mock('vue-router', () => ({ useRoute: () => route }))
vi.mock('@/api/inspirations', () => ({ fetchDominantColors: vi.fn(async () => []) }))

function setup() {
  const reloadFirstPage = vi.fn()
  const loadPage = vi.fn()
  const filters = useInspirationFilters({ reloadFirstPage, loadPage })
  return { filters, reloadFirstPage, loadPage }
}

describe('useInspirationFilters', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    route.query = {}
    localStorage.clear()
  })

  it('从 URL query 恢复筛选状态（刷新/详情返回时保持）', () => {
    route.query = {
      source: 'douyin',
      media: 'video',
      status: 'untagged',
      quality: 'approved',
      tags: 'JK制服,御姐风',
      color: '#ffffff',
      rating_min: '3',
      sort: 'rating',
      focus: 'id-1,id-2',
    }
    const { filters } = setup()

    expect(filters.sourceFilter.value).toBe('douyin')
    expect(filters.mediaFilter.value).toBe('video')
    expect(filters.statusFilter.value).toBe('untagged')
    expect(filters.qualityFilter.value).toBe('approved')
    expect(filters.selectedTags.value).toEqual(['JK制服', '御姐风'])
    expect(filters.colorFilter.value).toBe('#ffffff')
    expect(filters.ratingMin.value).toBe('3')
    expect(filters.sortMode.value).toBe('rating')
    expect(filters.focusedIds.value).toEqual(['id-1', 'id-2'])
  })

  it('未带 query 时用默认值（全库 / 最新在前 / 无筛选）', () => {
    const { filters } = setup()

    expect(filters.sourceFilter.value).toBe('all')
    expect(filters.mediaFilter.value).toBe('all')
    expect(filters.sortMode.value).toBe('newest')
    expect(filters.selectedTags.value).toEqual([])
    expect(filters.focusedIds.value).toEqual([])
  })

  it('setter 先改状态再回调重新加载（筛选与排序走同一入口）', () => {
    const { filters, reloadFirstPage } = setup()

    filters.setSourceFilter('xiaohongshu')
    expect(filters.sourceFilter.value).toBe('xiaohongshu')
    expect(reloadFirstPage).toHaveBeenCalledTimes(1)

    filters.setMediaFilter('image')
    filters.setStatusFilter('done')
    filters.setQualityFilter('pending')
    filters.setColorFilter('#123456')
    filters.setSortMode('rating')
    expect(reloadFirstPage).toHaveBeenCalledTimes(6)
    expect(filters.colorFilter.value).toBe('#123456')
    expect(filters.sortMode.value).toBe('rating')
  })

  it('removeTagFilter 只移除指定标签并触发重载', () => {
    route.query = { tags: 'A,B,C' }
    const { filters, reloadFirstPage } = setup()

    filters.removeTagFilter('B')

    expect(filters.selectedTags.value).toEqual(['A', 'C'])
    expect(reloadFirstPage).toHaveBeenCalledTimes(1)
  })

  it('clearAllFilters 复位全部条件（保留定位模式）并触发一次重载', () => {
    route.query = { source: 'douyin', tags: 'A', color: '#fff', rating_min: '4', focus: 'x' }
    const { filters, reloadFirstPage } = setup()

    filters.clearAllFilters()

    expect(filters.sourceFilter.value).toBe('all')
    expect(filters.selectedTags.value).toEqual([])
    expect(filters.colorFilter.value).toBe('')
    // 已知缺口（本次重构保持行为不变）：clearAllFilters 未复位评分筛选，
    // 与 resetFiltersForFocus 的口径不一致——即「清除全部筛选」会漏掉「★N 分及以上」。
    expect(filters.ratingMin.value).toBe('4')
    expect(filters.sortMode.value).toBe('newest')
    expect(filters.focusedIds.value).toEqual(['x']) // 定位模式不在此处清除
    expect(reloadFirstPage).toHaveBeenCalledTimes(1)
  })

  it('filtersActive：无实质条件时为 false，有条件时为 true', () => {
    const { filters } = setup()
    expect(filters.filtersActive.value).toBe(false)

    filters.setSortMode('rating') // 排序不算「实质筛选条件」
    expect(filters.filtersActive.value).toBe(false)

    filters.setSourceFilter('douyin')
    expect(filters.filtersActive.value).toBe(true)
  })

  it('无定位时 clearFocus 不触发加载', () => {
    const { filters, loadPage } = setup()

    filters.clearFocus()

    expect(loadPage).not.toHaveBeenCalled()
  })

  it('有定位时 clearFocus 清空并回到第一页', () => {
    route.query = { focus: 'x,y' }
    const { filters, loadPage } = setup()

    filters.clearFocus()

    expect(filters.focusedIds.value).toEqual([])
    expect(loadPage).toHaveBeenCalledWith(1)
  })
})
