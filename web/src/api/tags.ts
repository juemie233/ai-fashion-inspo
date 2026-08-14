/** 标签相关 API 调用。 */

import apiClient from './client'

export interface TagCategoryGroup {
  category: string
  tags: TagItem[]
}

export interface TagItem {
  id: number
  name: string
  category: string
  source: string  // seed | ai_generated | manual
  pinned: boolean
  sort_order: number
  description: string | null
  usage_count: number
}

/** 类别名称的中文映射 */
export const CATEGORY_LABELS: Record<string, string> = {
  style: '风格',
  item_type: '单品',
  color: '颜色',
  body_part: '穿着方式',
  fit: '版型',
  attribute: '属性',
  free: '自定义',
  outfit: '穿搭大标签',
}

/** 来源的中文映射 */
export const SOURCE_LABELS: Record<string, string> = {
  seed: '预设',
  ai_generated: 'AI生成',
  manual: '手动',
}

// ===== 基础 CRUD =====

/** 获取所有标签（按类别分组） */
export async function fetchTagsGrouped() {
  const { data } = await apiClient.get<TagCategoryGroup[]>('/tags')
  return data
}

/** 创建自定义标签 */
export async function createTag(name: string, category: string = 'free') {
  const { data } = await apiClient.post('/tags', { name, category })
  return data
}

/** 更新标签（重命名 / 改类别 / 置顶 / 排序 / 备注） */
export async function updateTag(
  tagId: number,
  body: { name?: string; category?: string; pinned?: boolean; sort_order?: number; description?: string | null },
) {
  const { data } = await apiClient.patch(`/tags/${tagId}`, body)
  return data
}

/** 批量更新标签自定义排序 */
export async function reorderTags(items: Array<{ id: number; sort_order: number }>) {
  const { data } = await apiClient.post('/tags/reorder', { items })
  return data
}

/** 合并标签 */
export async function mergeTags(sourceTagId: number, targetTagId: number) {
  const { data } = await apiClient.post('/tags/merge', {
    source_tag_id: sourceTagId,
    target_tag_id: targetTagId,
  })
  return data
}

/** 查找相似标签建议 */
export async function getSimilarSuggestions(name: string) {
  const { data } = await apiClient.get(`/tags/suggestions/${encodeURIComponent(name)}`)
  return data
}

// ===== 批量操作 =====

/** 批量删除标签 */
export async function batchDeleteTags(tagIds: number[]) {
  const { data } = await apiClient.post('/tags/batch-delete', { tag_ids: tagIds })
  return data
}

/** 删除所有未使用标签 */
export async function deleteUnusedTags() {
  const { data } = await apiClient.delete('/tags/unused')
  return data
}

// ===== 统计 =====

export interface TagStats {
  total: number
  unused: number
  total_links: number
  by_source: Record<string, number>
  by_category: Record<string, number>
}

/** 获取标签统计 */
export async function fetchTagStats(): Promise<TagStats> {
  const { data } = await apiClient.get('/tags/stats')
  return data
}

// ===== 重复扫描 =====

export interface DuplicatePair {
  tag_a: { id: number; name: string; category: string }
  tag_b: { id: number; name: string; category: string }
  similarity: number
}

/** 扫描重复/相似标签 */
export async function findDuplicates(threshold: number = 0.75) {
  const { data } = await apiClient.get<{ duplicates: DuplicatePair[]; total: number }>(
    '/tags/duplicates', { params: { threshold } }
  )
  return data
}

// ===== 标签详情 =====

export interface TagInspiration {
  inspiration_id: string
  file_path: string
  thumbnail_path: string | null
  media_type: string
  confidence: number
  created_at: string | null
}

/** 获取使用某标签的素材 */
export async function fetchTagInspirations(tagId: number, page: number = 1, size: number = 20, sort?: string) {
  const { data } = await apiClient.get(`/tags/${tagId}/inspirations`, { params: { page, size, sort } })
  return data
}

/** 批量解除标签与多个素材的关联 */
export async function batchRemoveTagInspirations(tagId: number, inspirationIds: string[]) {
  const { data } = await apiClient.post<{ removed: number }>(
    `/tags/${tagId}/inspirations/batch-remove`,
    { inspiration_ids: inspirationIds },
  )
  return data
}

// ===== 导入/导出 =====

/** 导出所有标签 */
export async function exportTags() {
  const { data } = await apiClient.get('/tags/export')
  return data
}

/** 导入标签 */
export async function importTags(tags: Array<{ name: string; category: string }>) {
  const { data } = await apiClient.post('/tags/import', { tags })
  return data
}

// ===== 别名管理 =====

export interface TagAlias {
  id: number
  tag_id: number
  alias: string
  tag_name?: string
}

/** 获取所有标签别名 */
export async function fetchAliases(): Promise<TagAlias[]> {
  const { data } = await apiClient.get('/tags/aliases')
  return data
}

/** 为标签添加别名 */
export async function createAlias(tagId: number, alias: string) {
  const { data } = await apiClient.post(`/tags/${tagId}/aliases`, { alias })
  return data
}

/** 删除别名 */
export async function deleteAlias(aliasId: number) {
  const { data } = await apiClient.delete(`/tags/aliases/${aliasId}`)
  return data
}

// ===== 共现网络与使用趋势 =====

export interface CooccurrenceNode {
  id: number
  name: string
  category: string
  usage_count: number
}

export interface CooccurrenceEdge {
  source: number
  target: number
  weight: number
}

export interface CooccurrenceNetwork {
  nodes: CooccurrenceNode[]
  edges: CooccurrenceEdge[]
}

/** 获取标签共现网络 */
export async function fetchCooccurrenceNetwork(limit: number = 30, minCount: number = 1) {
  const { data } = await apiClient.get<CooccurrenceNetwork>('/tags/cooccurrence-network', {
    params: { limit, min_count: minCount },
  })
  return data
}

export interface TopTag {
  id: number
  name: string
  category: string
  usage_count: number
}

/** 获取热门标签排行 */
export async function fetchTopTags(limit: number = 20): Promise<TopTag[]> {
  const { data } = await apiClient.get('/tags/top', { params: { limit } })
  return data
}

export interface TagTrendPoint {
  bucket: string
  count: number
}

/** 获取标签使用趋势 */
export async function fetchTagTrend(tagId: number, granularity: string = 'month') {
  const { data } = await apiClient.get<{
    tag: { id: number; name: string }
    granularity: string
    trend: TagTrendPoint[]
  }>(`/tags/${tagId}/trend`, { params: { granularity } })
  return data
}
