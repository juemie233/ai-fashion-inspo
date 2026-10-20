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

/** Cookie 真实登录态校验结果（探测平台登录态接口得出） */
export interface CookieVerify {
  state: 'valid' | 'invalid' | 'unknown' | 'no_file'
  detail: string
  checked_at: string
  probe_url: string
}

/** 平台 Cookie 状态 */
export interface CookieStatus {
  platform: string
  exists: boolean
  age_hours: number
  valid: boolean
  hint: string
  /** 最近一次真实校验结果；缺省/null 表示从未校验 */
  verify?: CookieVerify | null
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

/** 入库前预览结果（后端 `GET /api/scraper/f2-preview`，与入库共用判重链路）。 */
export interface F2PreviewResult {
  /** 扫描根目录 */
  root: string
  /** 扫描到的可识别文件总数 */
  files: number
  /** 预计要导入的文件数（判重后） */
  files_to_import: number
  /** 预计入库的作品数（一个图集算一个作品） */
  works: number
  /** 跳过明细：{ 原因: 数量 } */
  skipped: Record<string, number>
  /** 因 limit 未处理的作品数 */
  deferred_works: number
  /** 本次统计的作者范围（null = 不限） */
  authors_scope: string[] | null
  /** 扫描目录里是否有任何可识别产物 */
  has_any: boolean
  /** 哈希缓存统计（首次全量算、之后命中缓存；供排查「为什么这次慢」） */
  hash_cache?: Record<string, unknown>
}
