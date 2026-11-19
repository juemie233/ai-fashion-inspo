/** 收藏合集 API：手动合集（实体成员）与智能合集（筛选条件动态求值）。 */

import apiClient from './client'
import type { InspirationOut } from './inspirations'

/** 系统收藏根名称（与后端 MANUAL_ROOT_COLLECTION_NAME 保持一致）：
 * 智能合集 {is_favorite:true}，只能自动同步，不允许编辑/固化/删除。 */
export const SYSTEM_MANUAL_COLLECTION_NAME = '系统入库手动收藏'

/** 智能合集筛选条件（与素材库筛选口径一致，见 docs/features/收藏合集两级结构.md） */
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
  /** 父收藏夹 ID：一级为 null，二级为所属一级合集的 ID（最多两级） */
  parent_id: number | null
  /** 自动维护来源：'douyin' = 抖音同步自动建（名字不可改）；用户自建为 null */
  auto_source: string | null
  /** 直接子收藏夹数（一级节点用） */
  child_count: number
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
  /** 非空即建二级收藏夹（父必须是一级合集） */
  parent_id?: number | null
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

/** 合集内容筛选/排序参数（与 GET /collections/{id}/inspirations 契约一致） */
export interface CollectionInspirationParams {
  page?: number
  /** 每页条数：后端参数名是 `page_size`（上限 200），传 `size` 会被忽略、永远拿默认 50 条 */
  page_size?: number
  /** 关键词（后端按标签名/作者/来源等模糊匹配） */
  q?: string
  media_type?: 'image' | 'video'
  quality_status?: 'pending' | 'approved' | 'rejected'
  rating_min?: number
  /** 仅看已收藏 */
  is_favorite?: boolean
  /** 排序：手动顺序 / 最新 / 最早 / 评分高 / 评分低 */
  sort?: 'manual' | 'newest' | 'oldest' | 'rating' | 'rating_asc'
}

export async function fetchCollectionInspirations(
  id: number,
  params: CollectionInspirationParams = {},
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

/** 合集间批量移动/复制结果 */
export interface CollectionTransferResult {
  /** 成功加入目标合集的素材数 */
  added: number
  /** 已存在/无法加入而跳过的数量 */
  skipped: number
  /** move 模式从源合集移除的数量（copy 模式不返回或为 0） */
  removed_from_source?: number
}

/**
 * 批量把源合集里的素材移动/复制到多个目标合集（仅手动合集、仅容器节点）。
 *
 * - `mode='move'`：先从源合集移除再加到目标（素材本体不变；同一素材可进多个目标）；
 * - `mode='copy'`：只加入目标，源合集保留。
 */
export async function transferCollectionItems(
  sourceId: number,
  inspirationIds: string[],
  targetIds: number[],
  mode: 'move' | 'copy',
): Promise<CollectionTransferResult> {
  const { data } = await apiClient.post<CollectionTransferResult>(
    `/collections/${sourceId}/inspirations/transfer`,
    { ids: inspirationIds, target_ids: targetIds, mode },
  )
  return data
}

/** 合集合并结果 */
export interface CollectionMergeResult {
  /** 被合并的源合集数量 */
  merged_collections: number
  /** 加入目标合集的素材数 */
  added: number
  /** 重复/无法加入而跳过的数量 */
  skipped: number
  /** 合并成功后删除的源合集数量（用户确认：合并后删除源） */
  deleted_sources: number
}

/**
 * 把多个源合集合并进目标合集（仅手动合集）。
 *
 * 后端确认口径：素材并入目标后**删除源合集**（用户已确认）；返回统计供前端提示。
 */
export async function mergeCollections(
  targetId: number,
  sourceIds: number[],
): Promise<CollectionMergeResult> {
  const { data } = await apiClient.post<CollectionMergeResult>(`/collections/${targetId}/merge`, {
    source_ids: sourceIds,
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
