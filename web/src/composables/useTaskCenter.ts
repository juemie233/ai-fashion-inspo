/** 任务中心域：聚合任务队列与采集任务，统一筛选、分页、轮询与操作。 */

import { getApiErrorMessage } from '@/utils/apiError'
import { ref, computed, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import apiClient from '@/api/client'
import type { UnifiedTask, TaskEventPayload } from '@/types/task'
import { isTaskTerminalStatus } from '@/types/task'
import { usePolling } from '@/composables/usePolling'
import { useTaskActions } from '@/composables/useTaskActions'
import { subscribeWs, onWsReconnected, isWsConnected } from '@/composables/useWebSocket'
import {
  compareUnifiedTasks,
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
      // 排序口径见 compareUnifiedTasks：已暂停的最前（等着用户处理），其余最新在前
      tasks.value = [...queue, ...scraper].sort(compareUnifiedTasks)
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
  // 四个操作收敛在 useTaskActions（采集管理页的抖音采集历史复用同一份实现）

  const { cancelTask, deleteTask, pauseTask, resumeTask } = useTaskActions({
    onQueueTaskDeleted: (t) => {
      // 本地先移除该行即时反馈（无需整页刷新），随后全量刷新校正页码
      tasks.value = tasks.value.filter((x) => !(x.source === 'queue' && x.id === t.id))
      page.value = Math.min(page.value, Math.max(1, pageCount.value))
    },
    reload: loadTasks,
  })

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
