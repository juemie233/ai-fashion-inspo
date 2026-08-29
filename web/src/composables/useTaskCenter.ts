/** 任务中心域：聚合任务队列与采集任务，统一筛选、分页、轮询与操作。 */

import { getApiErrorMessage } from '@/utils/apiError'
import { ref, computed, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import apiClient from '@/api/client'
import type { UnifiedTask, TaskEventPayload } from '@/types/task'
import { isTaskTerminalStatus } from '@/types/task'
import { usePolling } from '@/composables/usePolling'
import { subscribeWs, onWsReconnected, isWsConnected } from '@/composables/useWebSocket'
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
/** WS 已连接时的保底轮询间隔（毫秒）：推送驱动为主，低频轮询兜底 */
const WS_CONNECTED_POLL_MS = 30000

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

  /** 暂停运行中的标签网络分析任务（后端仅 tag_network_analyze 支持） */
  async function pauseTask(t: UnifiedTask) {
    try {
      const { data } = await apiClient.post<{ message?: string }>(`/tasks/${t.id}/pause`)
      Message.success(data?.message || '任务已暂停')
      loadTasks()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '暂停失败'))
    }
  }

  /** 恢复已暂停的标签网络分析任务（断点续算） */
  async function resumeTask(t: UnifiedTask) {
    try {
      const { data } = await apiClient.post<{ message?: string }>(`/tasks/${t.id}/resume`)
      Message.success(data?.message || '任务已恢复')
      loadTasks()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '恢复失败'))
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

  // ===== 轮询：有活动任务时定期刷新，无活动则自动停止 =====
  // WS 已连接时改为推送驱动（30s 低频轮询兜底），断开时回退 5s 轮询。

  const {
    start: startPoll,
    stop: stopPoll,
    running: pollRunning,
  } = usePolling({
    intervalMs: () => (isWsConnected() ? WS_CONNECTED_POLL_MS : POLL_INTERVAL_MS),
    immediate: false,
    callback: () => {
      if (hasActive.value) void loadTasks()
      else stopPoll()
    },
  })

  // WS 连接状态切换后重启轮询，使新间隔生效（usePolling 的间隔在 start 时解析一次）
  watch(isWsConnected, () => {
    if (!pollRunning.value) return
    stopPoll()
    startPoll()
  })

  // ===== WebSocket 推送：任务事件驱动即时更新 =====

  // 全量刷新去抖：终态/新任务事件触发，300ms 合并避免事件风暴下频繁拉取
  let reloadTimer: ReturnType<typeof setTimeout> | null = null
  function scheduleReload(delay = 300) {
    if (reloadTimer) clearTimeout(reloadTimer)
    reloadTimer = setTimeout(() => {
      reloadTimer = null
      void loadTasks()
    }, delay)
  }

  subscribeWs('task_event', (raw) => {
    const ev = raw as unknown as TaskEventPayload
    if (!ev || typeof ev.task_id !== 'number') return
    // 就地更新匹配的队列任务行（采集任务不走 task_event，仍靠自身轮询）
    const row = tasks.value.find((t) => t.source === 'queue' && t.id === ev.task_id)
    if (row) {
      if (typeof ev.progress === 'number') row.progress = ev.progress
      if (typeof ev.done === 'number') row.done = ev.done
      if (typeof ev.total === 'number') row.total = ev.total
      if (ev.status) row.status = ev.status
      if (ev.event === 'failed' && ev.error) row.error = ev.error
    }
    // 列表里没有该任务（新建）或已到终态：全量刷新校正计数与页码
    if (!row || isTaskTerminalStatus(ev.status)) scheduleReload()
  })

  // 断线重连成功：全量刷新一次，补齐断线期间漏掉的事件
  onWsReconnected(() => scheduleReload(0))

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
    pauseTask,
    resumeTask,
    retryFailedScraper,
    startPoll,
    stopPoll,
  }
}
