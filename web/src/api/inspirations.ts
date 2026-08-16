/** 灵感素材相关 API 调用。 */

import apiClient from './client'
import type { PersonBrief } from '@shared/types/person'

/** 审核状态 */
export type QualityStatus = 'pending' | 'approved' | 'rejected'

/** 垃圾桶删除原因（负样本学习只用「质量差」子集保证语义纯净） */
export type TrashReason = '质量差' | '重复' | '不喜欢' | '隐私' | '其他'

/** 删除原因可选项（前端下拉用） */
export const TRASH_REASON_OPTIONS: { label: string; value: TrashReason }[] = [
  { label: '质量差', value: '质量差' },
  { label: '重复', value: '重复' },
  { label: '不喜欢', value: '不喜欢' },
  { label: '隐私', value: '隐私' },
  { label: '其他', value: '其他' },
]

/** 灵感列表响应类型 */
export interface InspirationOut {
  id: string
  source_type?: string
  source_url?: string | null
  source_author?: string | null
  source_platform_id?: string | null
  file_path: string
  thumbnail_path?: string | null
  media_type: string
  dominant_colors?: string | null
  is_favorite: boolean
  quality_status?: QualityStatus | null
  quality_reason?: string | null
  is_ai_generated?: boolean
  deleted_at?: string | null
  trash_reason?: TrashReason | null
  created_at: string
  updated_at?: string | null
  tags: InspirationTagOut[]
  persons?: PersonBrief[]
  analysis_status?: string | null
}

export interface TagOut {
  id: number
  name: string
  category: string
}

export interface InspirationTagOut {
  tag: TagOut
  confidence: number
}

export interface InspirationListOut {
  items: InspirationOut[]
  total: number
  page: number
  size: number
  /** 垃圾桶保留天数（仅垃圾桶列表返回，前端据此展示剩余天数） */
  trash_retention_days?: number | null
}

export interface InspirationDetailOut extends InspirationOut {
  analysis_logs: AnalysisLogOut[]
}

export interface AnalysisLogOut {
  id: number
  model_name: string
  processing_time_ms?: number | null
  error?: string | null
  created_at: string
}

/** 获取图片/缩略图的完整 URL（逐段编码，兼容含空格/#/?/中文的文件名） */
export function getFileUrl(relativePath: string): string {
  return `/api/files/${relativePath.split('/').map(encodeURIComponent).join('/')}`
}

/** 手动给素材关联标签（按名称查找/创建） */
export async function addTagsToInspiration(
  id: string,
  names: string[],
  category = 'outfit',
  source = 'manual',
) {
  const { data } = await apiClient.post(`/inspirations/${id}/tags`, { names, category, source })
  return data
}

/** 批量打标结果 */
export interface BatchAddTagsResult {
  added: number
  affected: number
  skipped: number
  not_found: number
  skipped_existing: number
  missing_ids: string[]
}

/** 批量给多个素材关联标签（如相似素材批量添加穿搭大标签） */
export async function batchAddTagsToInspirations(
  inspirationIds: string[],
  names: string[],
  category = 'outfit',
  source = 'manual',
): Promise<BatchAddTagsResult> {
  const { data } = await apiClient.post<BatchAddTagsResult>('/inspirations/batch-tags', {
    inspiration_ids: inspirationIds,
    names,
    category,
    source,
  })
  return data
}

/** 批量收藏/取消收藏素材，返回实际更新行数 */
export async function batchFavorite(ids: string[], isFavorite: boolean): Promise<number> {
  const { data } = await apiClient.post<{ updated: number }>('/inspirations/batch-favorite', {
    ids,
    is_favorite: isFavorite,
  })
  return data.updated
}

/** 批量移入垃圾桶（软删除），返回 {trashed, skipped} */
export async function batchTrash(ids: string[], reason?: TrashReason) {
  const { data } = await apiClient.post<{ trashed: number; skipped: number }>(
    '/inspirations/batch-trash',
    reason ? { ids, reason } : { ids },
  )
  return data
}

/** 批量编辑元数据的字段集合（仅更新显式提供的字段） */
export interface BatchUpdateFields {
  source_type?: string
  is_favorite?: boolean
  quality_status?: QualityStatus
  is_ai_generated?: boolean
}

/** 批量编辑素材元数据（来源/收藏/审核状态/疑似 AI 标记） */
export async function batchUpdateInspirations(ids: string[], fields: BatchUpdateFields) {
  const { data } = await apiClient.post<{ updated: number }>('/inspirations/batch-update', {
    ids,
    ...fields,
  })
  return data.updated
}

/** 解除素材与标签的关联 */
export async function removeTagFromInspiration(id: string, tagId: number) {
  const { data } = await apiClient.delete(`/inspirations/${id}/tags/${tagId}`)
  return data
}

/** AI 建议穿搭大标签（只建议不入库） */
export async function suggestOutfitTags(id: string) {
  const { data } = await apiClient.post('/ai/outfit-tags/suggest', null, {
    params: { inspiration_id: id },
  })
  return data
}

/** 库内主色调统计条目（供颜色筛选） */
export interface DominantColorItem {
  color: string
  count: number
}

/** 获取库内实际出现的主色调及出现次数 */
export async function fetchDominantColors(limit = 30): Promise<DominantColorItem[]> {
  const { data } = await apiClient.get<DominantColorItem[]>('/inspirations/dominant-colors', {
    params: { limit },
  })
  return data
}

/** 获取灵感列表 */
export async function fetchInspirations(params: {
  page?: number
  size?: number
  source_type?: string
  is_favorite?: boolean
  media_type?: string
  analysis_status?: string
  tag_status?: string
  quality_status?: string
  is_ai_generated?: boolean
  include_tags?: string
  dominant_color?: string
  date_from?: string
  date_to?: string
  sort?: string
} = {}) {
  const { data } = await apiClient.get<InspirationListOut>('/inspirations', { params })
  return data
}

/** 获取灵感详情 */
export async function fetchInspiration(id: string) {
  const { data } = await apiClient.get<InspirationDetailOut>(`/inspirations/${id}`)
  return data
}

/** 上传灵感图片（支持透传上传进度回调与取消信号） */
export async function uploadInspiration(
  formData: FormData,
  onProgress?: (e: any) => void,
  signal?: AbortSignal,
) {
  const { data } = await apiClient.post<InspirationOut>('/inspirations', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress: onProgress,
    signal,
  })
  return data
}

/** 切换收藏状态 */
export async function toggleFavorite(id: string, isFavorite: boolean) {
  const { data } = await apiClient.patch<InspirationOut>(`/inspirations/${id}`, {
    is_favorite: isFavorite,
  })
  return data
}

/** 彻底删除灵感（物理删除，不可恢复；用于垃圾桶「彻底删除」等场景） */
export async function deleteInspiration(id: string) {
  await apiClient.delete(`/inspirations/${id}`)
}

/** 移入垃圾桶（软删除，可恢复；reason 为空时后端按素材状态自动推断） */
export async function moveToTrash(id: string, reason?: TrashReason) {
  const { data } = await apiClient.post<InspirationOut>(
    `/inspirations/${id}/trash`,
    reason ? { reason } : {},
  )
  return data
}

/** 从垃圾桶恢复素材 */
export async function restoreInspiration(id: string) {
  const { data } = await apiClient.post<InspirationOut>(`/inspirations/${id}/restore`)
  return data
}

/** 获取垃圾桶素材列表 */
export async function fetchTrash(params: {
  page?: number
  size?: number
  reason?: TrashReason
} = {}) {
  const { data } = await apiClient.get<InspirationListOut>('/inspirations/trash', { params })
  return data
}

/** 清空垃圾桶（onlyExpired=true 仅清理超过保留期的过期素材） */
export async function emptyTrash(onlyExpired = false) {
  const { data } = await apiClient.delete<{ deleted: number; freed_bytes: number; message?: string }>(
    '/inspirations/trash',
    { params: { only_expired: onlyExpired } },
  )
  return data
}

/** 触发 AI 分析（用于手动重新分析） */
export async function analyzeInspiration(id: string) {
  const { data } = await apiClient.post(`/ai/analyze/${id}`)
  return data
}

/** 批量质量审核（审核所有待审核素材） */
export async function batchQualityCheck(limit?: number) {
  const { data } = await apiClient.post<{ message: string; count: number }>(
    '/ai/quality-check',
    null,
    { params: { limit } },
  )
  return data
}

/** 人工复核：修改素材审核状态 */
export async function updateQualityStatus(id: string, qualityStatus: QualityStatus) {
  const { data } = await apiClient.patch<InspirationOut>(`/inspirations/${id}`, {
    quality_status: qualityStatus,
  })
  return data
}

/** 人工复核：批量将疑似 AI 素材重新标记为非 AI */
export async function batchUnmarkAi(ids: string[]) {
  const { data } = await apiClient.post<{ updated: number }>('/admin/batch-unmark-ai', { ids })
  return data
}

/** 批量删除所有已拒绝素材 */
export async function deleteRejectedInspirations() {
  const { data } = await apiClient.delete<{ deleted: number; freed_bytes: number; message?: string }>(
    '/inspirations/quality-rejected',
  )
  return data
}
