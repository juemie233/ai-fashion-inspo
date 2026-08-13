/** 灵感素材相关 API 调用。 */

import apiClient from './client'

/** 审核状态 */
export type QualityStatus = 'pending' | 'approved' | 'rejected'

/** 灵感列表响应类型 */
export interface InspirationOut {
  id: string
  source_type: string
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
  created_at: string
  updated_at?: string | null
  tags: InspirationTagOut[]
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

/** 获取图片/缩略图的完整 URL */
export function getFileUrl(relativePath: string): string {
  return `/api/files/${relativePath}`
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

/** 上传灵感图片 */
export async function uploadInspiration(formData: FormData) {
  const { data } = await apiClient.post<InspirationOut>('/inspirations', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
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

/** 删除灵感 */
export async function deleteInspiration(id: string) {
  await apiClient.delete(`/inspirations/${id}`)
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

/** 批量删除所有已拒绝素材 */
export async function deleteRejectedInspirations() {
  const { data } = await apiClient.delete<{ deleted: number; freed_bytes: number; message?: string }>(
    '/inspirations/quality-rejected',
  )
  return data
}
