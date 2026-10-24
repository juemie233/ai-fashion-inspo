/** 素材管理后台相关 API 调用（导出 / 趋势 / 人物频次）。 */

import apiClient from './client'
import { warnItems } from '@/utils/apiGuard'

/** 每日新增趋势点 */
export interface TrendPoint {
  day: string
  count: number
}

/** 人物频次条目 */
export interface PersonFrequencyItem {
  id: number
  name: string
  person_type: string
  platform: string
  count: number
}

/** 提示词版本质量指标（按「提示词版本 × 模型」聚合） */
export interface PromptQualityItem {
  prompt_version: string | null
  /** 可读版本名（当前提示词 / 版本 #N / 未登记版本） */
  version_label: string
  model_name: string | null
  analyses: number
  successes: number
  success_rate: number
  /** 成功分析的平均标签数（基于结构化快照） */
  avg_tags: number
  corrections: number
  /** 每百次分析的纠错反馈数 */
  correction_rate: number
  /** 裸词率（仅请求 include_bare_rate 时返回，采样重放原始响应） */
  bare_rate?: number
  last_used_at: string | null
}

/** 提示词版本质量对比（数据洞察页；includeBareRate 会重放原始响应，较慢） */
export async function fetchPromptQuality(
  days = 30,
  includeBareRate = false,
): Promise<{
  days: number
  total_versions: number
  include_bare_rate: boolean
  items: PromptQualityItem[]
}> {
  const { data } = await apiClient.get('/ai/prompt-quality', {
    params: { days, include_bare_rate: includeBareRate },
  })
  return data
}

/** 导出全部素材为 CSV 并触发浏览器下载（走 apiClient 以携带 API Key 认证头） */
export async function exportInspirationsCsv(): Promise<void> {
  const res = await apiClient.get<Blob>('/admin/export', { responseType: 'blob' })
  const disposition = (res.headers['content-disposition'] as string) || ''
  const match = disposition.match(/filename="?([^";]+)"?/)
  const filename = match?.[1] || `inspirations_${Date.now()}.csv`
  const url = URL.createObjectURL(res.data)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

/** 获取近 N 天每日新增素材趋势 */
export async function fetchInspirationTrend(days = 30): Promise<TrendPoint[]> {
  const { data } = await apiClient.get<{ days: number; trend: TrendPoint[] }>('/admin/trend', {
    params: { days },
  })
  return data.trend
}

/** 获取人物 × 素材数量排行 */
export async function fetchPersonFrequency(limit = 20): Promise<PersonFrequencyItem[]> {
  const { data } = await apiClient.get<PersonFrequencyItem[]>('/admin/person-frequency', {
    params: { limit },
  })
  return data
}

/** 操作审计日志条目 */
export interface AuditLogItem {
  id: number
  action: string
  target_type: string
  count: number
  freed_bytes: number
  detail: string | null
  created_at: string | null
}

/** 获取破坏性操作审计日志（按时间倒序） */
export async function fetchAuditLogs(limit = 50): Promise<AuditLogItem[]> {
  const { data } = await apiClient.get<AuditLogItem[]>('/admin/audit-logs', {
    params: { limit },
  })
  // 校验日志条目关键字段（此前 created_at 缺时区曾致时间显示早 8 小时）
  warnItems(
    data,
    {
      id: 'number',
      action: 'string',
      count: 'number',
      freed_bytes: 'number',
      detail: 'string?',
      created_at: 'string?',
    },
    'audit-logs',
  )
  return data
}

/** 近似重复组内的单个文件 */
export interface NearDuplicateFile {
  id: string
  file_path: string
  thumbnail_path: string | null
  is_favorite: boolean
  created_at: string | null
  size_bytes: number
  score: number
  distance: number
}

/** 一组视觉近似重复的素材 */
export interface NearDuplicateGroup {
  rep_phash: string
  files: NearDuplicateFile[]
  keeper_id: string
  wasted_bytes: number
}

/** 近似重复扫描结果 */
export interface NearDuplicateResult {
  groups: NearDuplicateGroup[]
  scanned: number
  total: number
  truncated: boolean
  threshold: number
  /** 本次补算并缓存的感知哈希数（限时补算，未补完的交给后台任务） */
  backfilled: number
  /** 当前已缓存感知哈希的图片数（全库渐进完备） */
  cached_total: number
  /** 仍未缓存感知哈希的图片数（>0 时本次扫描只覆盖已缓存部分） */
  missing: number
  /** 哈希缓存是否已完整（未完整时建议先跑后台补齐任务） */
  cache_complete: boolean
  /** 算不出哈希的素材数（文件缺失/损坏，重跑也补不上） */
  unhashable: number
}

/** 扫描视觉近似重复的图片素材（phash 分组，仅返回候选）。
 *  limit=0 表示全库扫描（推荐，纯内存分组秒级返回、不漏检）；>0 为随机抽样数量。
 *  接口**只读缓存、不做补算**（请求内补一批要 30+ 秒，超过 axios 的 30 秒上限）：
 *  缺口看返回的 missing/cache_complete，补算走 startPhashBackfill 的后台任务 */
export async function fetchNearDuplicates(limit = 0, threshold = 32): Promise<NearDuplicateResult> {
  const { data } = await apiClient.post<NearDuplicateResult>('/admin/near-duplicates', {
    limit,
    threshold,
  })
  return data
}

/** 启动「感知哈希缓存补齐」后台任务（扫描接口只读缓存，缺口全交给 worker）。
 *
 *  force=true 时**整库重算**（含已缓存行）：哈希来源在 10-24 从原图换成缩略图，
 *  两种来源的哈希不同口径（同图中位差 31 位），旧缓存必须重算一次才能与新的对齐；
 *  force 时即使缓存已完整也会创建任务（不再返回 400）。
 *
 *  幂等：已有进行中任务时返回该任务（reused=true）。 */
export async function startPhashBackfill(force = false): Promise<{
  task_id: number | null
  total: number
  reused: boolean
  message: string
}> {
  const { data } = await apiClient.post('/admin/phash-backfill', undefined, {
    params: force ? { force: true } : undefined,
  })
  return data
}

/** 数据备份历史条目（一次时间戳命名的备份目录） */
export interface BackupHistoryItem {
  name: string
  success: boolean
  time: string | null
}

/** 数据备份状态（任务管理页「数据备份」卡片数据源，只读） */
export interface BackupStatus {
  enabled: boolean
  configured: boolean
  target_path: string
  running: boolean
  latest_success_at: string | null
  latest_success_dir: string | null
  history: BackupHistoryItem[]
  log_tail: string[]
}

/** 获取数据备份状态（只读，不触发备份；双通道备份的运行锁/历史均实时读取） */
export async function fetchBackupStatus(): Promise<BackupStatus> {
  const { data } = await apiClient.get<BackupStatus>('/admin/backup/status')
  return data
}
