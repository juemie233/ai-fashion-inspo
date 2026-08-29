/** 素材库筛选状态 → 智能合集 query_json 的序列化与反序列化。
 *
 * 口径保证：素材库怎么筛、智能合集就怎么算（见 docs/收藏合集设计方案.md 二）。
 * 注意素材库的标签筛选是「标签名」，保存为智能合集时经 tagsStore 映射为 tag_id；
 * 质量审核筛选（quality）暂不在智能合集条件内（契约字段未包含）。
 */

import type { SmartCollectionQuery } from '@/api/collections'

/** 素材库筛选状态（HomeView 的筛选 ref 子集，全部为字符串/字符串数组原始形态） */
export interface BrowseFilterState {
  source: string // 'all' | 具体来源
  media: string // 'all' | 'image' | 'video'
  status: string // 'all' | 'done' | 'pending' | 'untagged' | 'favorites'
  quality: string
  tags: string[] // 标签名列表
  color: string // 主色调 hex（智能合集条件暂不包含颜色，忽略）
  ratingMin: string // '' | '1'~'5'
  keyword: string // 关键词（空串 = 无；素材库暂无关键词输入，预留）
}

/** 标签名 → ID 映射器（由调用方从 tagsStore.groups 构建后传入） */
export type TagNameToId = (name: string) => number | undefined

/** 把素材库筛选状态序列化为智能合集条件；无任何有效条件时返回空条件对象 */
export function buildSmartQuery(f: BrowseFilterState, nameToId: TagNameToId): SmartCollectionQuery {
  const query: SmartCollectionQuery = {}

  if (f.keyword && f.keyword.trim()) query.keyword = f.keyword.trim()
  if (f.source && f.source !== 'all') query.source_types = [f.source]
  if (f.media && f.media !== 'all') query.media_type = f.media as 'image' | 'video'
  if (f.status === 'favorites') query.is_favorite = true
  const rating = parseInt(f.ratingMin, 10)
  if (!Number.isNaN(rating) && rating > 0) query.min_rating = rating

  const tagIds = (f.tags || []).map(nameToId).filter((id): id is number => id !== undefined)
  if (tagIds.length > 0) {
    query.tag_ids = tagIds
    query.tag_mode = 'and' // 素材库标签筛选语义为「需同时包含」= AND
  }

  // 日期范围：素材库浏览页暂无日期筛选，预留字段（保持 null 即不限）
  return query
}

/** 判断筛选状态是否「有实质条件」（决定保存为合集按钮的提示文案） */
export function hasActiveFilters(f: BrowseFilterState): boolean {
  const q = buildSmartQuery(f, () => undefined)
  return (
    !!q.keyword ||
    (q.source_types?.length ?? 0) > 0 ||
    !!q.media_type ||
    q.is_favorite === true ||
    (q.min_rating ?? 0) > 0 ||
    (q.tag_ids?.length ?? 0) > 0
  )
}

/** 把智能合集条件反序列化为人读的条件摘要（合集页展示用） */
export function describeSmartQuery(
  q: SmartCollectionQuery | null,
  idToName: (id: number) => string | undefined,
  sourceLabel: (value: string) => string,
): string {
  if (!q) return ''
  const parts: string[] = []
  if (q.keyword) parts.push(`关键词「${q.keyword}」`)
  const tagNames = (q.tag_ids || []).map(idToName).filter(Boolean)
  if (tagNames.length > 0) {
    const joiner = q.tag_mode === 'or' ? ' 或 ' : ' + '
    parts.push(`标签 ${tagNames.join(joiner)}`)
  }
  if (q.source_types?.length) {
    parts.push(q.source_types.map(sourceLabel).join('、'))
  }
  if (q.media_type) parts.push(q.media_type === 'image' ? '图片' : '视频')
  if (q.is_favorite) parts.push('仅收藏')
  if (q.min_rating) parts.push(`${q.min_rating} 星及以上`)
  if (q.start_date || q.end_date) {
    parts.push(`${q.start_date || '…'} ~ ${q.end_date || '…'}`)
  }
  return parts.length > 0 ? parts.join(' · ') : '全部素材'
}
