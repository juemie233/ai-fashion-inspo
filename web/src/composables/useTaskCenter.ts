/** 任务中心域：聚合任务队列与采集任务，统一筛选、分页、轮询与操作。 */

import { getApiErrorMessage } from '@/utils/apiError'
import { ref, computed } from 'vue'
import { Message } from '@arco-design/web-vue'
import apiClient from '@/api/client'
import type { UnifiedTask } from '@/types/task'
import { usePolling } from '@/composables/usePolling'
import {
  normalizeQueueTask,
  normalizeScraperTask,
  type QueueTask,
  type ScraperTaskRaw,
} from '@/utils/taskPresentation'

/** 每页条数（客户端分页） */
const PAGE_SIZE = 20

/** 任务轮询间隔（毫秒）：有活动任务时每 5 秒刷新一次 */
const POLL_INTERVAL_MS = 5000

export function useTaskCenter() {
  const tasks = ref<UnifiedTask[]>([])
  const loading = ref(false)
  const statusFilter = ref('')
  const typeFilter = ref('')
  const page = ref(1)
  const retrying = ref(false)

  // ===== 数据加载 =====

  async function loadTasks() {
    loading.value = true
    try {
      const [qRes, sRes] = await Promise.all([
        apiClient.get<{ items: QueueTask[] }>('/tasks', { params: { size: 200 } }),
        apiClient.get<{ items: ScraperTaskRaw[] }>('/scraper/tasks', {
          params: { sort: 'newest', size: 200 },
        }),
      ])
      const queue = (qRes.data.items || []).map(normalizeQueueTask)
      const scraper = (sRes.data.items || []).map(normalizeScraperTask)
      tasks.value = [...queue, ...scraper].sort(
        (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
      )
      // 任务数量缩减后页码可能超出总页数，回退到最后一页
      page.value = Math.min(page.value, Math.max(1, pageCount.value))
    } catch {
      Message.error('加载任务失败')
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
      const { data } = await apiClient.post<{ message?: string; deleted?: boolean }>(url)
      // 队列任务 pending 取消 = 后端物理删除（deleted: true）；运行中取消仅标记 cancelled
      const deleted = data?.deleted === true
      Message.success(data?.message || (deleted ? '任务已删除' : '已取消'))
      if (deleted) {
        // 本地先移除该行即时反馈（无需整页刷新），随后全量刷新校正页码
        tasks.value = tasks.value.filter((x) => !(x.source === 'queue' && x.id === t.id))
        page.value = Math.min(page.value, Math.max(1, pageCount.value))
      }
      await loadTasks()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '取消失败'))
    }
  }

  async function deleteTask(t: UnifiedTask) {
    try {
      // 采集任务记录在 scraper_tasks 表，走采集专用删除接口；队列任务走通用删除接口
      const url = t.source === 'scraper' ? `/scraper/tasks/${t.id}` : `/tasks/${t.id}`
      await apiClient.delete(url)
      Message.success('已删除')
      loadTasks()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '删除失败'))
    }
  }

  async function retryFailedScraper() {
    try {
      retrying.value = true
      const { data } = await apiClient.post('/scraper/tasks/retry-failed')
      Message.success(data.message || '已重试')
      loadTasks()
    } catch (e) {
      const is404 = (e as { response?: { status?: number } })?.response?.status === 404
      Message.info(is404 ? '没有失败任务' : getApiErrorMessage(e, '重试失败'))
    } finally {
      retrying.value = false
    }
  }

  // ===== 轮询：有活动任务时每 5 秒刷新一次，无活动则自动停止 =====

  const { start: startPoll, stop: stopPoll } = usePolling({
    intervalMs: POLL_INTERVAL_MS,
    immediate: false,
    callback: () => {
      if (hasActive.value) void loadTasks()
      else stopPoll()
    },
  })

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
