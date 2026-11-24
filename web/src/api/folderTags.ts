/** 抖音收藏夹 → 穿搭大标签映射 API（2026-11-24）。 */

import apiClient from './client'

export type FolderTagMode = 'tag' | 'skip' | 'pending'

export interface FolderTagMapping {
  folder_name: string
  mode: FolderTagMode
  /** 首次同步按同名自动建标签后为 true，等用户确认 */
  needs_confirm: boolean
  tag_id: number | null
  tag_name: string | null
  tag_category: string | null
  local_collection_id: number | null
  local_item_count: number | null
}

export interface FolderTagUpsertPayload {
  folder_name: string
  mode: FolderTagMode
  tag_id?: number | null
  new_tag_name?: string | null
  /** 同名标签已属其它类别时，显式同意改为「穿搭大标签」 */
  reuse_existing_category?: boolean
}

export interface FolderTagApplyResult {
  applied: number
  folders: Array<Record<string, unknown>>
}

export interface FolderTagImpact {
  tag_id: number
  name: string
  category: string
  usage_count: number
  mapped_folders: string[]
}

export async function fetchFolderTagMappings(): Promise<FolderTagMapping[]> {
  const { data } = await apiClient.get<FolderTagMapping[]>('/admin/douyin-folder-tags')
  return data
}

export async function upsertFolderTagMapping(
  payload: FolderTagUpsertPayload,
): Promise<FolderTagMapping> {
  const { data } = await apiClient.post<FolderTagMapping>('/admin/douyin-folder-tags', payload)
  return data
}

export async function deleteFolderTagMapping(folderName: string): Promise<void> {
  await apiClient.delete(`/admin/douyin-folder-tags/${encodeURIComponent(folderName)}`)
}

export async function applyFolderTagMapping(folderName: string): Promise<FolderTagApplyResult> {
  const { data } = await apiClient.post<FolderTagApplyResult>(
    `/admin/douyin-folder-tags/${encodeURIComponent(folderName)}/apply`,
  )
  return data
}

export async function applyAllFolderTags(): Promise<FolderTagApplyResult> {
  const { data } = await apiClient.post<FolderTagApplyResult>('/admin/douyin-folder-tags/apply-all')
  return data
}

export async function fetchFolderTagImpact(tagId: number): Promise<FolderTagImpact> {
  const { data } = await apiClient.get<FolderTagImpact>(
    `/admin/douyin-folder-tags/tag-impact/${tagId}`,
  )
  return data
}
