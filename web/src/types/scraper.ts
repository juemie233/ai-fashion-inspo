/** 采集模块共享类型定义。 */

/** 采集任务 */
export interface ScraperTask {
  id: number
  platform: string
  status: string
  config: string | null
  items_found: number
  items_added: number
  error?: string | null
  diagnostics?: string | null
  started_at?: string | null
  finished_at?: string | null
  created_at: string
}

/** 单次搜索漏斗明细 */
export interface FunnelSearch {
  keyword: string
  sort_type: string
  cards_total?: number
  cards_with_img?: number
  cards_without_img?: number
  skipped_small?: number
  skipped_icon?: number
  urls_extracted?: number
  batch_added?: number
  batch_skipped_existing?: number
  batch_skipped_content_dup?: number
  batch_skipped_http?: number
  batch_skipped_network?: number
  error?: string
}

/** 任务漏斗诊断数据 */
export interface FunnelDiagnostics {
  per_search: FunnelSearch[]
  summary: {
    total_found: number
    skipped_url_seen: number
    skipped_content_dup: number
    skipped_http_error: number
    skipped_network_error: number
    total_added: number
  }
}

/** 可用的采集源 */
export interface ScraperSource {
  platform: string
  name: string
  status: string
  features: string[]
  note: string
}

/** 平台 Cookie 状态 */
export interface CookieStatus {
  platform: string
  exists: boolean
  age_hours: number
  valid: boolean
  hint: string
}

/** 采集专用 Chrome 连接状态 */
export interface ChromeStatus {
  state: 'running' | 'not_started' | 'port_conflict' | 'starting'
  detail: string
  pid: number | null
}

/** 定时采集计划 */
export interface ScraperSchedule {
  id: number
  platform: string
  keywords: string[]
  max_count: number
  sort_mode: string | null
  enabled: boolean
  interval_minutes: number
  next_run_at: string | null
  last_run_at: string | null
  last_task_id: number | null
  run_count: number
  created_at: string
}
