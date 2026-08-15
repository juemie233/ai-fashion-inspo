/** 采集管理页任务域：任务数据、筛选排序、任务操作、轮询与来源/状态标签。 */

import { ref, computed, watch } from 'vue'
import { useMessage } from 'naive-ui'
import apiClient from '@/api/client'
import type { ScraperTask, ScraperSource, CookieStatus } from '@/types/scraper'

/** 平台显示文案 */
export const PLATFORM_LABELS: Record<string, string> = {
  xiaohongshu: '小红书', douyin: '抖音', browser_extension: '浏览器插件', scraper: '自动采集', manual_upload: '手动上传',
}

/** 任务状态显示文案 */
export const STATUS_LABELS: Record<string, string> = {
  pending: '等待中', running: '运行中', completed: '已完成', failed: '失败', cancelled: '已取消',
}

/** 任务域数据与操作集合，由 ScraperView 及其子组件消费。 */
export function useScraperTasks() {
  const message = useMessage()

  // ===== 数据 =====
  const sources = ref<ScraperSource[]>([])
  const tasks = ref<ScraperTask[]>([])
  const tombstoneCount = ref(0)
  const cookieStatuses = ref<Record<string, CookieStatus>>({})
  const defaultMaxCount = ref(0)

  // ===== 任务筛选/排序 =====
  const taskFilterPlatform = ref('')
  const taskFilterStatus = ref(localStorage.getItem('scraper-task-filter') || '')
  const taskSort = ref(localStorage.getItem('scraper-task-sort') || 'newest')
  const taskPage = ref(1)

  // 持久化任务筛选/排序：刷新或再次进入时保持上次选择
  watch(taskFilterStatus, (v) => { localStorage.setItem('scraper-task-filter', v) })
  watch(taskSort, (v) => { localStorage.setItem('scraper-task-sort', v) })

  // ===== 操作 loading 态 =====
  const deletingTask = ref<number | null>(null)
  const clearing = ref(false)
  const retrying = ref(false)
  const retryingTask = ref<number | null>(null)

  // ===== 派生状态 =====
  const hasActiveTasks = computed(() => tasks.value.some(t => t.status === 'pending' || t.status === 'running'))
  const taskStats = computed(() => {
    const t = tasks.value
    const total = t.length
    const completed = t.filter(x => x.status === 'completed').length
    const failed = t.filter(x => x.status === 'failed').length
    const rate = total > 0 ? Math.round(completed / total * 100) : 0
    return { total, completed, failed, rate }
  })
  const hasFailedTasks = computed(() => tasks.value.some(t => t.status === 'failed'))

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
          },
        }),
        apiClient.get('/scraper/cookie-status', { params: { platform: 'xiaohongshu' } })
          .catch(() => ({ data: { platform: 'xiaohongshu', exists: false, age_hours: 0, valid: false, hint: '检查失败' } })),
        apiClient.get('/scraper/cookie-status', { params: { platform: 'douyin' } })
          .catch(() => ({ data: { platform: 'douyin', exists: false, age_hours: 0, valid: false, hint: '检查失败' } })),
      ])
      sources.value = sRes.data.sources
      tasks.value = tRes.data
      tombstoneCount.value = sRes.data.tombstone_count || 0
      defaultMaxCount.value = sRes.data.default_max_count || 0
      cookieStatuses.value = {
        xiaohongshu: cXhs.data as CookieStatus,
        douyin: cDy.data as CookieStatus,
      }
    } catch { message.error('加载失败') }
  }

  async function refreshTasks() {
    try {
      const tRes = await apiClient.get('/scraper/tasks', {
        params: {
          platform: taskFilterPlatform.value || undefined,
          status: taskFilterStatus.value || undefined,
          sort: taskSort.value,
          page: taskPage.value,
        },
      })
      tasks.value = tRes.data
    } catch { /* 轮询/静默刷新失败不提示，保持旧数据 */ }
  }

  /** 筛选或排序变化：回到第一页并刷新 */
  function onFilterChange() { taskPage.value = 1; refreshTasks() }

  // ===== 任务操作 =====
  async function cancelTask(taskId: number) {
    try {
      await apiClient.post(`/scraper/tasks/${taskId}/cancel`)
      message.success('已取消')
      refreshTasks()
    } catch (e: any) { message.error(e.response?.data?.detail || '取消失败') }
  }

  async function deleteSingleTask(taskId: number) {
    try {
      deletingTask.value = taskId
      const res = await apiClient.delete(`/scraper/tasks/${taskId}`)
      if (res.status === 200 || res.status === 204) {
        tasks.value = tasks.value.filter(t => t.id !== taskId)
        message.success('已删除')
      }
    } catch (e: any) {
      if (e.response?.status === 204) {
        tasks.value = tasks.value.filter(t => t.id !== taskId)
        message.success('已删除')
      } else message.error('删除失败: ' + (e.response?.data?.detail || ''))
    } finally { deletingTask.value = null }
  }

  async function clearAllTasks() {
    try {
      clearing.value = true
      await apiClient.delete('/scraper/tasks')
      tasks.value = []
      message.success('已清空')
    } catch { message.error('清空失败') } finally { clearing.value = false }
  }

  async function retryFailedTasks() {
    try {
      retrying.value = true
      message.success((await apiClient.post('/scraper/tasks/retry-failed')).data.message)
      refreshTasks()
      startPollIfNeeded()
    } catch (e: any) {
      message.info(e.response?.status === 404 ? '没有失败任务' : (e.response?.data?.detail || '重试失败'))
    } finally { retrying.value = false }
  }

  async function retrySingleTask(taskId: number) {
    try {
      retryingTask.value = taskId
      await apiClient.post(`/scraper/tasks/${taskId}/retry`)
      message.success('已重新加入队列（断点续采）')
      refreshTasks()
      startPollIfNeeded()
    } catch (e: any) {
      message.error(e.response?.data?.detail || '续采失败')
    } finally { retryingTask.value = null }
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
    }, 5000)
  }
  function stopPoll() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
  }

  // ===== 工具函数 =====
  async function copyText(text: string) {
    try { await navigator.clipboard.writeText(text); message.success('已复制') }
    catch {
      try {
        const ta = document.createElement('textarea'); ta.value = text
        ta.style.cssText = 'position:fixed;left:-9999px'; document.body.appendChild(ta)
        ta.select(); document.execCommand('copy'); document.body.removeChild(ta)
        message.success('已复制')
      } catch { message.error('复制失败') }
    }
  }

  function statusType(s: string): 'default' | 'info' | 'success' | 'error' | 'warning' {
    const m: Record<string, 'default' | 'info' | 'success' | 'error' | 'warning'> = {
      pending: 'default', running: 'info', completed: 'success', failed: 'error', cancelled: 'warning',
    }
    return m[s] || 'default'
  }

  function platformName(p: string) { return sources.value.find(s => s.platform === p)?.name || PLATFORM_LABELS[p] || p }

  function formatDate(d: string | null | undefined) {
    if (!d) return '-'
    try { const dt = new Date(d); return isNaN(dt.getTime()) ? '-' : dt.toLocaleString('zh-CN') } catch { return '-' }
  }

  function parseKeywords(c: string | null) {
    if (!c) return '-'
    try { return (JSON.parse(c).keywords || []).join(', ') || '-' } catch { return '-' }
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
    taskFilterStatus, taskSort, taskPage, deletingTask, clearing, retrying, retryingTask,
    taskStats, hasFailedTasks,
    loadAll, refreshTasks, onFilterChange,
    cancelTask, deleteSingleTask, clearAllTasks, retryFailedTasks, retrySingleTask,
    startPollIfNeeded, stopPoll, copyText,
    statusType, platformName, formatDate, parseKeywords, getTaskDuration,
  }
}
