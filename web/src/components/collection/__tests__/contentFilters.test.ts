/**
 * 合集内容筛选/排序的纯逻辑测试：默认排序、参数拼装、筛选激活判断。
 *
 * 这些规则直接决定「切合集后看到什么」：
 * - 手动合集默认手动顺序、智能合集默认最新；
 * - 空条件不传参（避免后端把空串当关键词）；
 * - 排序永远显式传（后端据此区分 manual/newest）；
 * - 有任意筛选时「清除筛选」要出现。
 */

import { describe, expect, it } from 'vitest'
import {
  buildCollectionContentParams,
  contentSortLabel,
  defaultContentSort,
  isContentFilterActive,
  type ContentFilterState,
} from '../contentFilters'

function state(over: Partial<ContentFilterState> = {}): ContentFilterState {
  return {
    keyword: '',
    mediaType: '',
    quality: '',
    ratingMin: '',
    favoriteOnly: false,
    sort: 'manual',
    ...over,
  }
}

describe('合集内容筛选纯逻辑', () => {
  it('默认排序：手动合集=manual，智能合集=newest', () => {
    expect(defaultContentSort('manual')).toBe('manual')
    expect(defaultContentSort('smart')).toBe('newest')
    expect(defaultContentSort(null)).toBe('manual')
  })

  it('空条件只带分页与默认排序', () => {
    expect(buildCollectionContentParams(state(), 2, 50)).toEqual({
      page: 2,
      size: 50,
      sort: 'manual',
    })
  })

  it('关键词去空白；所有筛选与仅收藏都映射到接口字段', () => {
    const params = buildCollectionContentParams(
      state({
        keyword: '  白裙 ',
        mediaType: 'image',
        quality: 'approved',
        ratingMin: 4,
        favoriteOnly: true,
        sort: 'rating',
      }),
      1,
      50,
    )
    expect(params).toEqual({
      page: 1,
      size: 50,
      sort: 'rating',
      q: '白裙',
      media_type: 'image',
      quality_status: 'approved',
      rating_min: 4,
      is_favorite: true,
    })
  })

  it('只有空白关键词不算筛选（不显示清除筛选）', () => {
    expect(isContentFilterActive(state({ keyword: '   ' }))).toBe(false)
    expect(isContentFilterActive(state({ keyword: '裙' }))).toBe(true)
    expect(isContentFilterActive(state({ favoriteOnly: true }))).toBe(true)
    expect(isContentFilterActive(state({ ratingMin: 1 }))).toBe(true)
    expect(isContentFilterActive(state({ mediaType: 'video' }))).toBe(true)
    expect(isContentFilterActive(state({ quality: 'pending' }))).toBe(true)
  })

  it('排序文案可读（未知值原样展示）', () => {
    expect(contentSortLabel('manual')).toBe('手动顺序')
    expect(contentSortLabel('newest')).toBe('最新收藏/入库')
    expect(contentSortLabel('rating_asc')).toBe('评分从低到高')
  })
})
