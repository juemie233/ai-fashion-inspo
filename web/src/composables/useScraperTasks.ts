/** 采集管理页任务域：任务数据、筛选排序、任务操作、轮询与来源/状态标签。 */

import { getApiErrorMessage } from '@/utils/apiError'
import { ref, computed, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import apiClient from '@/api/client'
import { SOURCE_TYPE_LABELS } from '@/utils/sourceLabel'
import { formatDate } from '@/utils/format'
import { copyToClipboard } from '@/utils/clipboard'
import { normalizeTaskStatus, taskStatusType } from '@/utils/taskLabel'
import { parseKeywords as parseKeywordsList } from '@/utils/scraperKeywords'
import type { ScraperTask, ScraperSource, CookieStatus } from '@/types/scraper'

/** 平台显示文案（复用来源映射，单一来源避免多处重复维护） */
export const PLATFORM_LABELS: Record<string, string> = SOURCE_TYPE_LABELS

/** 任务状态显示文案 */
export const STATUS_LABELS: Record<string, string> = {
  pending: '等待中', running: '运行中', completed: '已完成', failed: '失败', cancelled: '已取消',
}

/** 任务列表轮询间隔（毫秒）：有运行/等待中的任务时每 5 秒刷新一次 */
const POLL_INTERVAL_MS = 5000

/** 任务域数据与操作集合，由 ScraperView 及其子组件消费。 */
export function useScraperTasks() {
  
  // ===== 数据 =====
  const sources = ref<ScraperSource[]>([])
  const tasks = ref<ScraperTask[]>([])
  const tombstoneCount = ref(0)
  const cookieStatuses = ref<Record<string, CookieStatus>>({})
  const defaultMaxCount = ref(0)

  // ===== 任务筛选/排序/分页 =====
  const taskFilterPlatform = ref('')
  const taskFilterStatus = ref(localStorage.getItem('scraper-task-filter') || '')
  const taskSort = ref(localStorage.getItem('scraper-task-sort') || 'newest')
  const taskPage = ref(1)
  const taskPageSize = 50
  const taskTotal = ref(0)

  // 持久化任务筛选/排序：刷新或再次进入时保持上次选择
  watch(taskFilterStatus, (v) => { localStorage.setItem('scraper-task-filter', v) })
  watch(taskSort, (v) => { localStorage.setItem('scraper-task-sort', v) })

  // ===== 操作 loading 态 =====
  const deletingTask = ref<number | null>(null)
  const clearing = ref(false)
  const retrying = ref(false)
  const retryingTask = ref<number | null>(null)
  const copyingTask = ref<number | null>(null)

  // ===== 派生状态 =====
  // 任务统计来自后端聚合（覆盖全部筛选结果，而非仅当前页）
  const taskStats = ref({ total: 0, completed: 0, failed: 0, running: 0, pending: 0, rate: 0 })
  const hasActiveTasks = computed(() => taskStats.value.running + taskStats.value.pending > 0)
  const hasFailedTasks = computed(() => taskStats.value.failed > 0)

  /** 将列表接口返回的统计写入 taskStats */
  function applyTaskStats(data: { total: number; stats?: Record<string, number> }) {
    const total = data.total || 0
    const s = data.stats || {}
    taskStats.value = {
      total,
      completed: s.completed || 0,
      failed: s.failed || 0,
      running: s.running || 0,
      pending: s.pending || 0,
      rate: total > 0 ? Math.round((s.completed || 0) / total * 100) : 0,
    }
  }

  // ===== 数据加载 =====
  async function loadAll() {
    try {
      const [sRes, tRes, cXhs, cDy] = await Promise.all([
        apiClient.get('/scraper/sources'),
        apiClient.get('/scraper/tasks', {
          params: {
            platform: taskFilterPlatform.value || undefined,
            status: taskFilterStatus.value || undefined,
            sort: taskSort.value,
            page: taskPage.value,
            size: taskPageSize,
          },
        }),
        apiClient.get('/scraper/cookie-status', { params: { platform: 'xiaohongshu' } })
          .catch(() => ({ data: { platform: 'xiaohongshu', exists: false, age_hours: 0, valid: false, hint: '检查失败' } })),
        apiClient.get('/scraper/cookie-status', { params: { platform: 'douyin' } })
          .catch(() => ({ data: { platform: 'douyin', exists: false, age_hours: 0, valid: false, hint: '检查失败' } })),
      ])
      sources.value = sRes.data.sources
      tasks.value = tRes.data.items
      taskTotal.value = tRes.data.total || 0
      applyTaskStats(tRes.data)
      tombstoneCount.value = sRes.data.tombstone_count || 0
      defaultMaxCount.value = sRes.data.default_max_count || 0
      cookieStatuses.value = {
        xiaohongshu: cXhs.data as CookieStatus,
        douyin: cDy.data as CookieStatus,
      }
    } catch { Message.error('加载失败') }
  }

  async function refreshTasks() {
    try {
      const tRes = await apiClient.get('/scraper/tasks', {
        params: {
          platform: taskFilterPlatform.value || undefined,
          status: taskFilterStatus.value || undefined,
          sort: taskSort.value,
          page: taskPage.value,
          size: taskPageSize,
        },
      })
      tasks.value = tRes.data.items
      taskTotal.value = tRes.data.total || 0
      applyTaskStats(tRes.data)
    } catch { /* 轮询/静默刷新失败不提示，保持旧数据 */ }
  }

  /** 筛选或排序变化：回到第一页并刷新 */
  function onFilterChange() { taskPage.value = 1; refreshTasks() }

  /** 翻页：更新页码并刷新列表 */
  function onPageChange(page: number) { taskPage.value = page; refreshTasks() }

  // ===== 任务操作 =====
  async function cancelTask(taskId: number) {
    try {
      await apiClient.post(`/scraper/tasks/${taskId}/cancel`)
      Message.success('已取消')
      refreshTasks()
    } catch (e) { Message.error(getApiErrorMessage(e, '取消失败')) }
  }

  async function deleteSingleTask(taskId: number) {
    try {
      deletingTask.value = taskId
      const res = await apiClient.delete(`/scraper/tasks/${taskId}`)
      if (res.status === 200 || res.status === 204) {
        Message.success('已删除')
        refreshTasks()
      }
    } catch (e) {
      // 204 同属 2xx 成功响应（apiClient validateStatus 放行），不会落入 catch，无需单独处理
      Message.error('删除失败: ' + (getApiErrorMessage(e, '')))
    } finally { deletingTask.value = null }
  }

  async function clearAllTasks() {
    try {
      clearing.value = true
      await apiClient.delete('/scraper/tasks')
      taskPage.value = 1
      Message.success('已清空')
      refreshTasks()
    } catch { Message.error('清空失败') } finally { clearing.value = false }
  }

  async function retryFailedTasks() {
    try {
      retrying.value = true
      Message.success((await apiClient.post('/scraper/tasks/retry-failed')).data.message)
      refreshTasks()
      startPollIfNeeded()
    } catch (e) {
      const is404 = (e as { response?: { status?: number } })?.response?.status === 404
      Message.info(is404 ? '没有失败任务' : getApiErrorMessage(e, '重试失败'))
    } finally { retrying.value = false }
  }

  async function retrySingleTask(taskId: number) {
    try {
      retryingTask.value = taskId
      await apiClient.post(`/scraper/tasks/${taskId}/retry`)
      Message.success('已重新加入队列（断点续采）')
      refreshTasks()
      startPollIfNeeded()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '续采失败'))
    } finally { retryingTask.value = null }
  }

  /** 复制任务配置重新采集：解析原任务 config，按相同参数创建新任务 */
  async function copyTask(task: ScraperTask) {
    let cfg: any = {}
    try { cfg = task.config ? JSON.parse(task.config) : {} } catch { cfg = {} }
    const keywords: string[] = Array.isArray(cfg.keywords) ? cfg.keywords : []
    if (!keywords.length) { Message.warning('原任务没有关键词配置，无法复制'); return }
    try {
      copyingTask.value = task.id
      const payload: any = {
        platform: task.platform,
        keywords,
        max_count: cfg.max_count || 20,
        headless: !!cfg.headless,
        cdp_port: task.platform === 'xiaohongshu' ? (cfg.cdp_port || 9222) : null,
      }
      if (task.platform === 'xiaohongshu' && cfg.sort_mode && cfg.sort_mode !== 'general') payload.sort_mode = cfg.sort_mode
      await apiClient.post('/scraper/tasks', payload)
      Message.success('已按原配置创建新采集任务')
      refreshTasks()
      startPollIfNeeded()
    } catch (e) {
      // 特殊业务：后端 detail 可能是「带启动命令」的对象（Chrome 未启动时引导复制命令）
      const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data
        ?.detail
      if (typeof detail === 'object' && detail && (detail as { command?: string }).command) {
        const d = detail as { error?: string; command: string }
        Message.error(d.error || '创建失败')
        setTimeout(() => copyText(d.command), 500)
      } else {
        Message.error(getApiErrorMessage(e, '创建失败'))
      }
    } finally { copyingTask.value = null }
  }

  // ===== 轮询：有运行/等待中的任务时每 5s 刷新一次 =====
  let pollTimer: ReturnType<typeof setInterval> | null = null
  function startPollIfNeeded() {
    if (pollTimer) return
    pollTimer = setInterval(async () => {
      if (hasActiveTasks.value) {
        await refreshTasks()
        if (!hasActiveTasks.value) stopPoll()
      }
    }, POLL_INTERVAL_MS)
  }
  function stopPoll() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
  }

  // ===== 工具函数 =====
  /** 复制文本到剪贴板（复用 utils/clipboard 实现，成功/失败给出提示） */
  async function copyText(text: string) {
    const ok = await copyToClipboard(text)
    if (ok) {
      Message.success('已复制')
    } else {
      Message.error('复制失败')
    }
  }

  /** 状态标签颜色：复用 taskLabel 的映射（completed 与 success 语义一致，返回 Arco 预设色） */
  function statusType(s: string): string {
    return taskStatusType(normalizeTaskStatus(s))
  }

  function platformName(p: string) { return sources.value.find(s => s.platform === p)?.name || PLATFORM_LABELS[p] || p }

  /** 关键词展示（表格列）：解析 config 中的关键词，逗号拼接，无则显示占位符 */
  function parseKeywords(c: string | null) {
    const kw = parseKeywordsList(c)
    return kw.length > 0 ? kw.join(', ') : '-'
  }

  function getTaskDuration(t: ScraperTask) {
    if (t.started_at && t.finished_at) {
      const ms = new Date(t.finished_at).getTime() - new Date(t.started_at).getTime()
      if (ms < 1000) return ms + 'ms'
      if (ms < 60000) return (ms / 1000).toFixed(0) + 's'
      return (ms / 60000).toFixed(1) + 'min'
    }
    return '-'
  }

  return {
    sources, tasks, tombstoneCount, cookieStatuses, defaultMaxCount,
    taskFilterPlatform, taskFilterStatus, taskSort, taskPage, taskPageSize, taskTotal,
    deletingTask, clearing, retrying, retryingTask, copyingTask,
    taskStats, hasFailedTasks,
    loadAll, refreshTasks, onFilterChange, onPageChange,
    cancelTask, deleteSingleTask, clearAllTasks, retryFailedTasks, retrySingleTask, copyTask,
    startPollIfNeeded, stopPoll, copyText,
    statusType, platformName, formatDate, parseKeywords, getTaskDuration,
  }
}
