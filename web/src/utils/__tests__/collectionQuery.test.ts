/** collectionQuery 工具单测：素材库筛选状态 ↔ 智能合集条件的序列化。 */

import { describe, expect, it } from 'vitest'
import { buildSmartQuery, describeSmartQuery, hasActiveFilters } from '../collectionQuery'

const noFilters = {
  source: 'all',
  media: 'all',
  status: 'all',
  quality: 'all',
  tags: [] as string[],
  color: '',
  ratingMin: '',
  keyword: '',
}

const nameToId = (name: string) => (name === '法式穿搭' ? 12 : name === '通勤' ? 34 : undefined)

describe('buildSmartQuery', () => {
  it('无实质条件时返回空条件对象', () => {
    expect(buildSmartQuery(noFilters, nameToId)).toEqual({})
  })

  it('序列化来源/媒体/收藏/评分条件', () => {
    const q = buildSmartQuery(
      { ...noFilters, source: 'xiaohongshu', media: 'image', status: 'favorites', ratingMin: '3' },
      nameToId,
    )
    expect(q).toEqual({
      source_types: ['xiaohongshu'],
      media_type: 'image',
      is_favorite: true,
      min_rating: 3,
    })
  })

  it('标签名映射为 tag_id，未知名跳过；语义为 AND', () => {
    const q = buildSmartQuery({ ...noFilters, tags: ['法式穿搭', '通勤', '不存在'] }, nameToId)
    expect(q.tag_ids).toEqual([12, 34])
    expect(q.tag_mode).toBe('and')
  })

  it('关键词保留首尾去空格', () => {
    const q = buildSmartQuery({ ...noFilters, keyword: '  白色衬衫  ' }, nameToId)
    expect(q.keyword).toBe('白色衬衫')
  })
})

describe('hasActiveFilters', () => {
  it('默认筛选 = 无条件', () => {
    expect(hasActiveFilters(noFilters)).toBe(false)
  })
  it('仅评分筛选即算有条件', () => {
    expect(hasActiveFilters({ ...noFilters, ratingMin: '2' })).toBe(true)
  })
})

describe('describeSmartQuery', () => {
  it('生成人读条件摘要', () => {
    const text = describeSmartQuery(
      {
        keyword: '衬衫',
        tag_ids: [12, 34],
        tag_mode: 'and',
        source_types: ['xiaohongshu'],
        media_type: 'video',
        is_favorite: true,
        min_rating: 4,
      },
      (id) => (id === 12 ? '法式穿搭' : id === 34 ? '通勤' : undefined),
      (v) => (v === 'xiaohongshu' ? '小红书' : v),
    )
    expect(text).toContain('关键词「衬衫」')
    expect(text).toContain('法式穿搭 + 通勤')
    expect(text).toContain('小红书')
    expect(text).toContain('视频')
    expect(text).toContain('仅收藏')
    expect(text).toContain('4 星及以上')
  })
  it('空条件显示「全部素材」', () => {
    expect(
      describeSmartQuery(
        {},
        () => undefined,
        () => '',
      ),
    ).toBe('全部素材')
  })
})
