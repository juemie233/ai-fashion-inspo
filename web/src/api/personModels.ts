/** 模特与照片组的接口（从 api/persons.ts 尾部整段拆出，C-5）。
 *
 * 只做搬运：函数体、请求路径、返回类型均未改动。`api/persons.ts` 仍重导出这些符号，
 * 既有 `from '@/api/persons'` 的调用点无需修改。
 */

import apiClient from './client'
import { createPersonApi } from './personBase'

import type { AxiosProgressEvent } from 'axios'
import type {
  ModelPhoto,
  ModelPhotoSet,
  ModelPhotoSetDetail,
  ModelPhotoSetListOut,
  PersonImportResult,
} from './persons'

export const modelsApi = {
  ...createPersonApi('models'),

  /** 从写真照片组注册/重新注册模特人脸特征（Top-K 平均池化，默认 5 张） */
  async registerFace(id: number, topK: number = 5): Promise<ModelFaceRegisterResult> {
    const formData = new FormData()
    formData.append('top_k', String(topK))
    const { data } = await apiClient.post<ModelFaceRegisterResult>(`/models/${id}/face`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    return data
  },

  /** 查询模特人脸特征注册状态 */
  async fetchFaceStatus(id: number): Promise<ModelFaceStatus> {
    const { data } = await apiClient.get<ModelFaceStatus>(`/models/${id}/face`)
    return data
  },
}

/** 模特人脸特征注册状态 */
export interface ModelFaceStatus {
  registered: boolean
  model_id: number
  updated_at?: string | null
}

/** 模特人脸注册结果（照片组 Top-K 平均池化） */
export interface ModelFaceRegisterResult {
  registered: boolean
  model_id: number
  model_name?: string
  /** 实际使用的照片数（= min(合格人脸数, top_k)） */
  photos_used?: number
  photos_total?: number
  /** 通过质量过滤的人脸总数 */
  qualified?: number
  warnings?: string[]
  updated_at?: string | null
}

/** 上传 CSV 批量导入博主（按 xhs_id upsert，昵称/小红书号必填） */
export async function importBloggersCsv(file: File) {
  const formData = new FormData()
  formData.append('file', file)
  const { data } = await apiClient.post<PersonImportResult>('/bloggers/import-csv', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return data
}

// ── 博主主页信息补全（人物管理页一键补全）──

/** 缺失主页信息的小红书博主（profile_url 或 platform_user_id 为空） */
export interface MissingProfileBlogger {
  id: number
  name: string
  /** 平台：xiaohongshu=缺主页链接/ID（在线搜索补）；douyin=缺 IP 属地（离线读 f2 库补） */
  platform: string
  xhs_id: string | null
  /** 平台用户 ID：抖音这一列就是 sec_user_id，缺了就没法查 IP 属地 */
  platform_user_id: string | null
}

/** 查询资料有缺口的博主列表（小红书缺主页信息 / 抖音缺 IP 属地） */
export async function fetchMissingProfiles(): Promise<{
  items: MissingProfileBlogger[]
  total: number
}> {
  const { data } = await apiClient.get<{ items: MissingProfileBlogger[]; total: number }>(
    '/bloggers/missing-profile',
  )
  return data
}

/** 创建博主主页补全任务（bloggerIds 缺省 = 全部缺失博主） */
export async function enrichMissingProfiles(bloggerIds?: number[]): Promise<{
  task_id: number
  total: number
  message: string
}> {
  const { data } = await apiClient.post('/bloggers/enrich-missing-profile', {
    blogger_ids: bloggerIds,
  })
  return data
}

/** 已跳过补全的博主 */
export interface EnrichSkipItem {
  blogger_id: number
  name: string
  reason: string
  created_at: string
}

/** 查询已跳过补全的博主 */
export async function fetchEnrichSkips(): Promise<{ items: EnrichSkipItem[]; total: number }> {
  const { data } = await apiClient.get<{ items: EnrichSkipItem[]; total: number }>(
    '/bloggers/enrich-skips',
  )
  return data
}

/** 手动跳过补全（确定性无法获取信息的博主，从缺失列表移除） */
export async function skipEnrichBloggers(
  bloggerIds: number[],
  reason?: string,
): Promise<{ skipped: number }> {
  const { data } = await apiClient.post('/bloggers/enrich-skip', {
    blogger_ids: bloggerIds,
    reason,
  })
  return data
}

/** 解除跳过（重新纳入补全范围） */
export async function unskipEnrichBloggers(bloggerIds: number[]): Promise<{ unskipped: number }> {
  const { data } = await apiClient.post('/bloggers/enrich-unskip', {
    blogger_ids: bloggerIds,
  })
  return data
}

// ── 模特照片组（写真，与穿搭素材分离；仅模特拥有）──

/** 获取模特照片组分页列表 */
export async function fetchModelPhotoSets(
  modelId: number,
  page: number = 1,
  size: number = 50,
): Promise<ModelPhotoSetListOut> {
  const { data } = await apiClient.get<ModelPhotoSetListOut>(`/models/${modelId}/photo-sets`, {
    params: { page, size },
  })
  return data
}

/** 创建模特照片组（name 可留空，后端回退「未命名照片组」） */
export async function createModelPhotoSet(
  modelId: number,
  name?: string | null,
): Promise<ModelPhotoSet> {
  const { data } = await apiClient.post<ModelPhotoSet>(`/models/${modelId}/photo-sets`, {
    name: name || null,
  })
  return data
}

/** 获取照片组详情（含分页照片列表） */
export async function fetchModelPhotoSet(
  modelId: number,
  setId: number,
  page: number = 1,
  size: number = 200,
): Promise<ModelPhotoSetDetail> {
  const { data } = await apiClient.get<ModelPhotoSetDetail>(
    `/models/${modelId}/photo-sets/${setId}`,
    { params: { page, size } },
  )
  return data
}

/** 删除照片组（级联删除照片与文件） */
export async function deleteModelPhotoSet(modelId: number, setId: number) {
  await apiClient.delete(`/models/${modelId}/photo-sets/${setId}`)
}

/** 上传一张照片到照片组（支持进度回调与取消信号） */
export async function uploadModelPhoto(
  modelId: number,
  setId: number,
  file: File,
  sortOrder: number,
  onProgress?: (e: AxiosProgressEvent) => void,
  signal?: AbortSignal,
): Promise<ModelPhoto> {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('sort_order', String(sortOrder))
  const { data } = await apiClient.post<ModelPhoto>(
    `/models/${modelId}/photo-sets/${setId}/photos`,
    formData,
    {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: onProgress,
      signal,
    },
  )
  return data
}
