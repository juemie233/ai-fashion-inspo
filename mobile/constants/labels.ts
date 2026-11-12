/** 中文字典：分类 / 平台 / 任务类型 / 状态——合并原本散落在 6 个文件里的重复常量表。
 *
 * 审查报告 P1-10：`CATEGORY_LABELS` 曾复制 4 份（详情页、标签管理、标签编辑器、标签筛选）、
 * `PLATFORM_LABELS` 2 份、`STATUS_META` 2 份。改一处漏其余就会出现「同一个分类两个中文名」。
 * 现在只有这里一份，新增分类/平台/状态只改本文件。
 */

import type { ListFilter, SortKey } from '../services/api'
import type { StatusTone } from './theme'

/** 标签分类中文名（与后端 `TagCategory` 枚举一致） */
export const CATEGORY_LABELS: Record<string, string> = {
  style: '风格',
  item_type: '单品',
  color: '颜色',
  body_part: '穿着方式',
  fit: '版型',
  season: '季节',
  attribute: '属性',
  atmosphere: '氛围',
  design_detail: '设计细节',
  expression: '表情',
  free: '自定义',
}

/** 分类中文名（未知分类回退原值，避免显示空白） */
export function tagCategoryLabel(category: string | null | undefined): string {
  if (!category) return '其他'
  return CATEGORY_LABELS[category] ?? category
}

/** 平台中文名 */
export const PLATFORM_LABELS: Record<string, string> = {
  douyin: '抖音',
  xiaohongshu: '小红书',
  other: '其他',
  instagram: 'Instagram',
  weibo: '微博',
}

/** 平台中文名（未知回退原值） */
export function platformLabel(platform: string | null | undefined): string {
  if (!platform) return '其他'
  return PLATFORM_LABELS[platform] ?? platform
}

/** 任务类型中文名（后端 `TASK_HANDLERS` 的真实取值，17 种；缺失时回退原值） */
export const TASK_TYPE_LABELS: Record<string, string> = {
  batch_analyze: '批量 AI 分析',
  multi_analyze: '多模态分析',
  image_gen: '本地出图',
  face_scan: '人脸扫描',
  face_match: '人脸匹配',
  face_cluster: '人脸聚类',
  quality_check: '质量审核',
  vector_backfill: '向量回填',
  phash_backfill: '去重回填',
  deduplicate: '内容去重',
  tag_cluster_scan: '标签聚类',
  tag_health_scan: '标签体检',
  tag_network_analyze: '标签网络分析',
  batch_delete: '批量删除',
  enrich_blogger_profile: '博主资料补全',
  blogger_avatar_fill: '人物头像补全',
  f2_import: '抖音素材获取',
}

/** 任务状态文案 + 色调（`task_queue.status`） */
export const TASK_STATUS_META: Record<string, { label: string; tone: StatusTone }> = {
  pending: { label: '排队中', tone: 'warning' },
  running: { label: '进行中', tone: 'brand' },
  success: { label: '已完成', tone: 'success' },
  failed: { label: '失败', tone: 'danger' },
  paused: { label: '已暂停', tone: 'warning' },
  cancelled: { label: '已取消', tone: 'neutral' },
}

/** 出图批次状态文案 + 色调（后端 `GENERATION_STATUSES`） */
export const GENERATE_STATUS_META: Record<string, { label: string; tone: StatusTone }> = {
  queued: { label: '排队中', tone: 'warning' },
  waiting_vram: { label: '等待显存', tone: 'warning' },
  running: { label: '出图中', tone: 'brand' },
  done: { label: '已完成', tone: 'success' },
  failed: { label: '失败', tone: 'danger' },
  cancelled: { label: '已取消', tone: 'neutral' },
}

/** 状态兜底（未知状态原样显示，色调走中性） */
export function statusMeta(
  table: Record<string, { label: string; tone: StatusTone }>,
  status: string | null | undefined,
): { label: string; tone: StatusTone } {
  if (!status) return { label: '未知', tone: 'neutral' }
  return table[status] ?? { label: status, tone: 'neutral' }
}

/** 素材库筛选胶囊（互斥，选中即整体替换条件） */
export const FILTER_OPTIONS: { key: string; label: string; filter: ListFilter }[] = [
  { key: 'all', label: '全部', filter: {} },
  { key: 'fav', label: '收藏', filter: { is_favorite: true } },
  { key: 'tagged', label: '已标注', filter: { tag_status: 'tagged' } },
  { key: 'untagged', label: '未标注', filter: { tag_status: 'untagged' } },
]

/** 排序选项（手机侧够用的三种，其余留在 Web；手机本期无评分入口，不展示评分排序） */
export const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: 'newest', label: '最新' },
  { key: 'oldest', label: '最早' },
  { key: 'random', label: '随机' },
]

/** 当前筛选命中的是哪个胶囊 */
export function activeFilterKey(filter: ListFilter): string {
  if (filter.is_favorite) return 'fav'
  if (filter.tag_status === 'tagged') return 'tagged'
  if (filter.tag_status === 'untagged') return 'untagged'
  return 'all'
}

/** 排序中文名（未知回退「最新」） */
export function sortLabel(sort: SortKey): string {
  return SORT_OPTIONS.find((s) => s.key === sort)?.label ?? '最新'
}
