/** 任务展示规则：阶段文案、结果摘要、筛选项、时间与耗时格式化。
 *
 * 与 Web 的 `web/src/utils/taskPresentation.ts` 同口径（同一批后端字段、同一套文案），
 * 但只保留手机端用得到的部分：不做统一任务列表、不做 f2 收藏夹明细、不做按钮矩阵。
 *
 * 为什么要单独一层：任务页与任务详情页都要用，写在组件里会两处漂移；
 * 而且这些都是纯函数，能直接单测（后端字段缺失/脏值时不能崩）。
 */

import type { TaskItem, TaskRun } from '../services/api'

/** 状态筛选（后端只支持单状态过滤，所以这里一一对应，不做「进行中=pending+running」合成） */
export const TASK_STATUS_FILTERS: { key: string; label: string; status?: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'running', label: '进行中', status: 'running' },
  { key: 'pending', label: '排队中', status: 'pending' },
  { key: 'paused', label: '已暂停', status: 'paused' },
  { key: 'failed', label: '失败', status: 'failed' },
  { key: 'success', label: '已完成', status: 'success' },
]

/** 后端 `result.stage` → 中文（未收录的原样显示，方便发现新阶段） */
const STAGE_LABELS: Record<string, string> = {
  download: '下载中',
  import: '入库中',
  analyze: '分析中',
  analyze_pending: '等待分析',
  cluster: '聚类中',
  encode: '向量编码中',
  embed: '生成向量中',
  scan: '扫描中',
  match: '匹配中',
  quality: '质量审核中',
  settle: '收尾中',
}

/** 阶段中文名（未知阶段回退原值） */
export function stageLabel(stage: string | null | undefined): string {
  if (!stage) return ''
  return STAGE_LABELS[stage] ?? stage
}

/**
 * 未结束任务的一句话说明（排队 / 运行 / 暂停）。
 *
 * 进度条只给百分比，长时间任务会出现「卡在 1% 好几分钟」的观感；说清楚当前阶段
 * 与计数，用户才能判断是在正常干活还是挂了。已结束的任务返回空串（走摘要）。
 */
export function describeStage(task: TaskItem): string {
  const { status, type, done, total } = task
  const counter = total > 0 ? `第 ${done}/${total}` : ''

  if (status === 'paused') {
    return '已暂停：已处理的部分已记入结果，重跑会跳过已完成的'
  }
  if (status === 'pending') {
    return type === 'f2_import' ? '排队中：等待执行器认领' : '排队中：等待开始'
  }
  if (status !== 'running') return ''

  const stage = typeof task.result?.stage === 'string' ? task.result.stage : ''
  const label = stageLabel(stage)
  if (label) return counter ? `${label}：${counter}` : label
  // 没有阶段标记的任务（批量分析等）只补计数说明，避免与进度条重复
  return counter ? `处理中：${counter}` : '处理中'
}

/**
 * 已结束任务的结果摘要（成功/失败都用它；失败时优先显示错误，由页面决定）。
 *
 * 只列**用户能理解**的业务数字，不把内部字段名抛出来；未知类型返回空串，
 * 让页面退回「完成 N/M」的通用展示。
 */
export function summarizeTaskResult(task: TaskItem): string {
  if (task.status === 'failed') return ''
  const result = task.result
  if (!result || typeof result !== 'object') return ''
  const r = result as Record<string, unknown>
  const num = (key: string): number | null => {
    const value = r[key]
    return typeof value === 'number' && Number.isFinite(value) ? value : null
  }
  const parts: string[] = []

  switch (task.type) {
    case 'vector_backfill': {
      const image = num('image_done')
      const text = num('text_done')
      const failed = num('text_failed')
      const skipped = (num('image_skipped') ?? 0) + (num('text_skipped') ?? 0)
      if (image !== null) parts.push(`图像向量 ${image}`)
      if (text !== null) parts.push(`文本向量 ${text}`)
      if (failed) parts.push(`文本失败 ${failed}`)
      if (skipped) parts.push(`跳过 ${skipped}`)
      break
    }
    case 'quality_check': {
      const pairs: [string, string][] = [
        ['approved', '通过'],
        ['rejected', '拒绝'],
        ['pending', '未判定'],
        ['failed', '失败'],
      ]
      for (const [key, label] of pairs) {
        const value = num(key)
        if (value !== null) parts.push(`${label} ${value}`)
      }
      break
    }
    case 'batch_analyze': {
      const done = num('done')
      if (done !== null) parts.push(`完成 ${done} 张`)
      break
    }
    case 'deduplicate': {
      const groups = num('groups_processed')
      const deleted = num('files_deleted')
      if (deleted !== null) parts.push(`删除 ${deleted} 个文件`)
      if (groups !== null) parts.push(`处理 ${groups} 组`)
      break
    }
    case 'batch_delete': {
      const deleted = num('deleted_count')
      if (deleted !== null) parts.push(`删除 ${deleted} 个素材`)
      break
    }
    case 'f2_import': {
      const imported = (r.import ?? {}) as Record<string, unknown>
      const count = typeof imported.imported === 'number' ? imported.imported : null
      if (count !== null) parts.push(`入库 ${count}`)
      if (count === 0) parts.push('本轮没有新素材')
      break
    }
    default:
      return ''
  }
  return parts.join(' · ')
}

/**
 * 服务端时间解析：后端历史载荷可能没有时区后缀（naive UTC），
 * 直接 `new Date()` 会被当本地时间、差 8 小时。统一按 UTC 解析。
 */
export function parseServerTime(value: string | null | undefined): Date | null {
  if (!value) return null
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value)
  const date = new Date(hasZone ? value : `${value}Z`)
  return Number.isNaN(date.getTime()) ? null : date
}

/** `10-31 14:02` 形式的本地时间（跨年时自动带年份） */
export function formatRunTime(value: string | null | undefined, now = new Date()): string {
  const date = parseServerTime(value)
  if (!date) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  const md = `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
  return date.getFullYear() === now.getFullYear() ? md : `${date.getFullYear()}-${md}`
}

/** 单次执行耗时（秒级，未结束返回空） */
export function formatRunDuration(run: TaskRun): string {
  const start = parseServerTime(run.started_at)
  const end = parseServerTime(run.finished_at)
  if (!start || !end) return ''
  const seconds = Math.max(0, Math.round((end.getTime() - start.getTime()) / 1000))
  if (seconds < 60) return `${seconds} 秒`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} 分 ${seconds % 60} 秒`
  return `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分`
}

/** 运行台账的状态文案（后端 status：running/success/retry/failed） */
export function runStatusLabel(status: string): string {
  switch (status) {
    case 'running':
      return '执行中'
    case 'success':
      return '成功'
    case 'retry':
      return '重试中'
    case 'failed':
      return '失败'
    default:
      return status || '未知'
  }
}
