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
  avatar_path: string | null
  count: number
  best_conf: number | null
  /** 平均置信度（按人物聚合时返回；用于显示「N 条 · 平均 x · 最高 y」） */
  avg_conf?: number | null
}

/** 单条命中/未匹配明细 */
export interface DetectionItem {
  detection_id: number
  inspiration_id: string
  confidence: number | null
  file_path: string
  thumbnail_path: string | null
  /** 素材媒体类型：video 时 file_path 为 mp4，不能当 <img> 加载（前端据此防破图） */
  media_type?: 'image' | 'video' | null
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
  /** 代表素材媒体类型：video 时 rep_file_path 为 mp4，不能当 <img> 加载 */
  rep_media_type?: 'image' | 'video' | null
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

/** 人脸识别子服务可用性（供人脸入口**事前**提示，而不是点了才报错） */
export interface FaceServiceStatus {
  /** 是否配置了 FACE_SERVICE_URL */
  enabled: boolean
  /** 子服务是否可达（enabled=false 时恒为 false） */
  reachable: boolean
  url: string
  /** 中文原因，可直接展示给用户 */
  message: string
  /** 子服务 /health 详情（reachable=true 时才有） */
  detail?: Record<string, unknown> | null
}

/** 查询人脸子服务状态：接口恒返回 200，可用性看 enabled/reachable */
export async function fetchFaceServiceStatus(): Promise<FaceServiceStatus> {
  const { data } = await apiClient.get<FaceServiceStatus>('/face-scan/service-status')
  return data
}

/** 批量注册候选簇：与后端 register-plan 的 clusters 字段一一对应 */
export interface FaceRegisterCluster {
  size: number
  avg_det_score: number
  suggested_inspiration_ids: string[]
  cover_detection_ids: number[]
  candidates: Array<{
    detection_id?: number
    inspiration_id: string
    det_score: number
    thumbnail_path: string | null
  }>
}

/** 批量注册计划中的一位博主 */
export interface FaceRegisterPlanItem {
  person_type: 'blogger'
  person_id: number
  name: string
  avatar_path: string | null
  inspiration_count: number
  registered: boolean
  clusters: FaceRegisterCluster[]
  skip_reason: string | null
}

/** 批量注册计划（只读） */
export interface FaceRegisterPlan {
  items: FaceRegisterPlanItem[]
  summary: {
    total: number
    with_candidates: number
    no_candidates: number
    min_cluster_size: number
    min_det_score: number
  }
}

/** 批量注册单条结果 */
export interface FaceRegisterBatchResultItem {
  person_id: number
  ok: boolean
  face_count: number
  error: string | null
  warnings?: string[]
}

/** 批量注册结果 */
export interface FaceRegisterBatchResult {
  items: FaceRegisterBatchResultItem[]
  succeeded: number
  failed: number
}

/** 拉取批量注册候选计划（只读；后端会在服务端完成聚类与排序） */
export async function fetchFaceRegisterPlan(
  params: {
    limit?: number
    min_cluster_size?: number
    min_det_score?: number
    only_with_candidates?: boolean
  } = {},
): Promise<FaceRegisterPlan> {
  const { data } = await apiClient.post<FaceRegisterPlan>('/face-scan/register-plan', params)
  return data
}

/** 批量注册博主人脸（一次最多 20 位，前端分批循环） */
export async function registerFaceBatch(
  items: Array<{ person_type: 'blogger' | 'model'; person_id: number; inspiration_ids: string[] }>,
): Promise<FaceRegisterBatchResult> {
  const { data } = await apiClient.post<FaceRegisterBatchResult>('/face-scan/register-batch', {
    items,
  })
  return data
}

/** 聚类分组：整组忽略（标记为不匹配，永久退出后续匹配） */
export async function ignoreFaceClusterGroup(
  groupId: number,
): Promise<{ group_id: number; ignored: number }> {
  const { data } = await apiClient.post<{ group_id: number; ignored: number }>(
    `/face-scan/cluster/groups/${groupId}/ignore`,
  )
  return data
}

/** 聚类分组：按组新建人物并注册人脸（随后整组确认关联） */
export async function createFaceClusterPerson(
  groupId: number,
  payload: { name: string; platform: 'xiaohongshu' | 'douyin' | 'other' },
): Promise<{
  blogger_id: number
  name: string
  registered: boolean
  face_count: number
  assigned: number
  group_id: number
}> {
  const { data } = await apiClient.post<{
    blogger_id: number
    name: string
    registered: boolean
    face_count: number
    assigned: number
    group_id: number
  }>(`/face-scan/cluster/groups/${groupId}/create-person`, payload)
  return data
}
