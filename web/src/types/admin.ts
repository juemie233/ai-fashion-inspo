/** 素材管理后台相关类型定义。 */

/** 月度新增统计 */
export interface MonthStat { month: string; count: number }
/** 来源类型统计 */
export interface SourceStat { source_type: string; count: number }
/** 媒体类型统计 */
export interface MediaStat { media_type: string; count: number }
/** 分析状态统计 */
export interface StatusStat { status: string; count: number; label: string }
/** 大文件条目 */
export interface LargeFile { id: string; file_path: string; source_type: string; created_at: string | null; size_bytes: number; exists: boolean }
/** 缺失文件：数据库有记录但磁盘文件不存在 */
export interface MissingFile { file_path: string; inspiration_ids: string[] }
/** 孤立文件：磁盘有文件但数据库无记录 */
export interface OrphanFile { file_path: string; size_bytes: number }
/** 重复文件组：相同哈希的多个文件 */
export interface DuplicateGroup { hash: string; files: { id: string; file_path: string; size_bytes: number }[] }
/** 去重结果 */
export interface DedupResult { groups_processed: number; files_deleted: number; freed_bytes: number }

/** 管理后台统计概览 */
export interface Stats {
  total_count: number
  total_size_bytes: number
  thumbnail_size_bytes: number
  images_size_bytes: number
  untagged_count: number
  analysis_failed_count: number
  favorite_count: number
  total_tags: number
  tombstone_count: number
  by_source_type: SourceStat[]
  by_media_type: MediaStat[]
  by_analysis_status: StatusStat[]
  by_month: MonthStat[]
}

/** 后台任务（批量删除/去重）—— 数据库驱动任务队列 */
export interface AdminTask {
  id: number
  type: string
  status: string
  progress: number
  total: number
  done: number
  result: {
    label?: string
    deleted_count?: number
    freed_bytes?: number
    groups_processed?: number
    files_deleted?: number
  } | null
  error: string | null
}
