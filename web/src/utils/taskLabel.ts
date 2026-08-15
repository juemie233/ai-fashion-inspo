/** 任务类型与状态的中文映射（任务中心专用）。 */

import type { UnifiedTask } from '@/types/task'

/** 任务类型中文标签 */
export const TASK_TYPE_LABELS: Record<string, string> = {
  batch_analyze: '批量 AI 分析',
  quality_check: '质量审核',
  batch_delete: '批量删除',
  deduplicate: '去重',
  scraper: '采集',
}

/** 采集平台中文标签 */
export const SCRAPER_PLATFORM_LABELS: Record<string, string> = {
  xiaohongshu: '小红书',
  douyin: '抖音',
}

/** 任务状态中文标签（success 与 completed 语义一致，统一展示「已完成」） */
export const TASK_STATUS_LABELS: Record<string, string> = {
  pending: '排队中',
  running: '运行中',
  success: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

/** 归一化状态：采集任务的 completed → success */
export function normalizeTaskStatus(status: string): string {
  return status === 'completed' ? 'success' : status
}

/** 状态对应的 naive-ui 标签颜色类型 */
export function taskStatusType(
  status: string,
): 'default' | 'info' | 'success' | 'error' | 'warning' {
  const map: Record<string, 'default' | 'info' | 'success' | 'error' | 'warning'> = {
    pending: 'default',
    running: 'info',
    success: 'success',
    failed: 'error',
    cancelled: 'warning',
  }
  return map[status] || 'default'
}

/** 任务类型对应的标签颜色（naive-ui NTag type），不同任务类型用醒目颜色区分 */
export function taskTypeTagColor(
  type: string,
): 'default' | 'primary' | 'info' | 'success' | 'warning' | 'error' {
  const map: Record<string, 'default' | 'primary' | 'info' | 'success' | 'warning' | 'error'> = {
    batch_analyze: 'primary',
    quality_check: 'warning',
    batch_delete: 'error',
    deduplicate: 'success',
    scraper: 'info',
  }
  return map[type] || 'default'
}

// ===== 剩余时间预测 =====

/** 将秒数格式化为中文时长（不足 1 分钟显示秒，否则显示分钟/小时） */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return ''
  const s = Math.ceil(seconds)
  if (s < 60) return `${s} 秒`
  const m = Math.round(s / 60)
  if (m < 60) return `${m} 分钟`
  const h = Math.floor(m / 60)
  const rm = m % 60
  return rm > 0 ? `${h} 小时 ${rm} 分` : `${h} 小时`
}

/** 预测运行中任务的剩余时间（仅采集任务与批量 AI 分析）；信息不足时返回 null */
export function predictEta(task: UnifiedTask): string | null {
  if (task.status !== 'running') return null
  if (task.type !== 'batch_analyze' && task.type !== 'scraper') return null
  const startTs = task.started_at
    ? new Date(task.started_at).getTime()
    : new Date(task.created_at).getTime()
  if (!startTs || Number.isNaN(startTs)) return null
  const elapsedSec = (Date.now() - startTs) / 1000
  if (elapsedSec < 10) return null // 运行时间太短，速率不稳定，先不预测
  const { done, target } = task
  if (done <= 0 || target <= 0 || target <= done) return null
  const rate = done / elapsedSec
  if (rate <= 0) return null
  return formatDuration((target - done) / rate)
}
