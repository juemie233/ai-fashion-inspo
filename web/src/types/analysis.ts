/** AI 标签分析面板共享类型定义。 */

/** 分析队列统计 */
export interface QueueStats {
  total: number
  analyzed: number
  unanalyzed: number
  failed: number
}

/** 正在进行的分析任务集合 */
export interface ActiveAnalysis {
  active_analyses: Record<string, string>
  count: number
}

/** 数据库驱动批量分析任务 */
export interface TaskInfo {
  id: number
  type: string
  status: string
  progress: number
  total: number
  done: number
  result: { success_count?: number; failed_count?: number } | null
  error: string | null
  retry_count: number
  max_retries: number
  next_retry_at: string | null
  created_at: string
  updated_at: string
}

/** 排队中素材 */
export interface QueueItem {
  inspiration_id: string
  thumbnail_path: string | null
  file_path: string | null
  status: string
}

/** 分析历史记录 */
export interface HistoryItem {
  id: number
  inspiration_id: string
  model_name: string
  log_type?: string
  thumbnail_path: string | null
  file_path: string | null
  processing_time_ms: number | null
  error: string | null
  status: string
  created_at: string
  tags: Array<{ name: string; category: string }>
}

/** 详情弹窗标签 */
export interface TagDetail {
  name: string
  category: string
  confidence: number
}

/** 分析详情 */
export interface AnalysisDetail {
  id: number
  inspiration_id: string
  model_name: string
  raw_response: string | null
  parsed_response: Record<string, any> | null
  processing_time_ms: number | null
  error: string | null
  status: string
  created_at: string | null
  thumbnail_path: string | null
  file_path: string | null
  tags: TagDetail[]
}

/** 分析结果对比数据 */
export interface CompareData {
  inspiration_id: string
  thumbnail_path: string | null
  file_path: string | null
  analyses: Array<{
    id: number
    model_name: string
    processing_time_ms: number | null
    error: string | null
    status: string
    created_at: string | null
    parsed_response: Record<string, any> | null
    tags_count: Record<string, number>
  }>
  analyses_count: number
  tag_diff: { added: string[]; removed: string[]; common: string[] } | null
  time_comparison: Array<{ analysis_id: number; model_name: string; processing_time_ms: number | null; created_at: string | null }>
}
