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

/** 创建扫描任务（增量/全量；autoMatch 扫描完成后自动全库匹配） */
export async function startFaceScan(
  scope: 'incremental' | 'all' = 'incremental',
  autoMatch = true,
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
