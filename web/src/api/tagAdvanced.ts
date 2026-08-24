/** 高级标签管理相关 API 调用（健康度/聚类/网络图/批量编辑/层级树/历史/效果分析）。 */

import apiClient from './client'
import type {
  BatchEditResult,
  BatchEditRule,
  ClusterScanResult,
  CombinationPair,
  CoverageStats,
  HealthIssuePage,
  HealthIssueType,
  HealthScanResult,
  HistoryItem,
  HistoryOperation,
  NetworkAnalysisResult,
  SourceDist,
  TaskStatus,
  TreeItem,
  TrendingItem,
} from '@/types/tagAdvanced'

// ===== 健康度 =====

/** 提交健康度扫描任务，返回 task_id */
export async function scanHealth(duplicateThreshold = 0.75) {
  const { data } = await apiClient.post<{ message: string; task_id: number }>('/tags/health/scan', {
    duplicate_threshold: duplicateThreshold,
  })
  return data
}

/** 获取某问题类型的明细（分页） */
export async function fetchHealthIssue(
  issueType: HealthIssueType,
  page = 1,
  size = 50,
): Promise<HealthIssuePage> {
  const { data } = await apiClient.get(`/tags/health/${issueType}`, {
    params: { page, size },
  })
  return data
}

// ===== 自动聚类 =====

/** 提交聚类扫描任务，返回 task_id */
export async function scanClusters(
  params: { threshold?: number; use_cooccurrence_boost?: boolean; min_group_size?: number } = {},
) {
  const { data } = await apiClient.post<{ message: string; task_id: number }>(
    '/tags/clusters/scan',
    params,
  )
  return data
}

/** 应用选中的候选组（合并 / 合并且保留别名） */
export async function applyClusters(body: {
  batch_id?: string
  groups: Array<{
    group_id?: string
    target_tag_id?: number
    source_tag_ids?: number[]
    keep_as_alias?: boolean
  }>
}) {
  const { data } = await apiClient.post<{
    applied: number
    merged: number
    aliases_created: number
    errors: Array<{ group: string; message: string }>
    batch_id: string
  }>('/tags/clusters/apply', body)
  return data
}

// ===== 网络图分析 =====

/** 提交网络图分析任务，返回 task_id */
export async function analyzeNetwork(
  params: {
    limit?: number
    min_count?: number
    category?: string | null
    with_communities?: boolean
    with_centrality?: boolean
    max_edges_per_node?: number
  } = {},
) {
  const { data } = await apiClient.post<{ message: string; task_id: number }>(
    '/tags/network/analyze',
    params,
  )
  return data
}

// ===== 批量高级编辑 =====

/** 批量高级编辑（dry_run=true 预览；false 执行） */
export async function batchEditTags(body: { dry_run: boolean; rules: BatchEditRule[] }) {
  const { data } = await apiClient.post<BatchEditResult>('/tags/batch-edit', body)
  return data
}

// ===== 层级树 =====

/** 懒加载获取层级树某一层节点（parentId=null 表示根） */
export async function fetchTree(parentId: number | null, page = 1, size = 200) {
  const { data } = await apiClient.get<{
    items: TreeItem[]
    total: number
    parent_id: number | null
  }>('/tags/tree', { params: { parent_id: parentId, page, size } })
  return data
}

/** 批量移动标签层级 */
export async function moveTags(moves: Array<{ tag_id: number; parent_id: number | null }>) {
  const { data } = await apiClient.post<{
    moved: number
    errors: Array<{ tag_id: number; message: string }>
  }>('/tags/move', { moves })
  return data
}

// ===== 操作历史 =====

/** 分页查询操作历史 */
export async function fetchHistory(
  params: {
    page?: number
    size?: number
    operation?: HistoryOperation | ''
    tag_id?: number
    batch_id?: string
  } = {},
) {
  const { data } = await apiClient.get<{ items: HistoryItem[]; total: number }>('/tags/history', {
    params,
  })
  return data
}

/** 回滚一条操作历史 */
export async function rollbackHistory(historyId: number) {
  const { data } = await apiClient.post<{ rolled_back: boolean; message: string }>(
    `/tags/history/${historyId}/rollback`,
  )
  return data
}

// ===== 使用效果分析 =====

/** 热度升降榜 */
export async function fetchTrending(days = 30, top = 20) {
  const { data } = await apiClient.get<{
    days: number
    rising: TrendingItem[]
    falling: TrendingItem[]
  }>('/tags/effect/trending', { params: { days, top } })
  return data
}

/** 标签组合排行 */
export async function fetchCombinations(limit = 20, minCount = 2) {
  const { data } = await apiClient.get<{ pairs: CombinationPair[]; total: number }>(
    '/tags/effect/combinations',
    { params: { limit, min_count: minCount } },
  )
  return data
}

/** 覆盖度统计 */
export async function fetchCoverage() {
  const { data } = await apiClient.get<CoverageStats>('/tags/effect/coverage')
  return data
}

/** 来源分布 */
export async function fetchSourceDist() {
  const { data } = await apiClient.get<SourceDist>('/tags/effect/source_dist')
  return data
}

// ===== 任务状态（供面板复用） =====

/** 查询任务状态（GET /api/tasks/{id}） */
export async function fetchTaskStatus(taskId: number) {
  const { data } = await apiClient.get<TaskStatus>(`/tasks/${taskId}`)
  return data
}

// 任务类型 → 结果类型映射（供面板把 task.result 安全转型）
export type TagAnalysisTaskType = 'tag_health_scan' | 'tag_cluster_scan' | 'tag_network_analyze'

export function asHealthResult(result: Record<string, unknown> | null): HealthScanResult | null {
  return result ? (result as unknown as HealthScanResult) : null
}

export function asClusterResult(result: Record<string, unknown> | null): ClusterScanResult | null {
  return result ? (result as unknown as ClusterScanResult) : null
}

export function asNetworkResult(
  result: Record<string, unknown> | null,
): NetworkAnalysisResult | null {
  return result ? (result as unknown as NetworkAnalysisResult) : null
}
