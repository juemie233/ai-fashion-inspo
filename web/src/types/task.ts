/** 任务中心统一任务类型：归一化任务队列(TaskQueue)与采集任务(ScraperTask)两类来源。 */

/** WebSocket 任务事件（后端 services/task_events.py 广播，契约只增不改） */
export interface TaskEventPayload {
  /** 生命周期阶段：running=开始执行，progress=进度更新，success/failed/cancelled=终态 */
  event: 'running' | 'progress' | 'success' | 'failed' | 'cancelled'
  task_id: number
  task_type: string
  status: string
  progress?: number
  done?: number
  total?: number
  error?: string | null
}

/** 任务是否处于终态（与后端任务状态机一致） */
export function isTaskTerminalStatus(status: string): boolean {
  return status === 'success' || status === 'failed' || status === 'cancelled'
}

/** 任务来源：queue=任务队列，scraper=采集任务 */
export type TaskSource = 'queue' | 'scraper'

/** 统一任务视图模型（前端聚合两类后端任务后归一化得到） */
export interface UnifiedTask {
  id: number
  /** 来源：queue=任务队列，scraper=采集任务 */
  source: TaskSource
  /** 归一化后的任务类型（采集任务统一为 scraper） */
  type: string
  /** 采集平台（仅采集任务有值，如 xiaohongshu/douyin） */
  platform: string
  /** 归一化后的状态（采集任务的 completed → success） */
  status: string
  /** 进度 0~100；采集任务无精确进度，取 -1 表示未知 */
  progress: number
  /** 批处理总数（采集任务对应 items_found） */
  total: number
  /** 已完成数（采集任务对应 items_added） */
  done: number
  /** 预测目标数量：队列任务=批处理总数，采集任务=配置的 max_count */
  target: number
  /** 开始时间：采集任务为 started_at；队列任务为 null（预测时回退 created_at） */
  started_at: string | null
  /** 中文标题，如「批量 AI 分析」「小红书采集」 */
  title: string
  /** 摘要信息（如采集关键词、错误摘要） */
  detail: string
  error: string | null
  created_at: string
  finished_at: string | null
}
