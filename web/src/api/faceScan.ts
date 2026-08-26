/** 人脸库扫描 API 客户端（扫描任务 / 候选匹配 / 结果查询 / 审核确认）。 */

import apiClient from './client'

/** 扫描/匹配任务状态（供轮询） */
export interface FaceScanTaskOut {
  id: number
  type: string
  status: string // pending/running/success/failed/cancelled
  progress: number
  total: number
  done: number
  result: Record<string, unknown> | null
  error: string | null
}

/** 最近一次扫描与匹配任务 */
export interface FaceScanTaskStatus {
  scan_task: FaceScanTaskOut | null
  match_task: FaceScanTaskOut | null
}

/** 按人物聚合项（候选/已确认） */
export interface PersonAggregateItem {
  person_type: 'blogger' | 'model'
  person_id: number
  name: string
  count: number
  best_conf: number | null
}

/** 单条命中/未匹配明细 */
export interface DetectionItem {
  detection_id: number
  inspiration_id: string
  confidence: number | null
  file_path: string
  thumbnail_path: string | null
}

/** 结果查询响应（模式区分聚合/明细/未匹配） */
export interface FaceScanResults {
  mode: 'persons' | 'detail' | 'unmatched'
  items: Array<PersonAggregateItem | DetectionItem>
  total: number
  page: number
  size: number
}

/** 审核操作结果统计 */
export interface ConfirmResult {
  action: string
  confirmed: number
  rejected: number
  undone: number
  skipped: number
}

/** 人脸聚合分组列表项（未匹配人脸按疑似同一人聚类） */
export interface FaceClusterGroup {
  group_id: number
  size: number
  detection_ids: number[]
  rep_detection_id: number | null
  rep_inspiration_id: string | null
  rep_file_path: string | null
  rep_thumbnail_path: string | null
}

/** 人脸聚合分组查询响应 */
export interface FaceClusterGroups {
  task_status: string | null
  items: FaceClusterGroup[]
  total: number
  page: number
  size: number
  summary: {
    total_faces?: number | null
    method?: string | null
    group_count?: number | null
    clustered_faces?: number | null
    singletons?: number | null
    threshold?: number | null
  } | null
}

/** 人脸聚合组明细分页响应 */
export interface FaceClusterDetections {
  group_id: number
  items: DetectionItem[]
  total: number
  page: number
  size: number
}

/** 创建扫描任务（增量/半增量/全量；autoMatch 扫描完成后自动全库匹配，默认关闭） */
export async function startFaceScan(
  scope: 'incremental' | 'semi' | 'all' = 'semi',
  autoMatch = false,
): Promise<{ task_id: number; total: number }> {
  const { data } = await apiClient.post<{ task_id: number; total: number }>('/face-scan/start', {
    scope,
    auto_match: autoMatch,
  })
  return data
}

/** 创建全库候选匹配任务（可限定人物范围与阈值） */
export async function runFaceMatch(
  params: {
    scope?: 'all' | 'bloggers' | 'models'
    person_type?: 'blogger' | 'model'
    person_id?: number
    threshold?: number
  } = {},
): Promise<{ task_id: number }> {
  const { data } = await apiClient.post<{ task_id: number }>('/face-match/run', params)
  return data
}

/** 最近一次扫描/匹配任务状态（扫描页轮询） */
export async function fetchFaceScanTask(): Promise<FaceScanTaskStatus> {
  const { data } = await apiClient.get<FaceScanTaskStatus>('/face-scan/task')
  return data
}

/** 结果查询（聚合按人物 / 明细按人物 / 未匹配） */
export async function fetchFaceScanResults(
  params: {
    status?: 'pending' | 'confirmed'
    person_type?: 'blogger' | 'model'
    person_id?: number
    unmatched?: boolean
    page?: number
    size?: number
  } = {},
): Promise<FaceScanResults> {
  const { data } = await apiClient.get<FaceScanResults>('/face-scan/results', { params })
  return data
}

/** 审核确认 / 驳回 / 撤销 */
export async function confirmFaceScan(
  action: 'confirm' | 'reject' | 'undo',
  items: Array<{
    detection_id: number
    person_type?: 'blogger' | 'model'
    person_id?: number
  }>,
): Promise<ConfirmResult> {
  const { data } = await apiClient.post<ConfirmResult>('/face-scan/confirm', { action, items })
  return data
}

/** 创建人脸聚合聚类任务（未匹配人脸按疑似同一人分组） */
export async function runFaceCluster(
  params: { threshold?: number; min_group_size?: number } = {},
): Promise<{ task_id: number; message: string }> {
  const { data } = await apiClient.post<{ task_id: number; message: string }>(
    '/face-scan/cluster/run',
    params,
  )
  return data
}

/** 最近一次人脸聚合聚类任务状态 */
export async function fetchFaceClusterTask(): Promise<{ cluster_task: FaceScanTaskOut | null }> {
  const { data } = await apiClient.get<{ cluster_task: FaceScanTaskOut | null }>(
    '/face-scan/cluster/task',
  )
  return data
}

/** 人脸聚合分组分页查询 */
export async function fetchFaceClusterGroups(
  params: { page?: number; size?: number } = {},
): Promise<FaceClusterGroups> {
  const { data } = await apiClient.get<FaceClusterGroups>('/face-scan/cluster/groups', { params })
  return data
}

/** 某聚合组的人脸明细分页 */
export async function fetchFaceClusterDetections(
  groupId: number,
  params: { page?: number; size?: number } = {},
): Promise<FaceClusterDetections> {
  const { data } = await apiClient.get<FaceClusterDetections>(
    `/face-scan/cluster/groups/${groupId}/detections`,
    { params },
  )
  return data
}
