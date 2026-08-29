/** 收藏合集 API：手动合集（实体成员）与智能合集（筛选条件动态求值）。 */

import apiClient from './client'
import type { InspirationOut } from './inspirations'

/** 智能合集筛选条件（与素材库筛选口径一致，见 docs/收藏合集设计方案.md） */
export interface SmartCollectionQuery {
  keyword?: string | null
  tag_ids?: number[]
  tag_mode?: 'and' | 'or'
  source_types?: string[] | null
  media_type?: 'image' | 'video' | null
  is_favorite?: boolean | null
  min_rating?: number | null
  start_date?: string | null
  end_date?: string | null
}

export type CollectionKind = 'manual' | 'smart'

export interface CollectionOut {
  id: number
  name: string
  description: string | null
  kind: CollectionKind
  position: number
  cover_inspiration_id: string | null
  cover_thumbnail_path: string | null
  /** 手动合集 = 成员数；智能合集 = null（进入合集页才懒计算精确数） */
  item_count: number | null
  query_json: SmartCollectionQuery | null
  created_at: string
  updated_at: string
}

export interface CollectionInspirationsOut {
  items: InspirationOut[]
  total: number
  page: number
  size: number
}

export async function fetchCollections(): Promise<CollectionOut[]> {
  const { data } = await apiClient.get<CollectionOut[]>('/collections')
  return data
}

export async function createCollection(payload: {
  name: string
  description?: string | null
  query_json?: SmartCollectionQuery | null
}): Promise<CollectionOut> {
  const { data } = await apiClient.post<CollectionOut>('/collections', payload)
  return data
}

export async function updateCollection(
  id: number,
  payload: {
    name?: string
    description?: string | null
    cover_inspiration_id?: string | null
    query_json?: SmartCollectionQuery
  },
): Promise<CollectionOut> {
  const { data } = await apiClient.patch<CollectionOut>(`/collections/${id}`, payload)
  return data
}

export async function deleteCollection(id: number): Promise<void> {
  await apiClient.delete(`/collections/${id}`)
}

export async function fetchCollectionInspirations(
  id: number,
  params: { page?: number; size?: number } = {},
): Promise<CollectionInspirationsOut> {
  const { data } = await apiClient.get<CollectionInspirationsOut>(
    `/collections/${id}/inspirations`,
    { params },
  )
  return data
}

/** 批量加入素材（仅手动合集；智能合集后端返回 400） */
export async function addToCollection(
  id: number,
  inspirationIds: string[],
): Promise<{ added: number }> {
  const { data } = await apiClient.post<{ added: number }>(`/collections/${id}/inspirations`, {
    inspiration_ids: inspirationIds,
  })
  return data
}

/** 批量移出素材（仅手动合集） */
export async function removeFromCollection(
  id: number,
  inspirationIds: string[],
): Promise<{ removed: number }> {
  const { data } = await apiClient.delete<{ removed: number }>(`/collections/${id}/inspirations`, {
    data: { inspiration_ids: inspirationIds },
  })
  return data
}

/** 合集内素材拖拽排序（仅手动合集） */
export async function reorderCollectionItems(id: number, orderedIds: string[]): Promise<void> {
  await apiClient.patch(`/collections/${id}/items/order`, { ordered_ids: orderedIds })
}

/** 合集列表拖拽排序 */
export async function reorderCollections(orderedIds: number[]): Promise<void> {
  await apiClient.patch('/collections/order', { ordered_ids: orderedIds })
}

/** 智能合集转手动：按当前位置固化匹配成员并清空筛选条件 */
export async function solidifyCollection(id: number): Promise<CollectionOut> {
  const { data } = await apiClient.post<CollectionOut>(`/collections/${id}/solidify`)
  return data
}
