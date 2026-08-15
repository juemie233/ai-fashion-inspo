/** 任务类型与状态的中文映射（任务中心专用）。 */

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
