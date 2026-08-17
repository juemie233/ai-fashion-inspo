/** browseQuery 筛选/排序映射与本地持久化单测。 */

import { beforeEach, describe, expect, it } from 'vitest'
import {
  buildBrowseParams,
  storedBrowseSort,
  storedBrowsePageSize,
  SORT_STORAGE_KEY,
  PAGE_SIZE_STORAGE_KEY,
  parseFocusIds,
} from '../browseQuery'

describe('buildBrowseParams', () => {
  it('映射分页与基础字段', () => {
    const p = buildBrowseParams({}, 2, 50)
    expect(p.page).toBe(2)
    expect(p.size).toBe(50)
    expect(p.source_type).toBeUndefined()
  })

  it('映射来源/媒体/状态/质量筛选', () => {
    const p = buildBrowseParams(
      { source: 'xiaohongshu', media: 'video', status: 'done', quality: 'approved' },
      1,
      50,
    )
    expect(p.source_type).toBe('xiaohongshu')
    expect(p.media_type).toBe('video')
    expect(p.analysis_status).toBe('done')
    expect(p.quality_status).toBe('approved')
  })

  it('映射标签/颜色/日期筛选', () => {
    const p = buildBrowseParams(
      { tags: '法式,白色', color: '#FF0000', date_from: '2026-01-01', date_to: '2026-02-01' },
      1,
      50,
    )
    expect(p.include_tags).toBe('法式,白色')
    expect(p.dominant_color).toBe('#FF0000')
    expect(p.date_from).toBe('2026-01-01')
    expect(p.date_to).toBe('2026-02-01')
  })

  it('收藏/无标签/疑似 AI 映射为布尔与状态', () => {
    expect(buildBrowseParams({ status: 'favorites' }, 1, 50).is_favorite).toBe(true)
    expect(buildBrowseParams({ status: 'untagged' }, 1, 50).tag_status).toBe('untagged')

    const ai = buildBrowseParams({ quality: 'ai' }, 1, 50)
    expect(ai.is_ai_generated).toBe(true)
    expect(ai.quality_status).toBeUndefined()
  })

  it('all 来源/媒体/质量不传参', () => {
    const p = buildBrowseParams({ source: 'all', media: 'all', quality: 'all' }, 1, 50)
    expect(p.source_type).toBeUndefined()
    expect(p.media_type).toBeUndefined()
    expect(p.quality_status).toBeUndefined()
  })

  it('sort 使用 state.sort，缺失时回退本地持久化', () => {
    expect(buildBrowseParams({ sort: 'tag_count' }, 1, 50).sort).toBe('tag_count')
    expect(buildBrowseParams({}, 1, 50).sort).toBe(storedBrowseSort())
  })

  it('映射 ids 精确过滤（定位跳转），缺失时不传参', () => {
    const p = buildBrowseParams({ ids: 'id1,id2' }, 1, 50)
    expect(p.ids).toBe('id1,id2')
    expect(buildBrowseParams({}, 1, 50).ids).toBeUndefined()
  })
})

describe('parseFocusIds', () => {
  it('解析逗号分隔的定位 ID，去空去空格', () => {
    expect(parseFocusIds({ focus: 'id1, id2 ,,id3' })).toEqual(['id1', 'id2', 'id3'])
  })

  it('无 focus 参数或为空时返回空数组', () => {
    expect(parseFocusIds({})).toEqual([])
    expect(parseFocusIds({ focus: '' })).toEqual([])
    expect(parseFocusIds({ focus: 123 })).toEqual([])
  })
})

describe('storedBrowseSort / storedBrowsePageSize', () => {
  beforeEach(() => localStorage.clear())

  it('无持久化时回退默认', () => {
    expect(storedBrowseSort()).toBe('newest')
    expect(storedBrowsePageSize()).toBe(50)
  })

  it('读取持久化值', () => {
    localStorage.setItem(SORT_STORAGE_KEY, 'random')
    localStorage.setItem(PAGE_SIZE_STORAGE_KEY, '100')
    expect(storedBrowseSort()).toBe('random')
    expect(storedBrowsePageSize()).toBe(100)
  })

  it('非法每页数量回退 50', () => {
    localStorage.setItem(PAGE_SIZE_STORAGE_KEY, 'abc')
    expect(storedBrowsePageSize()).toBe(50)
  })
})
