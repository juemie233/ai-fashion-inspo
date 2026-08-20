/** 灵感素材相关 API 调用。 */

import type { AxiosProgressEvent } from 'axios'
import apiClient from './client'
import { warnItems } from '@/utils/apiGuard'
import type { PersonBrief } from '@shared/types/person'

/** 审核状态 */
export type QualityStatus = 'pending' | 'approved' | 'rejected'

/** 垃圾桶删除原因（垃圾桶素材全部作为负样本学习输入；「AI生成」用于疑似 AI 素材自动移入） */
export type TrashReason = '质量差' | '重复' | '不喜欢' | '隐私' | '其他' | 'AI生成'

/** 移入垃圾桶来源：manual 手动移入 / auto 质量审核自动移动（垃圾桶据此展示来源） */
export type TrashSource = 'manual' | 'auto'

/** 删除原因可选项（前端下拉用） */
export const TRASH_REASON_OPTIONS: { label: string; value: TrashReason }[] = [
  { label: '质量差', value: '质量差' },
  { label: '重复', value: '重复' },
  { label: '不喜欢', value: '不喜欢' },
  { label: '隐私', value: '隐私' },
  { label: '其他', value: '其他' },
  { label: 'AI生成', value: 'AI生成' },
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
  rating?: number
  quality_status?: QualityStatus | null
  quality_reason?: string | null
  is_ai_generated?: boolean
  deleted_at?: string | null
  trash_reason?: TrashReason | null
  trash_source?: TrashSource | null
  created_at: string
  updated_at?: string | null
  tags: InspirationTagOut[]
  /** 关联穿搭博主（博主/模特已拆分两表，素材详情返回两个独立列表） */
  bloggers?: PersonBrief[]
  /** 关联职业模特 */
  models?: PersonBrief[]
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

/** 批量关联博主结果统计 */
export interface BatchLinkBloggersResult {
  linked: number
  affected: number
  not_found_count: number
  skipped: number
  message: string
}

/** 批量给多个素材关联穿搭博主（幂等：已关联自动跳过） */
export async function batchLinkBloggers(
  inspirationIds: string[],
  personIds: number[],
): Promise<BatchLinkBloggersResult> {
  const { data } = await apiClient.post<BatchLinkBloggersResult>(
    '/inspirations/batch-bloggers',
    {
      inspiration_ids: inspirationIds,
      person_ids: personIds,
    },
    // 批量上限 200 素材 × 50 博主，服务端逐素材写入可能较慢；
    // 放宽超时避免「服务端已成功、前端却误报失败」
    { timeout: 120000 },
  )
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

/** 批量移入垃圾桶（软删除），返回 {trashed, skipped}；source 标记来源（默认手动） */
export async function batchTrash(
  ids: string[],
  reason?: TrashReason,
  source: TrashSource = 'manual',
) {
  const { data } = await apiClient.post<{ trashed: number; skipped: number }>(
    '/inspirations/batch-trash',
    reason || source !== 'manual' ? { ids, reason, source } : { ids },
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
export async function fetchInspirations(
  params: {
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
    ids?: string
    rating_min?: number
    sort?: string
  } = {},
) {
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
  onProgress?: (e: AxiosProgressEvent) => void,
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

/** 设置素材评分（0~5，0 表示清除评分） */
export async function updateRating(id: string, rating: number) {
  const { data } = await apiClient.patch<InspirationOut>(`/inspirations/${id}`, {
    rating,
  })
  return data
}

/** 彻底删除灵感（物理删除，不可恢复；用于垃圾桶「彻底删除」等场景） */
export async function deleteInspiration(id: string) {
  await apiClient.delete(`/inspirations/${id}`)
}

/** 移入垃圾桶（软删除，可恢复；reason 为空时后端按素材状态自动推断；source 默认手动） */
export async function moveToTrash(
  id: string,
  reason?: TrashReason,
  source: TrashSource = 'manual',
) {
  const { data } = await apiClient.post<InspirationOut>(
    `/inspirations/${id}/trash`,
    reason || source !== 'manual' ? { reason, source } : {},
  )
  return data
}

/** 从垃圾桶恢复素材 */
export async function restoreInspiration(id: string) {
  const { data } = await apiClient.post<InspirationOut>(`/inspirations/${id}/restore`)
  return data
}

/** 获取垃圾桶素材列表 */
export async function fetchTrash(
  params: {
    page?: number
    size?: number
    reason?: TrashReason
  } = {},
) {
  const { data } = await apiClient.get<InspirationListOut>('/inspirations/trash', { params })
  return data
}

/** 清空垃圾桶（onlyExpired=true 仅清理超过保留期的过期素材） */
export async function emptyTrash(onlyExpired = false) {
  const { data } = await apiClient.delete<{
    deleted: number
    freed_bytes: number
    message?: string
  }>('/inspirations/trash', { params: { only_expired: onlyExpired } })
  return data
}

/** 触发 AI 分析（用于手动重新分析） */
export async function analyzeInspiration(id: string) {
  const { data } = await apiClient.post(`/ai/analyze/${id}`)
  return data
}

// ── 人脸检测与博主匹配 ──

/** 素材人脸检测结果（含匹配博主/模特） */
export interface FaceDetectionOut {
  id: number
  face_index: number
  /** 人脸检测置信度（0~1；低于阈值的人脸不自动匹配） */
  det_score: number | null
  matched_blogger_id: number | null
  matched_blogger_name?: string | null
  matched_model_id: number | null
  matched_model_name?: string | null
  confidence: number | null
  created_at?: string | null
}

export interface FaceDetectionsOut {
  inspiration_id: string
  face_count: number
  detections: FaceDetectionOut[]
}

/** 触发素材人脸检测与博主特征库匹配（重新检测覆盖旧结果） */
export async function faceDetectInspiration(id: string): Promise<FaceDetectionsOut> {
  const { data } = await apiClient.post<FaceDetectionsOut>(`/inspirations/${id}/face-detect`)
  // 校验检测条目关键字段（此前检测接口缺 matched_blogger_name 导致前端显示「博主 #id」）
  warnItems(
    data.detections,
    {
      id: 'number',
      face_index: 'number',
      matched_blogger_id: 'number?',
      matched_blogger_name: 'string?',
      matched_model_id: 'number?',
      matched_model_name: 'string?',
      confidence: 'number?',
    },
    'face-detect detections',
  )
  return data
}

/** 查询素材人脸检测结果 */
export async function fetchFaceDetections(id: string): Promise<FaceDetectionsOut> {
  const { data } = await apiClient.get<FaceDetectionsOut>(`/inspirations/${id}/face-detections`)
  return data
}

/** 手动指定/解除人脸检测的人物关联（personId 传 null 即解除；默认博主，兼容旧调用） */
export async function updateFaceDetection(
  id: string,
  detectionId: number,
  personId: number | null,
  personType: 'blogger' | 'model' = 'blogger',
): Promise<{
  updated: boolean
  detection_id: number
  matched_blogger_id: number | null
  matched_model_id: number | null
}> {
  const { data } = await apiClient.put(`/inspirations/${id}/face-detections/${detectionId}`, {
    person_type: personId === null ? undefined : personType,
    person_id: personId,
  })
  return data
}

/** 删除单条人脸检测记录 */
export async function deleteFaceDetection(id: string, detectionId: number) {
  const { data } = await apiClient.delete(`/inspirations/${id}/face-detections/${detectionId}`)
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

/** 批量将全部已拒绝素材移入垃圾桶（软删除，可恢复） */
export async function deleteRejectedInspirations() {
  const { data } = await apiClient.delete<{ trashed: number; message?: string }>(
    '/inspirations/quality-rejected',
  )
  return data
}
