/**
 * 任务展示纯函数：把后端任务队列 / 采集任务的原始数据归一化为 UnifiedTask，
 * 并把任务 result 汇总成直观的完成文案。
 *
 * 这是 deep module：interface 只暴露 summarizeResult / describeRunningTask /
 * normalizeQueueTask / normalizeScraperTask，内部封装了各类任务的结果拼装、
 * 运行中阶段文案、状态归一化、平台与关键词展示、max_count 解析等规则。
 * 纯数据 → 数据，不接触 apiClient、路由或全局状态，因此可用一个普通对象入参
 * 在 vitest 中断言，无需 mock HTTP。
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

/** 支持「运行中暂停 / 已暂停恢复」的任务类型（与后端 _PAUSABLE_RUNNING_TYPES 对齐）：
 * - tag_network_analyze：标签网络分析（断点续算）
 * - batch_analyze / multi_analyze：AI 标签分析的批量/组合分析（worker 执行，
 *   暂停后恢复按「已成功跳过」幂等续算）
 */
export const PAUSABLE_TASK_TYPES = [
  'tag_network_analyze',
  'batch_analyze',
  'multi_analyze',
] as const

/** 判断任务类型是否支持暂停/恢复（任务列表与批量任务卡片的按钮显示统一走这里） */
export function isPausableTaskType(type: string): boolean {
  return (PAUSABLE_TASK_TYPES as readonly string[]).includes(type)
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
    case 'f2_import': {
      // f2 一键获取素材：本次入库量 + 导入计划的分层跳过。
      // 「已在垃圾桶」单独列出——它意味着用户主动丢弃过该内容，不会重新导入，
      // 想恢复要去垃圾桶还原（数字为 0 时不展示，避免日常噪音）。
      const plan = (r.plan || {}) as Record<string, unknown>
      const skipped = (plan.skipped || {}) as Record<string, number>
      const imported = (r.import || {}) as Record<string, unknown>
      const trash = Number(skipped['已在垃圾桶（不重新导入）']) || 0
      return [
        imported.imported != null ? `入库 ${imported.imported}` : '',
        plan.files != null ? `待入库 ${plan.files}` : '',
        trash ? `已在垃圾桶 ${trash}` : '',
      ]
        .filter(Boolean)
        .join(' · ')
    }
    default:
      return ''
  }
}

/**
 * 运行中任务的阶段文案（未结束的任务在「任务」列标题下显示这一行）。
 *
 * 为什么需要：进度条只给百分比，长时间任务（尤其 f2 获取素材）会出现「1% 挂了
 * 好几分钟」的观感，用户无法判断是在正常干活还是卡住了。原 f2 获取素材的下载阶段
 * 逐作者串行且作者历史要翻页，单个作者就可能几分钟——这里把阶段、计数和预期
 * 耗时讲清楚。
 *
 * @param type 任务类型
 * @param result 后端返回的 result（含 stage 阶段标记）
 * @param status 原始状态（pending/running/paused）
 * @param done 已完成计数（下载阶段=作者数，入库阶段=文件数）
 * @param total 总数（与 done 同口径）
 */
export function describeRunningTask(
  type: string,
  result: Record<string, unknown> | null,
  status: string,
  done: number,
  total: number,
): string {
  if (type !== 'f2_import') return ''
  if (status === 'paused') return '已暂停：已下载的文件与已入库素材都保留，可继续/重跑'
  if (status === 'pending') return '排队中：等待 worker 认领'
  const r = (result || {}) as Record<string, unknown>
  const stage = typeof r.stage === 'string' ? r.stage : ''
  const count = total > 0 ? `第 ${done}/${total} ` : ''
  if (stage === 'download') {
    // f2 逐个作者跑子进程，每个作者都要把作品列表翻页（每页固定等 timeout 秒），
    // 单作者十几秒到几分钟；已下载过的作品会被跳过，不会重复下载
    return `调 f2 下载中：${count}个作者 · 逐作者翻页，单作者约 10 秒~4 分钟`
  }
  if (stage === 'import') {
    return `入库中：${count}个文件 · 复制文件并生成缩略图`
  }
  if (stage === 'done') return '收尾中：写入统计与批次清单'
  return ''
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
    // 已结束 → 汇总结果；未结束（排队/运行/暂停）→ 说明当前阶段在干什么
    detail: finished
      ? summarizeResult(t.type, t.result, t.error || '')
      : describeRunningTask(t.type, t.result, t.status, t.done, t.total),
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
