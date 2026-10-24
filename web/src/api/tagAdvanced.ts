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

// 任务类型 → 结果类型映射（供面板把 task.result 安全转型）

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

// ===== 标签治理（颜色剥离 / 新标签观察名单 / 单标签共现）=====

/** 颜色前缀剥离的统计摘要（dry-run 与 apply 共用同一口径；仅本模块使用） */
interface ColorStripSummary {
  category: string
  /** 扫描了多少个该类别标签 */
  total_scanned: number
  /** 会被重命名（剥离颜色前缀）的数量 */
  renamed: number
  /** 剥离后与已有标签撞名、会合并的数量 */
  merged: number
  /** 跳过（剥完为空名 / 无法处理）的数量 */
  skipped: number
  /** 新名命中别名而跳过的数量 */
  alias_skipped: number
  /** 跨类别撞名跳过的数量 */
  cross_category_skipped: number
  /** 预计重指向的关联行数 */
  links_repointed: number
  /** 预计补建的颜色标签关联行数 */
  color_links_added: number
}

/** dry-run 额外返回「旧名→新名」样例，方便执行前肉眼确认 */
export interface ColorStripPreview extends ColorStripSummary {
  /** 颜色前缀词表大小（来自 color 类别标签名） */
  color_prefixes: number
  samples: Array<{ old: string; new: string; action: string }>
}

/** 颜色剥离 preview（不写库） */
export async function stripColorDryRun(
  body: { category?: string; limit?: number } = {},
): Promise<ColorStripPreview> {
  const { data } = await apiClient.post<ColorStripPreview>('/tags/color-strip/dry-run', body)
  return data
}

/** 颜色剥离执行（会写操作历史，可回滚）；返回批号便于去历史页核对 */
export async function stripColorApply(
  body: { category?: string; limit?: number } = {},
): Promise<ColorStripSummary & { batch_id: string }> {
  const { data } = await apiClient.post<ColorStripSummary & { batch_id: string }>(
    '/tags/color-strip/apply',
    body,
  )
  return data
}

/** 观察名单里的一条 AI 新标签 */
export interface WatchTag {
  id: number
  name: string
  category: string
  created_at: string
  usage_count: number
}

/** AI 新标签观察名单（最近 N 天新建、用量仍 ≤ max_usage） */
export async function fetchWatchList(
  params: { days?: number; max_usage?: number; limit?: number } = {},
): Promise<{
  days: number
  max_usage: number
  cutoff: string
  /** 真实命中数（可能远大于 items.length：列表按 limit 截断） */
  total: number
  /** 本次实际返回的条数 */
  returned: number
  items: WatchTag[]
}> {
  const { data } = await apiClient.get<{
    days: number
    max_usage: number
    cutoff: string
    total: number
    returned: number
    items: WatchTag[]
  }>('/tags/watchlist', { params })
  return data
}

/** 确认保留（source 改 manual，从观察名单消失） */
export async function keepWatchTags(tagIds: number[]): Promise<{ kept: number }> {
  const { data } = await apiClient.post<{ kept: number }>('/tags/watchlist/keep', {
    tag_ids: tagIds,
  })
  return data
}

/** 某标签「常一起出现的标签」（详情小卡） */
export async function fetchTagCooccurrence(
  tagId: number,
  limit = 8,
): Promise<{
  tag: { id: number; name: string; category: string } | null
  materials: number
  items: Array<{ id: number; name: string; category: string; count: number }>
}> {
  const { data } = await apiClient.get<{
    tag: { id: number; name: string; category: string } | null
    materials: number
    items: Array<{ id: number; name: string; category: string; count: number }>
  }>(`/tags/${tagId}/cooccurrence`, { params: { limit } })
  return data
}

/** 别名建议的一条配对（两侧都保证存在；仅本模块使用） */
interface AliasPair {
  tag_a: { id: number; name: string; category: string }
  tag_b: { id: number; name: string; category: string }
  similarity: number
}

/**
 * 别名建议：相似度落在 [min, max) 的标签对。
 *
 * 为什么不用 `findDuplicates(0.6)`：那个接口先按相似度倒序截断 50 条，返回的全是
 * ≥0.75 的重复对，前端再过滤就空了（实测踩过）。这里区间过滤在截断之前。
 * 首次计算较慢（本库 22k 标签约 30 秒），后端按标签修改时间缓存；超时给到 90 秒。
 */
export async function fetchAliasSuggestions(
  params: { min_similarity?: number; max_similarity?: number; limit?: number } = {},
): Promise<{ total: number; cached: boolean; pairs: AliasPair[] }> {
  const { data } = await apiClient.get<{
    min_similarity: number
    max_similarity: number
    total: number
    cached: boolean
    pairs: AliasPair[]
  }>('/tags/alias-suggestions', { params, timeout: 90000 })
  return data
}
