/** 人物相关 API 调用（穿搭博主 / 职业模特已拆分两表，工厂生成两组端点）。 */

import apiClient from './client'
import type {
  Blogger,
  Model,
  ModelPhoto,
  ModelPhotoSet,
  ModelPhotoSetDetail,
  ModelPhotoSetListOut,
  PersonBrief,
  PersonDetail,
  PersonImportResult,
  PersonListOut,
  PersonPlatform,
} from '@shared/types/person'

export type {
  Blogger,
  Model,
  PersonBrief,
  PersonDetail,
  PersonImportResult,
  PersonListOut,
  PersonPlatform,
  ModelPhoto,
  ModelPhotoSet,
  ModelPhotoSetDetail,
  ModelPhotoSetListOut,
} from '@shared/types/person'

/** 人物表单载荷（创建/更新共用，更新时字段均可选） */
export interface PersonForm {
  name?: string
  platform?: PersonPlatform
  platform_user_id?: string | null
  xhs_id?: string | null
  ip_location?: string | null
  profile_url?: string | null
  avatar_path?: string | null
  bio?: string | null
}

/** 人物素材项（与标签详情素材结构一致） */
export interface PersonInspiration {
  inspiration_id: string
  file_path: string
  thumbnail_path: string | null
  media_type: string
  confidence: number
  created_at: string | null
}

/** 人物素材列表响应 */
export interface PersonInspirationsOut {
  person: { id: number; name: string; platform: string }
  items: PersonInspiration[]
  total: number
  page: number
  size: number
}

/** IP 属地统计响应（人物管理页地域分布） */
export interface PersonIpStats {
  total: number
  items: Array<{ ip_location: string; count: number }>
}

/**
 * 按类型生成人物 API（博主 / 模特共用 CRUD，端点路径按 kind 区分）。
 * 照片组端点仅由模特 API 暴露（博主无写真照片组能力）。
 */
function createPersonApi(kind: 'bloggers' | 'models') {
  const base = `/${kind}`

  return {
    /** 获取人物列表（分页 + 搜索 + 平台筛选） */
    async fetchList(params: {
      page?: number
      size?: number
      search?: string
      platform?: string
      sort?: 'newest' | 'name' | 'count'
    } = {}): Promise<PersonListOut> {
      const { data } = await apiClient.get<PersonListOut>(base, { params })
      return data
    },

    /** 获取人物详情（含素材数与风格画像） */
    async fetchDetail(id: number): Promise<PersonDetail> {
      const { data } = await apiClient.get<PersonDetail>(`${base}/${id}`)
      return data
    },

    /** 创建人物 */
    async create(body: PersonForm) {
      const { data } = await apiClient.post<Blogger | Model>(base, body)
      return data
    },

    /** 更新人物（部分更新） */
    async update(id: number, body: PersonForm) {
      const { data } = await apiClient.patch<Blogger | Model>(`${base}/${id}`, body)
      return data
    },

    /** 删除人物（仅当其无关联素材时允许；有关联素材时后端返回 400 与提示） */
    async remove(id: number) {
      await apiClient.delete(`${base}/${id}`)
    },

    /** 获取人物的素材列表 */
    async fetchInspirations(
      id: number,
      page: number = 1,
      size: number = 20,
      sort?: string,
    ): Promise<PersonInspirationsOut> {
      const { data } = await apiClient.get<PersonInspirationsOut>(
        `${base}/${id}/inspirations`,
        { params: { page, size, sort } },
      )
      return data
    },

    /** 获取热门人物排行（按素材数倒序） */
    async fetchTop(limit: number = 20): Promise<PersonListOut['items']> {
      const { data } = await apiClient.get<PersonListOut['items']>(`${base}/top`, {
        params: { limit },
      })
      return data
    },

    /** 按 IP 属地分组统计（空属地归「未知」），供人物管理页展示地域分布 */
    async fetchIpStats(limit: number = 30): Promise<PersonIpStats> {
      const { data } = await apiClient.get<PersonIpStats>(`${base}/ip-stats`, {
        params: { limit },
      })
      return data
    },

    /** 按名称模糊匹配人物（用于选择去重） */
    async suggest(name: string): Promise<PersonListOut['items']> {
      const { data } = await apiClient.get<PersonListOut['items']>(`${base}/suggestions`, {
        params: { name },
      })
      return data
    },

    /** 给素材批量关联人物（幂等） */
    async link(inspirationId: string, personIds: number[]) {
      const { data } = await apiClient.post<{ added: PersonBrief[]; count: number }>(
        `/inspirations/${inspirationId}/${kind}`,
        { person_ids: personIds },
      )
      return data
    },

    /** 解除素材与人物关联 */
    async unlink(inspirationId: string, personId: number) {
      const { data } = await apiClient.delete<{ removed: number }>(
        `/inspirations/${inspirationId}/${kind}/${personId}`,
      )
      return data
    },
  }
}

/** 穿搭博主 API（/api/bloggers）：含人脸特征注册 */
export const bloggersApi = {
  ...createPersonApi('bloggers'),

  /** 注册/重新注册博主人脸：上传照片与/或已关联素材（合计 1~5 张，重复注册覆盖） */
  async registerFace(
    id: number,
    files: File[],
    inspirationIds?: string[],
  ): Promise<BloggerFaceRegisterResult> {
    const formData = new FormData()
    files.forEach((f) => formData.append('files', f))
    if (inspirationIds && inspirationIds.length > 0) {
      formData.append('inspiration_ids', JSON.stringify(inspirationIds))
    }
    // 显式 multipart：全局默认 application/json 会让 axios 把 FormData 序列化成 JSON（后端 422）
    const { data } = await apiClient.post<BloggerFaceRegisterResult>(`/bloggers/${id}/face`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    return data
  },

  /** 查询博主人脸注册状态 */
  async fetchFaceStatus(id: number): Promise<BloggerFaceStatus> {
    const { data } = await apiClient.get<BloggerFaceStatus>(`/bloggers/${id}/face`)
    return data
  },
}

/** 博主人脸注册状态 */
export interface BloggerFaceStatus {
  registered: boolean
  blogger_id: number
  updated_at?: string | null
}

/** 单张图片的注册结果明细（部分跳过时前端逐张提示原因） */
export interface FacePhotoResult {
  index: number
  /** 来源：上传照片 / 已关联素材 */
  source?: 'upload' | 'inspiration'
  status: 'used' | 'skipped'
  reason?: 'no_face' | 'low_confidence' | 'small_face' | null
  message?: string | null
  det_score?: number | null
  face_ratio?: number | null
}

/** 博主人脸注册结果 */
export interface BloggerFaceRegisterResult extends BloggerFaceStatus {
  blogger_name?: string
  photos_used?: number
  photos_total?: number
  /** 素材来源跳过警告（文件缺失/不属于该博主等） */
  warnings?: string[]
  photo_results?: FacePhotoResult[]
}

/** 职业模特 API（/api/models）：不含人脸能力，仅通用人物能力 */
export const modelsApi = {
  ...createPersonApi('models'),
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

// ── 模特照片组（写真，与穿搭素材分离；仅模特拥有）──

/** 获取模特照片组分页列表 */
export async function fetchModelPhotoSets(
  modelId: number,
  page: number = 1,
  size: number = 50,
): Promise<ModelPhotoSetListOut> {
  const { data } = await apiClient.get<ModelPhotoSetListOut>(
    `/models/${modelId}/photo-sets`,
    { params: { page, size } },
  )
  return data
}

/** 创建模特照片组（name 可留空，后端回退「未命名照片组」） */
export async function createModelPhotoSet(
  modelId: number,
  name?: string | null,
): Promise<ModelPhotoSet> {
  const { data } = await apiClient.post<ModelPhotoSet>(
    `/models/${modelId}/photo-sets`,
    { name: name || null },
  )
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

/** 更新照片组名称 */
export async function updateModelPhotoSet(
  modelId: number,
  setId: number,
  name: string,
): Promise<ModelPhotoSet> {
  const { data } = await apiClient.patch<ModelPhotoSet>(
    `/models/${modelId}/photo-sets/${setId}`,
    { name },
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
  onProgress?: (e: any) => void,
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

/** 删除照片组内的单张照片 */
export async function deleteModelPhoto(
  modelId: number,
  setId: number,
  photoId: number,
) {
  const { data } = await apiClient.delete<{ removed: number }>(
    `/models/${modelId}/photo-sets/${setId}/photos/${photoId}`,
  )
  return data
}
