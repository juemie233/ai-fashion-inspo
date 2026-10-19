/** AI 模型分析统计（后端 GET /ai/model-stats）：任务管理页据此估算批量 AI 分析的剩余时间。 */

import apiClient from './client'

/** 单个模型的统计行；`models[0]` 是「（全部模型汇总）」 */
export interface ModelStatItem {
  model_name: string
  /** 分析次数（含失败） */
  total_analyses: number
  success_count: number
  failure_count: number
  success_rate: number
  /** 平均耗时（毫秒）：后端按该模型的全部日志求均值 */
  avg_time_ms: number
  avg_tags: number
  last_used: string
}

/** 拉取模型统计。形状异常时返回空数组（调用方回退到旧速率估算，不报错）。 */
export async function fetchModelStats(): Promise<ModelStatItem[]> {
  const { data } = await apiClient.get<unknown>('/ai/model-stats')
  const raw = (data as { models?: unknown } | null)?.models
  return Array.isArray(raw) ? (raw as ModelStatItem[]) : []
}

/**
 * 从统计行里取「平均耗时（毫秒）」：优先用后端给的「（全部模型汇总）」行
 * （它按分析次数加权），拿不到时前端自己按次数加权算一遍。
 *
 * 为什么需要它：任务管理页的「预计剩余」要对批量 AI 分析用「模型平均耗时 × 未完成数」，
 * 平均耗时的唯一权威来源是 AI 模型管理页展示的那份统计。
 */
export function overallAvgMs(items: ModelStatItem[]): number | null {
  const summary = items.find((m) => (m.model_name || '').includes('全部模型汇总'))
  if (summary && summary.avg_time_ms > 0) return summary.avg_time_ms
  const rows = items.filter((m) => m.avg_time_ms > 0 && m.total_analyses > 0)
  if (rows.length === 0) return null
  const total = rows.reduce((sum, m) => sum + m.total_analyses, 0)
  return rows.reduce((sum, m) => sum + m.avg_time_ms * m.total_analyses, 0) / total
}
