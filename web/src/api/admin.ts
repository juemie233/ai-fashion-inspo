/** 素材管理后台相关 API 调用（导出 / 趋势 / 人物频次）。 */

import apiClient from './client'

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
  return data
}
