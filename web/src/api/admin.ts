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
  /** 本次补算并缓存的感知哈希数（首跑/增量渐进补齐，之后为 0） */
  backfilled: number
  /** 当前已缓存感知哈希的图片数（全库渐进完备） */
  cached_total: number
}

/** 扫描视觉近似重复的图片素材（全库随机抽样 + 感知哈希缓存补算，仅返回候选） */
export async function fetchNearDuplicates(
  limit = 1000,
  threshold = 32,
): Promise<NearDuplicateResult> {
  const { data } = await apiClient.post<NearDuplicateResult>('/admin/near-duplicates', {
    limit,
    threshold,
  })
  return data
}
