/**
 * 任务展示纯函数：把后端任务队列 / 采集任务的原始数据归一化为 UnifiedTask，
 * 并把任务 result 汇总成直观的完成文案。
 *
 * 这是 deep module：interface 只暴露 summarizeResult / normalizeQueueTask /
 * normalizeScraperTask，内部封装了 6 种任务类型的结果拼装、状态归一化、
 * 平台与关键词展示、max_count 解析等规则。纯数据 → 数据，不接触 apiClient、
 * 路由或全局状态，因此可用一个普通对象入参在 vitest 中断言，无需 mock HTTP。
 *
 * useTaskCenter 只负责加载/筛选/分页/轮询/操作，展示规则集中在此复用。
 */

import type { UnifiedTask } from '@/types/task'
import { TASK_TYPE_LABELS, SCRAPER_PLATFORM_LABELS, normalizeTaskStatus } from '@/utils/taskLabel'
import { formatSize } from '@/utils/format'
import { parseKeywords as parseKeywordsList } from '@/utils/scraperKeywords'

/** 任务队列原始条目（/api/tasks 返回项） */
export interface QueueTask {
  id: number
  type: string
  status: string
  progress: number
  total: number
  done: number
  result: Record<string, unknown> | null
  error: string | null
  created_at: string
  updated_at: string
}

/** 采集任务原始条目（/api/scraper/tasks 返回项） */
export interface ScraperTaskRaw {
  id: number
  platform: string
  status: string
  config: string | null
  items_found: number
  items_added: number
  error: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
}

/**
 * 根据任务 result 生成直观的完成统计（成功任务的「干成了什么」）。
 *
 * @param type 任务类型
 * @param result 后端返回的 result 对象
 * @param error 错误信息（优先展示）
 */
export function summarizeResult(
  type: string,
  result: Record<string, unknown> | null,
  error: string | null,
): string {
  if (error) return error
  if (!result || typeof result !== 'object') return ''
  const r = result as Record<string, unknown>
  switch (type) {
    case 'vector_backfill':
      // 向量回填：展示图像/文本向量入库与跳过统计，替代抽象的「N/N」
      return [
        r.image_done != null ? `图像向量 ${r.image_done}` : '',
        r.text_done != null ? `文本向量 ${r.text_done}` : '',
        r.image_skipped || r.text_skipped
          ? `跳过 ${(Number(r.image_skipped) || 0) + (Number(r.text_skipped) || 0)}`
          : '',
      ]
        .filter(Boolean)
        .join(' · ')
    case 'deduplicate':
      return [
        r.files_deleted != null ? `删除 ${r.files_deleted} 个文件` : '',
        r.freed_bytes != null ? `释放 ${formatSize(Number(r.freed_bytes))}` : '',
        r.groups_processed != null ? `处理 ${r.groups_processed} 组` : '',
      ]
        .filter(Boolean)
        .join(' · ')
    case 'batch_delete':
      return [
        r.deleted_count != null ? `删除 ${r.deleted_count} 个素材` : '',
        r.freed_bytes != null ? `释放 ${formatSize(Number(r.freed_bytes))}` : '',
      ]
        .filter(Boolean)
        .join(' · ')
    case 'batch_analyze':
      return r.done != null ? `完成 ${r.done} 张` : ''
    case 'quality_check':
      return [
        r.approved != null ? `通过 ${r.approved}` : '',
        r.rejected != null ? `拒绝 ${r.rejected}` : '',
        r.pending != null ? `未判定 ${r.pending}` : '',
        r.failed != null ? `失败 ${r.failed}` : '',
        r.ai_generated ? `疑似 AI ${r.ai_generated}` : '',
      ]
        .filter(Boolean)
        .join(' · ')
    case 'enrich_blogger_profile':
      return [
        r.updated != null ? `补全 ${r.updated}` : '',
        r.skipped != null ? `跳过 ${r.skipped}` : '',
        r.failed != null ? `失败 ${r.failed}` : '',
      ]
        .filter(Boolean)
        .join(' · ')
    default:
      return ''
  }
}

/** 归一化任务队列条目为统一任务视图模型 */
export function normalizeQueueTask(t: QueueTask): UnifiedTask {
  const status = normalizeTaskStatus(t.status)
  const finished = status === 'success' || status === 'failed' || status === 'cancelled'
  return {
    id: t.id,
    source: 'queue',
    type: t.type,
    platform: '',
    status,
    progress: t.progress,
    total: t.total,
    done: t.done,
    target: t.total,
    started_at: null,
    title: TASK_TYPE_LABELS[t.type] || t.type,
    detail: summarizeResult(t.type, t.result, t.error || ''),
    error: t.error,
    created_at: t.created_at,
    finished_at: finished ? t.updated_at : null,
  }
}

/** 关键词展示（任务详情）：解析 config 中的关键词，带前缀拼接；无则返回空串 */
export function formatKeywords(config: string | null): string {
  const kw = parseKeywordsList(config)
  return kw.length > 0 ? `关键词：${kw.join('、')}` : ''
}

/** 解析采集任务配置中的目标采集数量 max_count（无则返回 0） */
export function parseMaxCount(config: string | null): number {
  if (!config) return 0
  try {
    const obj = JSON.parse(config) as { max_count?: unknown }
    return typeof obj?.max_count === 'number' ? obj.max_count : 0
  } catch {
    return 0
  }
}

/** 归一化采集任务条目为统一任务视图模型 */
export function normalizeScraperTask(t: ScraperTaskRaw): UnifiedTask {
  const status = normalizeTaskStatus(t.status)
  const platform = SCRAPER_PLATFORM_LABELS[t.platform] || t.platform
  const keywords = formatKeywords(t.config)
  return {
    id: t.id,
    source: 'scraper',
    type: 'scraper',
    platform: t.platform,
    status,
    progress: -1,
    total: t.items_found,
    done: t.items_added,
    target: parseMaxCount(t.config),
    started_at: t.started_at,
    title: `${platform}采集`,
    detail: [keywords, t.error].filter(Boolean).join(' · ') || '',
    error: t.error,
    created_at: t.created_at,
    finished_at: t.finished_at,
  }
}
