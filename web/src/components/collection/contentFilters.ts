/** 合集内容筛选/排序的纯逻辑：默认排序、参数拼装、筛选是否激活、排序文案。
 *
 * 抽出来是为了让「切合集重置 / 手动合集默认手动顺序 / 智能合集默认最新 / 参数只带
 * 非空条件」这些规则能被单测直接锁住，不用挂载整个 CollectionsView 再操作下拉框。
 */

import type { CollectionInspirationParams, CollectionKind } from '@/api/collections'

/** 合集内排序：manual=手动顺序（仅手动合集）、newest/oldest、rating/rating_asc */
export type ContentSort = 'manual' | 'newest' | 'oldest' | 'rating' | 'rating_asc'
/** 媒体筛选（'' = 全部） */
export type ContentMedia = '' | 'image' | 'video'
/** 质量筛选（'' = 全部） */
export type ContentQuality = '' | 'pending' | 'approved' | 'rejected'
/** 评分下限（'' = 全部） */
export type ContentRatingMin = '' | 1 | 2 | 3 | 4 | 5

/** 合集内容工具条的筛选状态 */
export interface ContentFilterState {
  keyword: string
  mediaType: ContentMedia
  quality: ContentQuality
  ratingMin: ContentRatingMin
  favoriteOnly: boolean
  sort: ContentSort
}

export const MEDIA_OPTIONS = [
  { label: '全部媒体', value: '' },
  { label: '图片', value: 'image' },
  { label: '视频', value: 'video' },
]

export const QUALITY_OPTIONS = [
  { label: '全部质量', value: '' },
  { label: '待审核', value: 'pending' },
  { label: '已通过', value: 'approved' },
  { label: '已拒绝', value: 'rejected' },
]

export const RATING_OPTIONS = [
  { label: '全部评分', value: '' },
  { label: '≥ 1 星', value: 1 },
  { label: '≥ 2 星', value: 2 },
  { label: '≥ 3 星', value: 3 },
  { label: '≥ 4 星', value: 4 },
  { label: '5 星', value: 5 },
]

export const CONTENT_SORT_OPTIONS = [
  { label: '手动顺序', value: 'manual' },
  { label: '最新收藏/入库', value: 'newest' },
  { label: '最早收藏/入库', value: 'oldest' },
  { label: '评分从高到低', value: 'rating' },
  { label: '评分从低到高', value: 'rating_asc' },
]

/** 排序选项对应的中文名（未知值原样返回） */
export function contentSortLabel(sort: ContentSort): string {
  return CONTENT_SORT_OPTIONS.find((o) => o.value === sort)?.label ?? sort
}

/** 当前合集的默认排序：手动合集按手动顺序，智能合集按最新收藏/入库 */
export function defaultContentSort(kind: CollectionKind | null | undefined): ContentSort {
  return kind === 'smart' ? 'newest' : 'manual'
}

/** 除关键词外是否有筛选条件（决定是否显示「清除筛选」） */
export function isContentFilterActive(filters: ContentFilterState): boolean {
  return (
    Boolean(filters.keyword.trim()) ||
    Boolean(filters.mediaType) ||
    Boolean(filters.quality) ||
    filters.ratingMin !== '' ||
    filters.favoriteOnly
  )
}

/** 拼装合集内容请求参数：空条件不传，默认排序也显式传（后端据此决定 manual/newest） */
export function buildCollectionContentParams(
  filters: ContentFilterState,
  page: number,
  size: number,
): CollectionInspirationParams {
  const keyword = filters.keyword.trim()
  return {
    page,
    size,
    sort: filters.sort,
    ...(keyword ? { q: keyword } : {}),
    ...(filters.mediaType ? { media_type: filters.mediaType } : {}),
    ...(filters.quality ? { quality_status: filters.quality } : {}),
    ...(filters.ratingMin !== '' ? { rating_min: filters.ratingMin } : {}),
    ...(filters.favoriteOnly ? { is_favorite: true } : {}),
  }
}
