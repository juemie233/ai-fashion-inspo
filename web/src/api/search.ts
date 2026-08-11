/** 搜索相关 API 调用。 */

import apiClient from './client'
import type { InspirationListOut } from './inspirations'

export interface SearchQuery {
  include_tags?: string
  exclude_tags?: string
  dominant_color?: string
  source_type?: string
  date_from?: string
  date_to?: string
  combine?: 'AND' | 'OR'
  page?: number
  size?: number
}

/** 多维度搜索 */
export async function searchInspirations(query: SearchQuery) {
  const { data } = await apiClient.get<InspirationListOut>('/search', {
    params: query,
  })
  return data
}
