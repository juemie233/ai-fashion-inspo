/** 搜索相关 API 调用。 */

import apiClient from './client'
import type { InspirationListOut } from './inspirations'

export interface SearchQuery {
  include_tags?: string
  exclude_tags?: string
  keyword?: string
  dominant_color?: string
  source_type?: string
  media_type?: string
  analysis_status?: string
  tag_status?: string
  date_from?: string
  date_to?: string
  sort?: string
  combine?: 'AND' | 'OR'
  page?: number
  size?: number
}

export interface TagSuggestion {
  name: string
  usage_count: number
}

export interface CooccurrenceTag {
  name: string
  category: string
  shared_count: number
}

/** 多维度搜索 */
export async function searchInspirations(query: SearchQuery) {
  const { data } = await apiClient.get<InspirationListOut>('/search', {
    params: query,
  })
  return data
}

/** 搜索建议（标签名自动补全） */
export async function fetchSuggestions(q: string) {
  const { data } = await apiClient.get<{ items: TagSuggestion[] }>('/search/suggestions', { params: { q } })
  return data.items || data
}

/** 标签共现分析 */
export async function fetchCooccurrence(tagName: string) {
  const { data } = await apiClient.get<{
    tag: string
    related: CooccurrenceTag[]
  }>('/search/tag-cooccurrence', { params: { tag_name: tagName } })
  return data
}
