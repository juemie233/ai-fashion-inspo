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
  season: '季节',
  attribute: '属性',
  free: '自定义',
}

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
