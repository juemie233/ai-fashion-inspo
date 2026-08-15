/** 任务中心域：聚合任务队列与采集任务，统一筛选、分页、轮询与操作。 */

import { ref, computed } from 'vue'
import { useMessage } from 'naive-ui'
import apiClient from '@/api/client'
import type { UnifiedTask } from '@/types/task'
import {
  TASK_TYPE_LABELS,
  SCRAPER_PLATFORM_LABELS,
  normalizeTaskStatus,
} from '@/utils/taskLabel'

/** 任务队列原始条目（/api/tasks 返回项） */
interface QueueTask {
  id: number
  type: string
  status: string
  progress: number
  total: number
  done: number
  error: string | null
  created_at: string
  updated_at: string
}

/** 采集任务原始条目（/api/scraper/tasks 返回项） */
interface ScraperTaskRaw {
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

/** 每页条数（客户端分页） */
const PAGE_SIZE = 20

export function useTaskCenter() {
  const message = useMessage()

  const tasks = ref<UnifiedTask[]>([])
  const loading = ref(false)
  const statusFilter = ref('')
  const typeFilter = ref('')
  const page = ref(1)
  const retrying = ref(false)

  // ===== 归一化 =====

  function normalizeQueueTask(t: QueueTask): UnifiedTask {
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
      title: TASK_TYPE_LABELS[t.type] || t.type,
      detail: t.error || '',
      error: t.error,
      created_at: t.created_at,
      finished_at: finished ? t.updated_at : null,
    }
  }

  function normalizeScraperTask(t: ScraperTaskRaw): UnifiedTask {
    const status = normalizeTaskStatus(t.status)
    const platform = SCRAPER_PLATFORM_LABELS[t.platform] || t.platform
    const keywords = parseKeywords(t.config)
    return {
      id: t.id,
      source: 'scraper',
      type: 'scraper',
      platform: t.platform,
      status,
      progress: -1,
      total: t.items_found,
      done: t.items_added,
      title: `${platform}采集`,
      detail: [keywords, t.error].filter(Boolean).join(' · ') || '',
      error: t.error,
      created_at: t.created_at,
      finished_at: t.finished_at,
    }
  }

  function parseKeywords(config: string | null): string {
    if (!config) return ''
    try {
      const obj = JSON.parse(config)
      const kw = obj?.keywords
      return Array.isArray(kw) && kw.length ? `关键词：${kw.join('、')}` : ''
    } catch {
      return ''
    }
  }

  // ===== 数据加载 =====

  async function loadTasks() {
    loading.value = true
    try {
      const [qRes, sRes] = await Promise.all([
        apiClient.get('/tasks', { params: { size: 200 } }),
        apiClient.get('/scraper/tasks', { params: { sort: 'newest', size: 200 } }),
      ])
      const queue = (qRes.data.items || []).map(normalizeQueueTask)
      const scraper = (Array.isArray(sRes.data) ? sRes.data : []).map(normalizeScraperTask)
      tasks.value = [...queue, ...scraper].sort(
        (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
      )
    } catch {
      message.error('加载任务失败')
    } finally {
      loading.value = false
    }
  }

  // ===== 筛选与分页 =====

  const filtered = computed(() =>
    tasks.value.filter((t) => {
      if (statusFilter.value && t.status !== statusFilter.value) return false
      if (typeFilter.value && t.type !== typeFilter.value) return false
      return true
    }),
  )

  const total = computed(() => filtered.value.length)
  const pageCount = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
  const pageItems = computed(() => {
    const start = (page.value - 1) * PAGE_SIZE
    return filtered.value.slice(start, start + PAGE_SIZE)
  })

  const hasActive = computed(() =>
    tasks.value.some((t) => t.status === 'pending' || t.status === 'running'),
  )
  const hasFailedScraper = computed(() =>
    tasks.value.some((t) => t.source === 'scraper' && t.status === 'failed'),
  )

  function onFilterChange() {
    page.value = 1
  }

  // ===== 操作 =====

  async function cancelTask(t: UnifiedTask) {
    const url = t.source === 'queue' ? `/tasks/${t.id}/cancel` : `/scraper/tasks/${t.id}/cancel`
    try {
      await apiClient.post(url)
      message.success('已取消')
      loadTasks()
    } catch (e: any) {
      message.error(e.response?.data?.detail || '取消失败')
    }
  }

  async function deleteTask(t: UnifiedTask) {
    if (t.source !== 'scraper') return
    try {
      await apiClient.delete(`/scraper/tasks/${t.id}`)
      message.success('已删除')
      loadTasks()
    } catch (e: any) {
      message.error(e.response?.data?.detail || '删除失败')
    }
  }

  async function retryFailedScraper() {
    try {
      retrying.value = true
      const { data } = await apiClient.post('/scraper/tasks/retry-failed')
      message.success(data.message || '已重试')
      loadTasks()
    } catch (e: any) {
      message.info(e.response?.status === 404 ? '没有失败任务' : e.response?.data?.detail || '重试失败')
    } finally {
      retrying.value = false
    }
  }

  // ===== 轮询：有活动任务时每 3 秒刷新一次 =====

  let pollTimer: ReturnType<typeof setInterval> | null = null
  function startPoll() {
    if (pollTimer) return
    pollTimer = setInterval(() => {
      if (hasActive.value) loadTasks()
      else stopPoll()
    }, 3000)
  }
  function stopPoll() {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  return {
    tasks,
    loading,
    statusFilter,
    typeFilter,
    page,
    pageCount,
    retrying,
    total,
    pageItems,
    hasActive,
    hasFailedScraper,
    loadTasks,
    onFilterChange,
    cancelTask,
    deleteTask,
    retryFailedScraper,
    startPoll,
    stopPoll,
  }
}
