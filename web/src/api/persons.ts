/** 人物相关 API 调用。 */

import apiClient from './client'
import type { Person, PersonBrief, PersonDetail, PersonListOut, PersonPlatform, PersonType } from '@shared/types/person'

export type { Person, PersonBrief, PersonDetail, PersonStyleProfile, PersonPlatform, PersonType } from '@shared/types/person'

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

/** 更新人物（部分更新） */
export async function updatePerson(id: number, body: PersonForm) {
  const { data } = await apiClient.patch<Person>(`/persons/${id}`, body)
  return data
}

/** 删除人物（关联素材不受影响，仅解除人物关联） */
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
