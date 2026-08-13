/** 搜索相关 API 调用。 */

import apiClient from './client'
import type { InspirationListOut, InspirationOut } from './inspirations'

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

// ===== 向量检索（语义搜索 / 以图搜图 / 相似推荐） =====

/** 向量搜索结果单项 */
export interface VectorSearchItem {
  inspiration: InspirationOut
  score: number
}

/** 向量搜索响应 */
export interface VectorSearchOut {
  query_type: 'text' | 'image'
  query_text?: string | null
  items: VectorSearchItem[]
  total: number
}

/** 相似素材推荐单项 */
export interface SimilarItemOut {
  inspiration: InspirationOut
  similarity: number
  shared_tags: number
  match_source: 'visual' | 'tag' | 'hybrid'
}

/** 相似素材推荐响应 */
export interface SimilarOut {
  source: InspirationOut
  similar: SimilarItemOut[]
}

/** 向量检索能力状态 */
export interface VectorStatusOut {
  lancedb_available: boolean
  lancedb_dir: string
  text_embedding: {
    model: string
    dim: number
    note: string
  }
  image_embedding: {
    available: boolean
    model: string
    dim: number
    reason: string
  }
  text_vector_count: number
  image_vector_count: number
}

/** 语义搜索（文本） */
export async function vectorSearchText(text: string, topK = 20) {
  const form = new FormData()
  form.append('text', text)
  form.append('top_k', String(topK))
  const { data } = await apiClient.post<VectorSearchOut>('/search/vector', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return data
}

/** 以图搜图（图片上传） */
export async function vectorSearchImage(file: File, topK = 20) {
  const form = new FormData()
  form.append('file', file)
  form.append('top_k', String(topK))
  const { data } = await apiClient.post<VectorSearchOut>('/search/vector', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return data
}

/** 查询向量检索能力状态 */
export async function fetchVectorStatus() {
  const { data } = await apiClient.get<VectorStatusOut>('/search/vector/status')
  return data
}

/** 触发存量素材向量回填 */
export async function backfillVectors(mode = 'all', limit = 0) {
  const form = new FormData()
  form.append('mode', mode)
  form.append('limit', String(limit))
  const { data } = await apiClient.post('/search/vector/backfill', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return data as {
    processed: number
    text_added: number
    text_failed: number
    image_added: number
    image_failed: number
    skipped_non_image: number
  }
}

/** 相似素材推荐（视觉 + 标签加权） */
export async function fetchSimilar(id: string, topK = 10) {
  const { data } = await apiClient.get<SimilarOut>(`/search/similar/${id}`, {
    params: { top_k: topK },
  })
  return data
}
