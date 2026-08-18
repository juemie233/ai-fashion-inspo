/** 人物相关 API 调用。 */

import apiClient from './client'
import type {
  Person,
  PersonBrief,
  PersonDetail,
  PersonImportResult,
  PersonListOut,
  PersonPlatform,
  PersonPhoto,
  PersonPhotoSet,
  PersonPhotoSetDetail,
  PersonPhotoSetListOut,
  PersonType,
} from '@shared/types/person'

export type {
  Person,
  PersonBrief,
  PersonDetail,
  PersonImportResult,
  PersonStyleProfile,
  PersonPlatform,
  PersonPhoto,
  PersonPhotoSet,
  PersonPhotoSetDetail,
  PersonPhotoSetListOut,
  PersonType,
} from '@shared/types/person'

/** 人物表单载荷（创建/更新共用，更新时字段均可选） */
export interface PersonForm {
  name?: string
  person_type?: PersonType
  platform?: PersonPlatform
  platform_user_id?: string | null
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
  person: { id: number; name: string; person_type: PersonType; platform: string }
  items: PersonInspiration[]
  total: number
  page: number
  size: number
}

/** 获取人物列表（分页 + 搜索 + 内容类型/平台筛选） */
export async function fetchPersons(params: {
  page?: number
  size?: number
  search?: string
  person_type?: PersonType | ''
  platform?: string
  sort?: 'newest' | 'name' | 'count'
} = {}) {
  const { data } = await apiClient.get<PersonListOut>('/persons', { params })
  return data
}

/** 获取人物详情（含素材数与风格画像） */
export async function fetchPerson(id: number): Promise<PersonDetail> {
  const { data } = await apiClient.get<PersonDetail>(`/persons/${id}`)
  return data
}

/** 创建人物 */
export async function createPerson(body: PersonForm) {
  const { data } = await apiClient.post<Person>('/persons', body)
  return data
}

/** 上传 CSV 批量导入人物（按 xhs_id upsert，昵称/小红书号必填） */
export async function importPersonsCsv(file: File) {
  const formData = new FormData()
  formData.append('file', file)
  const { data } = await apiClient.post<PersonImportResult>(
    '/persons/import-csv',
    formData,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  )
  return data
}

/** 更新人物（部分更新） */
export async function updatePerson(id: number, body: PersonForm) {
  const { data } = await apiClient.patch<Person>(`/persons/${id}`, body)
  return data
}

/** 删除人物（仅当其无关联素材时允许；有关联素材时后端返回 400 与提示） */
export async function deletePerson(id: number) {
  await apiClient.delete(`/persons/${id}`)
}

/** 获取人物的素材列表 */
export async function fetchPersonInspirations(
  id: number,
  page: number = 1,
  size: number = 20,
  sort?: string,
) {
  const { data } = await apiClient.get<PersonInspirationsOut>(
    `/persons/${id}/inspirations`,
    { params: { page, size, sort } },
  )
  return data
}

/** 获取热门人物排行（按素材数倒序） */
export async function fetchTopPersons(limit: number = 20): Promise<Person[]> {
  const { data } = await apiClient.get<Person[]>('/persons/top', { params: { limit } })
  return data
}

/** 按名称模糊匹配人物（用于选择去重） */
export async function suggestPersons(name: string): Promise<Person[]> {
  const { data } = await apiClient.get<Person[]>('/persons/suggestions', {
    params: { name },
  })
  return data
}

/** 给素材批量关联人物（幂等） */
export async function linkPerson(inspirationId: string, personIds: number[]) {
  const { data } = await apiClient.post<{ added: PersonBrief[]; count: number }>(
    `/inspirations/${inspirationId}/persons`,
    { person_ids: personIds },
  )
  return data
}

/** 解除素材与人物关联 */
export async function unlinkPerson(inspirationId: string, personId: number) {
  const { data } = await apiClient.delete<{ removed: number }>(
    `/inspirations/${inspirationId}/persons/${personId}`,
  )
  return data
}

// ── 人物照片组（模特写真，与穿搭素材分离）──

/** 获取人物照片组分页列表 */
export async function fetchPersonPhotoSets(
  personId: number,
  page: number = 1,
  size: number = 50,
): Promise<PersonPhotoSetListOut> {
  const { data } = await apiClient.get<PersonPhotoSetListOut>(
    `/persons/${personId}/photo-sets`,
    { params: { page, size } },
  )
  return data
}

/** 创建人物照片组（name 可留空，后端回退「未命名照片组」） */
export async function createPersonPhotoSet(
  personId: number,
  name?: string | null,
): Promise<PersonPhotoSet> {
  const { data } = await apiClient.post<PersonPhotoSet>(
    `/persons/${personId}/photo-sets`,
    { name: name || null },
  )
  return data
}

/** 获取照片组详情（含分页照片列表） */
export async function fetchPersonPhotoSet(
  personId: number,
  setId: number,
  page: number = 1,
  size: number = 200,
): Promise<PersonPhotoSetDetail> {
  const { data } = await apiClient.get<PersonPhotoSetDetail>(
    `/persons/${personId}/photo-sets/${setId}`,
    { params: { page, size } },
  )
  return data
}

/** 更新照片组名称 */
export async function updatePersonPhotoSet(
  personId: number,
  setId: number,
  name: string,
): Promise<PersonPhotoSet> {
  const { data } = await apiClient.patch<PersonPhotoSet>(
    `/persons/${personId}/photo-sets/${setId}`,
    { name },
  )
  return data
}

/** 删除照片组（级联删除照片与文件） */
export async function deletePersonPhotoSet(personId: number, setId: number) {
  await apiClient.delete(`/persons/${personId}/photo-sets/${setId}`)
}

/** 上传一张照片到照片组（支持进度回调与取消信号） */
export async function uploadPersonPhoto(
  personId: number,
  setId: number,
  file: File,
  sortOrder: number,
  onProgress?: (e: any) => void,
  signal?: AbortSignal,
): Promise<PersonPhoto> {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('sort_order', String(sortOrder))
  const { data } = await apiClient.post<PersonPhoto>(
    `/persons/${personId}/photo-sets/${setId}/photos`,
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
export async function deletePersonPhoto(
  personId: number,
  setId: number,
  photoId: number,
) {
  const { data } = await apiClient.delete<{ removed: number }>(
    `/persons/${personId}/photo-sets/${setId}/photos/${photoId}`,
  )
  return data
}
