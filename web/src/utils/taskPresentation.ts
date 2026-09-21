/**
 * 任务展示纯函数：把后端任务队列 / 采集任务的原始数据归一化为 UnifiedTask，
 * 并把任务 result 汇总成直观的完成文案。
 *
 * 这是 deep module：interface 只暴露 summarizeResult / describeRunningTask /
 * compareUnifiedTasks / normalizeQueueTask / normalizeScraperTask，内部封装了各类任务的
 * 结果拼装、运行中阶段文案、状态归一化、平台与关键词展示、max_count 解析等规则。
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

/**
 * 任务列表排序：**已暂停的排最前**，其余按创建时间倒序（最新在前）。
 *
 * 为什么暂停优先：暂停任务是需要用户处理的（点「继续」或「取消」），把它留在
 * 「等你去点」的位置最顺手；否则它会被源源不断的新任务一层层压到后面。
 * 与后端 `/api/tasks` 的排序口径一致（`ORDER BY status='paused' 优先, id DESC`），
 * 双端一致才不会出现「翻页时顺序跳变」。
 *
 * 纯函数：入参是已归一化的任务，可在 vitest 里直接断言，无需 mock HTTP。
 */
export function compareUnifiedTasks(a: UnifiedTask, b: UnifiedTask): number {
  const pausedDiff = Number(b.status === 'paused') - Number(a.status === 'paused')
  if (pausedDiff !== 0) return pausedDiff
  return new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
}

/** 支持「运行中暂停 / 已暂停恢复」的任务类型（与后端 _PAUSABLE_RUNNING_TYPES 对齐）：
 * - tag_network_analyze：标签网络分析（断点续算）
 * - batch_analyze / multi_analyze：AI 标签分析的批量/组合分析（worker 执行，
 *   暂停后恢复按「已成功跳过」幂等续算）
 * - f2_import：一键获取素材（已下载文件与已入库素材保留，恢复按内容判重续算）
 */
export const PAUSABLE_TASK_TYPES = [
  'tag_network_analyze',
  'batch_analyze',
  'multi_analyze',
  'f2_import',
] as const

/** 判断任务类型是否支持暂停/恢复（任务列表与批量任务卡片的按钮显示统一走这里） */
export function isPausableTaskType(type: string): boolean {
  return (PAUSABLE_TASK_TYPES as readonly string[]).includes(type)
}

/** 支持「运行中取消」的任务类型（与后端 _CANCELABLE_RUNNING_TYPES 对齐）：
 * - face_scan / face_match：人脸扫描与匹配（增量语义，重跑自动跳过已扫部分）
 * - f2_import：一键获取素材（取消后已下载文件与已入库素材保留）
 */
export const CANCELABLE_TASK_TYPES = [
  'face_scan',
  'face_match',
  'tag_network_analyze',
  'f2_import',
] as const

/** 判断任务类型是否支持运行中取消（任务列表的取消按钮显示走这里） */
export function isCancelableTaskType(type: string): boolean {
  return (CANCELABLE_TASK_TYPES as readonly string[]).includes(type)
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
        // 其中抖音 IP 属地（离线读 f2 用户库）单独说明，否则「补全 N」看不出干了什么
        r.douyin_updated ? `抖音 IP 属地 ${r.douyin_updated}` : '',
        r.skipped != null ? `跳过 ${r.skipped}` : '',
        r.failed != null ? `失败 ${r.failed}` : '',
      ]
        .filter(Boolean)
        .join(' · ')
    case 'f2_import': {
      // f2 一键获取素材：本次入库量 + 导入计划的分层跳过。
      // 「已在垃圾桶」单独列出——它意味着用户主动丢弃过该内容，不会重新导入，
      // 想恢复要去垃圾桶还原（数字为 0 时不展示，避免日常噪音）。
      // 计数优先读结构化字段 plan.trash_skipped（后端跳过原因的中文文案改动
      // 不该让前端静默失效）；旧任务结果没有该字段时回退解析文案。
      const plan = (r.plan || {}) as Record<string, unknown>
      const skipped = (plan.skipped || {}) as Record<string, number>
      const imported = (r.import || {}) as Record<string, unknown>
      // 来源作者博主补登记（「我的喜欢」入库后）：只报新建数，复用/绑定不占版面
      const bloggers = (r.bloggers || {}) as Record<string, unknown>
      const bloggerCreated = Number(bloggers.created ?? 0) || 0
      const importedCount = Number(imported.imported ?? 0) || 0
      const planned = Number(plan.files ?? 0) || 0
      const trash = Number(plan.trash_skipped ?? skipped['已在垃圾桶（不重新导入）'] ?? 0) || 0
      return [
        // 点赞入口（我的喜欢）与博主主页入口同类型，靠 fetch_mode 区分
        r.fetch_mode === 'like' ? '我的喜欢' : '',
        imported.imported != null ? `入库 ${imported.imported}` : '',
        // 计划数与实际入库数一致时不重复展示；不一致（有失败）才补一句计划量
        planned && planned !== importedCount ? `计划 ${planned}` : '',
        trash ? `已在垃圾桶 ${trash}` : '',
        bloggerCreated ? `新登记博主 ${bloggerCreated}` : '',
      ]
        .filter(Boolean)
        .join(' · ')
    }
    default:
      return ''
  }
}

/**
 * 「我的喜欢」下载阶段的实时统计文案（后端 result.like_progress）。
 *
 * 点赞总数要全量翻页到底才知道，进度条没有真分母；这段文字就是给用户的证据：
 * 文件数在涨 = f2 在干活，「本次新增」为 0 = 这一轮没有新赞。
 * 注意全量模式下**零新增时进度条会一直停在 0**（它按已落盘文件数算），所以这段
 * 文案比进度条更可信。
 */
function likeDownloadText(r: Record<string, unknown>): string {
  const lp = (r.like_progress || {}) as Record<string, unknown>
  const files = Number(lp.files ?? 0) || 0
  if (!files && !Number(lp.added ?? 0)) return ''
  const bytes = Number(lp.bytes ?? 0) || 0
  const added = Number(lp.added ?? 0) || 0
  const size = bytes ? ` / ${formatSize(bytes)}` : ''
  return `本次新增 ${added} 个文件 · 目录内共 ${files} 个${size}`
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
  const likeMode = r.fetch_mode === 'like'
  const count = total > 0 ? `第 ${done}/${total} ` : ''
  if (stage === 'download') {
    if (likeMode) {
      // 点赞模式：f2 的点赞分页没有「遇到已下载就停」，全量时要空翻到底（每页固定
      // 等一次 timeout）；`like_max_counts>0` 时只翻最近 N 条（列表最新在前），快得多。
      // 注：f2 的点赞模式**不读 `-i`**，日期窗口在这里无效，能收窄的只有这个条数。
      const live = likeDownloadText(r)
      const maxCounts = Number(r.like_max_counts ?? 0) || 0
      const scope = maxCounts
        ? `增量：只翻最近 ${maxCounts} 条点赞`
        : '全量翻页（零新增时进度条会停在 0，属正常），首轮可能较慢'
      return `拉取我的喜欢（点赞）中：${live ? `${live} · ` : ''}${scope}；已下载过的作品会自动跳过`
    }
    // f2 逐个作者跑子进程，每个作者都要把作品列表翻页（每页固定等 timeout 秒），
    // 单作者十几秒到几分钟；已下载过的作品会被跳过，不会重复下载
    return `调 f2 下载中：${count}个作者 · 逐作者翻页，单作者约 10 秒~4 分钟`
  }
  if (stage === 'scan') {
    return '扫描与去重中：统计下载目录里的新作品，大目录需 1~2 分钟，之后才开始入库'
  }
  if (stage === 'import') {
    return `入库中：${count}个文件 · 复制文件并生成缩略图`
  }
  if (stage === 'blogger') {
    // 「我的喜欢」入库后的补登记：点赞作者动辄几百个，这一步要逐个建博主并绑定素材
    return '登记来源作者博主中：把未登记的点赞作者补建成抖音博主并绑定本批素材（不进下载白名单）'
  }
  if (stage === 'done') return '收尾中：写入统计与批次清单'
  return ''
}

/** 归一化任务队列条目为统一任务视图模型 */
export function normalizeQueueTask(t: QueueTask): UnifiedTask {
  const status = normalizeTaskStatus(t.status)
  const finished = status === 'success' || status === 'failed' || status === 'cancelled'
  // 抖音两条入口共用 f2_import 类型：点赞入口（我的喜欢）在标题上区分开，
  // 否则任务中心/抖音采集历史里两行看起来一模一样
  const likeImport = t.type === 'f2_import' && t.result?.fetch_mode === 'like'
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
    title: likeImport ? '抖音我的喜欢' : TASK_TYPE_LABELS[t.type] || t.type,
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
