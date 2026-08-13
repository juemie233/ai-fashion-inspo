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
  usage_count: number
}

/** 类别名称的中文映射 */
export const CATEGORY_LABELS: Record<string, string> = {
  style: '风格',
  item_type: '单品',
  color: '颜色',
  body_part: '穿着方式',
  fit: '版型',
  occasion: '场合',
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

/** 获取热门标签 */
export async function fetchPopularTags() {
  const { data } = await apiClient.get<TagItem[]>('/tags/popular')
  return data
}

/** 创建自定义标签 */
export async function createTag(name: string, category: string = 'free') {
  const { data } = await apiClient.post('/tags', { name, category })
  return data
}

/** 更新标签（重命名 / 改类别） */
export async function updateTag(tagId: number, body: { name?: string; category?: string }) {
  const { data } = await apiClient.patch(`/tags/${tagId}`, body)
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
