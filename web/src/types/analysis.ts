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
  prompt_version?: string | null
  log_type?: string
  thumbnail_path: string | null
  file_path: string | null
  processing_time_ms: number | null
  error: string | null
  status: string
  created_at: string
  tags: Array<{ name: string; category: string }>
}

/** 组合分析可选提示词项（id=0 表示当前默认提示词，>=1 为历史保存版本） */
export interface PromptOption {
  id: number
  label: string
  source: 'current' | 'version'
  saved_at?: string | null
  length?: number | null
}

/** 多模型 × 多提示词组合分析的提交参数 */
export interface MultiAnalyzeParams {
  /** 视觉模型名列表（多选） */
  models: string[]
  /** 提示词版本 ID 列表（0 = 当前默认提示词） */
  promptIds: number[]
  /** 是否把标签合并到素材（组合分析默认 false） */
  applyTags: boolean
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
  time_comparison: Array<{
    analysis_id: number
    model_name: string
    processing_time_ms: number | null
    created_at: string | null
  }>
}

/** 批量对比（POST /ai/compare-batch）中的单条记录 */
export interface CompareBatchRecord {
  id: number
  inspiration_id: string
  model_name: string
  prompt_version: string | null
  processing_time_ms: number | null
  error: string | null
  status: string
  created_at: string | null
  tags: Array<{ name: string; category: string; confidence: number }>
}

/** 批量对比数据（勾选同一素材多条记录并排对比） */
export interface CompareBatchData {
  inspiration_id: string
  thumbnail_path: string | null
  file_path: string | null
  analyses: CompareBatchRecord[]
  analyses_count: number
  tag_diff: {
    common: string[]
    differing: Array<{ name: string; log_ids: number[] }>
    common_analysis_ids: number[]
  } | null
  time_comparison: Array<{
    analysis_id: number
    model_name: string
    prompt_version: string | null
    processing_time_ms: number | null
    created_at: string | null
  }>
}
