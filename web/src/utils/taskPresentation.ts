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
 * - quality_check：质量审核（同样按批次边界停；已判定的素材写了 quality_status
 *   与审核日志，恢复时只查剩下的 pending）
 * - f2_import：一键获取素材（已下载文件与已入库素材保留，恢复按内容判重续算）
 *
 * ⚠ 这份清单与后端 `app/routers/tasks.py` 的白名单靠人工对齐（跨语言无法共用常量），
 * 两侧都有用例锁内容——改一处必须同步另一处。
 */
export const PAUSABLE_TASK_TYPES = [
  'tag_network_analyze',
  'batch_analyze',
  'multi_analyze',
  'quality_check',
  'f2_import',
] as const

/** 判断任务类型是否支持暂停/恢复（任务列表与批量任务卡片的按钮显示统一走这里） */
export function isPausableTaskType(type: string): boolean {
  return (PAUSABLE_TASK_TYPES as readonly string[]).includes(type)
}

/** 支持「运行中取消」的任务类型（与后端 _CANCELABLE_RUNNING_TYPES 对齐）：
 * - face_scan / face_match：人脸扫描与匹配（增量语义，重跑自动跳过已扫部分）
 * - tag_network_analyze：标签网络分析（断点续算）
 * - f2_import：一键获取素材（取消后已下载文件与已入库素材保留）
 * - batch_analyze / multi_analyze：AI 标签分析的批量/组合分析（每批检查一次，
 *   已写入的分析日志与标签保留）
 * - quality_check：质量审核（每批检查一次，已判定的 quality_status 与审核日志保留）
 *
 * ⚠ 这份清单与后端 `app/routers/tasks.py` 的白名单靠人工对齐（跨语言无法共用常量），
 * 两侧都有用例锁内容——改一处必须同步另一处，否则界面不显示按钮或后端直接 400。
 */
export const CANCELABLE_TASK_TYPES = [
  'face_scan',
  'face_match',
  'tag_network_analyze',
  'f2_import',
  'batch_analyze',
  'multi_analyze',
  'quality_check',
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
        // 「我的列表」入口（点赞/收藏）与博主主页入口同类型，靠 fetch_mode 区分
        r.fetch_mode === 'collection' ? '我的收藏' : r.fetch_mode === 'like' ? '我的喜欢' : '',
        imported.imported != null ? `入库 ${imported.imported}` : '',
        // 计划数与实际入库数一致时不重复展示；不一致（有失败）才补一句计划量
        planned && planned !== importedCount ? `计划 ${planned}` : '',
        trash ? `已在垃圾桶 ${trash}` : '',
        bloggerCreated ? `新登记博主 ${bloggerCreated}` : '',
        // 「先扫描、后下载」：本次只下了哪几个收藏夹（没勾的夹一件都没下）
        collectFolderText(r),
        // 收藏模式：本批素材聚合进「抖音收藏」合集的结果
        collectText(r),
        // 「我的列表」模式：下载结束后跨模式重复合并（like ↔ collection → 硬链接）
        mergeText(r),
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
 * 收藏模式的合集聚合结果（后端 result.collection）：本批素材加进「抖音收藏」合集的条数。
 * 没有该字段（其它模式）时返回空串。
 */
function collectText(r: Record<string, unknown>): string {
  const c = (r.collection || {}) as Record<string, unknown>
  const name = typeof c.name === 'string' && c.name ? c.name : ''
  if (!name) return ''
  const added = Number(c.added ?? 0) || 0
  const created = c.created === true ? '（新建）' : ''
  return `已加入「${name}」合集${created} +${added}`
}

/**
 * 跨模式重复合并的结果文案（后端 result.fetch.merge）。
 *
 * 背景：f2 判断「这个作品下过没有」只看**当前模式目录里有没有同名文件**（它没有
 * 下载台账），所以同一作品既被点赞又被收藏时，两个目录各下一份、各存一份。后端在
 * 下载结束后把「同名 + 内容完全相同」的那些合并成硬链接——两个目录里文件都还在
 * （f2 两侧的跳过逻辑继续有效），磁盘只占一份。没合并到东西时不占版面。
 */
function mergeText(r: Record<string, unknown>): string {
  const fetch = (r.fetch || {}) as Record<string, unknown>
  const merge = (fetch.merge || {}) as Record<string, unknown>
  const linked = Number(merge.linked ?? 0) || 0
  if (!linked) return ''
  const saved = Number(merge.saved_bytes ?? 0) || 0
  return `合并跨模式重复 ${linked} 个${saved ? ` · 省 ${formatSize(saved)}` : ''}`
}

/**
 * 「按选中收藏夹下载」的进度文案（后端 result.collect_progress）。
 *
 * 与平铺收藏的区别：分母是真的——各夹 total_number 之和，所以这里能给出
 * 「已处理 n/m 件」与当前夹；平铺模式只能按耗时给软进度（点赞/收藏总数要翻到底才知道）。
 * 没有该字段（平铺模式 / 尚未开始逐夹枚举）时返回空串，回落到原有文案。
 */
function collectProgressText(r: Record<string, unknown>): string {
  // 该字段只由「按选中收藏夹下载」那条链路写入，因此**用字段存在与否**判定走哪条文案：
  // 刚进入下载阶段时各计数还是 0（夹清单要 1~2 秒才回来），此时也该说「逐夹列举作品」，
  // 而不是回落成平铺收藏的文案（用户明明勾了夹，文案却说在翻整个收藏列表）。
  if (r.collect_progress == null) return ''
  const cp = (r.collect_progress || {}) as Record<string, unknown>
  const folders = Array.isArray(cp.folders) ? cp.folders : []
  const works = Number(cp.works ?? 0) || 0
  const totalWorks = Number(cp.total_works ?? 0) || 0
  const current = typeof cp.current === 'string' ? cp.current : ''
  const at = current ? `正在下载收藏夹「${current}」` : '正在逐夹列举作品'
  const doneFolders = `已完成 ${folders.length} 个夹`
  const worksText = totalWorks
    ? `已处理 ${works}/${totalWorks} 件`
    : `已处理 ${works} 件（正在取夹内清单）`
  return `只下勾选的收藏夹：${at} · ${doneFolders} · ${worksText}；已下载过的作品会自动跳过`
}

/**
 * 「按选中收藏夹下载」的完成文案（后端 result.fetch.collect）。
 *
 * 「先扫描、后下载」的那条链路才会写这个字段：这里报「下了几个夹、共处理多少件」，
 * 让用户一眼看到自己勾掉的那些夹确实一件都没下。平铺收藏没有它，返回空串。
 */
function collectFolderText(r: Record<string, unknown>): string {
  const fetch = (r.fetch || {}) as Record<string, unknown>
  const collect = (fetch.collect || {}) as Record<string, unknown>
  const folders = Array.isArray(collect.folders) ? collect.folders : []
  if (!folders.length) return ''
  const works = folders.reduce(
    (sum, f) => sum + (Number((f as Record<string, unknown>).works ?? 0) || 0),
    0,
  )
  const missing = Array.isArray(collect.missing_folders) ? collect.missing_folders.length : 0
  return `按收藏夹下载 ${folders.length} 个夹 · ${works} 件${missing ? `（${missing} 个夹已不存在）` : ''}`
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
  // 暂停文案对所有可暂停类型都适用（f2 说「产物保留」，其余说「已处理的记入结果、恢复不重复」），
  // 因此放在 f2 专属分支之前——否则质量审核/批量分析暂停后会一行说明都没有
  if (status === 'paused') {
    return type === 'f2_import'
      ? '已暂停：已下载的文件与已入库素材都保留，可继续/重跑'
      : '已暂停：已处理的素材已记入结果，点「继续」从断点接着跑（已处理的不重复）'
  }
  if (type !== 'f2_import') return ''
  if (status === 'pending') return '排队中：等待 worker 认领'
  const r = (result || {}) as Record<string, unknown>
  const stage = typeof r.stage === 'string' ? r.stage : ''
  const personalMode = r.fetch_mode === 'like' || r.fetch_mode === 'collection'
  const collectMode = r.fetch_mode === 'collection'
  const listLabel = collectMode ? '我的收藏' : '我的喜欢'
  const listName = collectMode ? '收藏' : '点赞'
  const count = total > 0 ? `第 ${done}/${total} ` : ''
  if (stage === 'download') {
    if (personalMode) {
      // 「先扫描、后下载」（按选中收藏夹）：逐夹枚举作品后交给 f2 下载器，
      // 分母是各夹 total_number 之和（真分母），比平铺模式的软进度可信
      const byFolder = collectProgressText(r)
      if (byFolder) return byFolder
      // 「我的列表」模式：f2 的分页没有「遇到已下载就停」，全量时要空翻到底（每页固定
      // 等一次 timeout）；`like_max_counts>0` 时只翻最近 N 条（列表最新在前），快得多。
      // 注：f2 的点赞/收藏模式**不读 `-i`**，日期窗口在这里无效，能收窄的只有这个条数。
      const live = likeDownloadText(r)
      const maxCounts = Number(r.like_max_counts ?? 0) || 0
      const scope = maxCounts
        ? `增量：只翻最近 ${maxCounts} 条${listName}`
        : '全量翻页（零新增时进度条会停在 0，属正常），首轮可能较慢'
      return `拉取${listLabel}（${listName}）中：${live ? `${live} · ` : ''}${scope}；已下载过的作品会自动跳过`
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
    // 「我的列表」入库后的补登记：来源作者动辄几百个，这一步要逐个建博主并绑定素材
    return `登记来源作者博主中：把未登记的${listName}作者补建成抖音博主并绑定本批素材（不进下载白名单）`
  }
  if (stage === 'done') {
    return collectMode
      ? '收尾中：写入统计、批次清单与「抖音收藏」合集'
      : '收尾中：写入统计与批次清单'
  }
  return ''
}

/** 归一化任务队列条目为统一任务视图模型 */
export function normalizeQueueTask(t: QueueTask): UnifiedTask {
  const status = normalizeTaskStatus(t.status)
  const finished = status === 'success' || status === 'failed' || status === 'cancelled'
  // 抖音三个入口共用 f2_import 类型：点赞/收藏入口在标题上区分开，
  // 否则任务中心/抖音采集历史里几行看起来一模一样
  const personalMode = t.result?.fetch_mode
  const personalTitle =
    personalMode === 'collection' ? '抖音我的收藏' : personalMode === 'like' ? '抖音我的喜欢' : ''
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
    title: personalTitle || TASK_TYPE_LABELS[t.type] || t.type,
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
