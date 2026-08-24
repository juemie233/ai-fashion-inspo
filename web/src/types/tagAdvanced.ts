/** 高级标签管理页的跨组件类型定义。 */

/** 后台任务通用结构（GET /api/tasks/{id} 响应） */
export interface TaskStatus {
  id: number
  type: string
  status: 'pending' | 'running' | 'success' | 'failed' | 'cancelled'
  progress: number
  total: number
  done: number
  result: Record<string, unknown> | null
  error: string | null
}

// ===== 健康度分析 =====

export type HealthIssueType = 'orphan' | 'low_frequency' | 'low_quality_name' | 'duplicate'

export interface HealthIssueItem {
  id: number
  name: string
  category: string
  source: string
  parent_id: number | null
  usage_count: number
  reason?: string | null
}

export interface DuplicateIssuePair {
  tag_a: { id: number; name: string; category: string; usage_count: number } | null
  tag_b: { id: number; name: string; category: string; usage_count: number } | null
  similarity: number
}

export interface HealthIssuePage {
  issue_type: HealthIssueType
  total: number
  page: number
  size: number
  items: HealthIssueItem[] | DuplicateIssuePair[]
}

export interface HealthScanResult {
  total: number
  score: number
  duplicate_threshold: number
  issues: Record<HealthIssueType, { count: number }>
  scanned_at: string
}

/** 健康度问题类型的中文文案 */
export const HEALTH_ISSUE_LABELS: Record<HealthIssueType, string> = {
  orphan: '孤儿标签（0 关联）',
  low_frequency: '低频标签（1 次关联）',
  low_quality_name: '低质命名',
  duplicate: '疑似重复',
}

// ===== 自动聚类 =====

export interface ClusterMember {
  id: number
  name: string
  category: string
  usage_count: number
}

export interface ClusterGroup {
  id: string
  reason: string
  suggested_target: ClusterMember
  members: ClusterMember[]
}

export interface ClusterScanResult {
  total: number
  threshold: number
  use_cooccurrence_boost: boolean
  min_group_size: number
  groups: ClusterGroup[]
}

// ===== 网络图分析 =====

export interface NetworkNode {
  id: number
  name: string
  category: string
  usage_count: number
  degree: number
  degree_centrality: number
  betweenness: number | null
  community: number
  is_bridge: boolean
}

export interface NetworkEdge {
  source: number
  target: number
  weight: number
}

export interface CommunityInfo {
  id: number
  size: number
  top_tags: string[]
}

export interface NetworkAnalysisResult {
  nodes: NetworkNode[]
  edges: NetworkEdge[]
  communities: CommunityInfo[]
  params: {
    limit: number
    min_count: number
    category: string | null
    with_communities: boolean
    with_centrality: boolean
  }
}

// ===== 批量高级编辑 =====

export interface BatchEditScope {
  tag_ids?: number[]
  category?: string
  source?: string
  search?: string
}

export interface BatchEditRule {
  type: 'regex_replace' | 'affix' | 'normalize' | 'regex_merge'
  pattern?: string
  replacement?: string
  mode?: string
  text?: string
  ops?: string[]
  target_template?: string
  scope?: BatchEditScope
}

export interface BatchEditPreviewItem {
  tag_id: number
  from: string
  to: string | null
  action: 'rename' | 'merge' | 'skip'
  conflict: boolean
  target: { id: number; name: string } | null
}

export interface BatchEditSummary {
  renamed: number
  merged: number
  skipped: number
  errors: number
}

export interface BatchEditResult {
  dry_run: boolean
  batch_id?: string
  preview?: BatchEditPreviewItem[]
  summary: BatchEditSummary
  errors?: Array<{ tag_id: number; message: string }>
}

/** 规则类型的中文文案 */
export const RULE_TYPE_LABELS: Record<BatchEditRule['type'], string> = {
  regex_replace: '正则查找替换',
  affix: '前后缀增删',
  normalize: '格式归一化',
  regex_merge: '正则批量合并',
}

// ===== 层级树 =====

export interface TreeItem {
  id: number
  name: string
  category: string
  parent_id: number | null
  usage_count: number
  has_children: boolean
}

export interface TreePage {
  items: TreeItem[]
  total: number
  parent_id: number | null
}

// ===== 操作历史 =====

export type HistoryOperation =
  | 'create'
  | 'rename'
  | 'category_change'
  | 'update'
  | 'move'
  | 'merge'
  | 'alias_add'
  | 'alias_remove'
  | 'batch_edit'
  | 'delete'

export interface HistoryItem {
  id: number
  batch_id: string | null
  operation: HistoryOperation
  tag_ids: number[]
  /** 受影响标签名（与 tag_ids 一一对应；已删除标签用 "#id" 兜底） */
  tag_names: string[]
  before: Record<string, unknown>
  after: Record<string, unknown>
  meta: Record<string, unknown> | null
  created_at: string | null
}

export interface HistoryPage {
  items: HistoryItem[]
  total: number
  page: number
  size: number
}

/** 操作类型的中文文案 */
export const HISTORY_OP_LABELS: Record<HistoryOperation, string> = {
  create: '创建',
  rename: '重命名',
  category_change: '改类别',
  update: '更新',
  move: '移动层级',
  merge: '合并',
  alias_add: '添加别名',
  alias_remove: '删除别名',
  batch_edit: '批量编辑',
  delete: '删除',
}

// ===== 使用效果分析 =====

export interface TrendingItem {
  id: number
  name: string
  current: number
  previous: number
  delta: number
}

export interface CombinationPair {
  tags: [string, string]
  count: number
}

export interface CoverageStats {
  inspiration_total: number
  with_tags: number
  tagged_ratio: number
  avg_tags_per_inspiration: number
  by_category: Record<string, number>
}

export interface SourceDist {
  by_source: Record<string, { tag_count: number; usage_total: number; avg_usage: number }>
  top_low_quality: Array<{ id: number; name: string; source: string; usage_count: number }>
}
