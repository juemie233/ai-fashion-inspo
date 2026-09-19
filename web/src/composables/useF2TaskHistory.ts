/** 抖音「一键获取素材」（f2_import）任务历史：只取任务队列里该类型的历史。
 *
 * 为什么单独一份：采集管理页要把「抖音（f2 增量下载）」与下方「CDP 采集」的历史
 * 分开看——任务中心那份是聚合视图（队列任务 + 采集任务混排，见 useTaskCenter）。
 *
 * 数据来源：GET /tasks?type=f2_import（服务端按类型过滤 + 分页）。操作与任务中心
 * 共用 useTaskActions，行为与文案完全一致。
 */

import { computed, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import apiClient from '@/api/client'
import { getApiErrorMessage } from '@/utils/apiError'
import type { TaskEventPayload, UnifiedTask } from '@/types/task'
import { normalizeQueueTask, type QueueTask } from '@/utils/taskPresentation'
import { usePolling } from '@/composables/usePolling'
import { useTaskActions } from '@/composables/useTaskActions'
import { subscribeWs } from '@/composables/useWebSocket'

/** 每页条数（服务端分页） */
export const F2_HISTORY_PAGE_SIZE = 10

/** 有任务进行中时的进度轮询间隔（毫秒） */
const POLL_INTERVAL_MS = 5000

export function useF2TaskHistory() {
  const tasks = ref<UnifiedTask[]>([])
  const loading = ref(false)
  const total = ref(0)
  const page = ref(1)
  /** 有批次清单可浏览的任务 id（结果里 import.batch_file 非空才有「查看结果」入口） */
  const resultTaskIds = ref<Set<number>>(new Set())

  const pageCount = computed(() => Math.max(1, Math.ceil(total.value / F2_HISTORY_PAGE_SIZE)))
  const hasActive = computed(() =>
    tasks.value.some((t) => t.status === 'pending' || t.status === 'running'),
  )

  /** 该任务是否有可浏览的批次结果：入库阶段落盘了批次清单且确实入了素材 */
  function hasBatchResults(result: Record<string, unknown> | null): boolean {
    const summary = result?.import
    if (!summary || typeof summary !== 'object') return false
    const { batch_file: batchFile, imported } = summary as {
      batch_file?: unknown
      imported?: unknown
    }
    return Boolean(batchFile) && Number(imported ?? 0) > 0
  }

  /**
   * 拉取当前页历史，返回是否成功（**不向上抛**）。
   *
   * 不抛的理由：调用点既有轮询（失败要停轮询）、也有任务操作后的刷新
   * （useTaskActions 把 reload 放在 try 里，抛出会让「取消成功」被误报成
   * 「取消失败」），还有即发即忘的翻页/刷新按钮（抛出会变成未处理的 rejection）。
   * 成败用返回值表达，由调用方决定后续动作。
   *
   * @param options.silent 静默刷新（轮询 / WS / 操作后刷新）：不 loading、不弹错
   */
  async function loadTasks(options: { silent?: boolean } = {}): Promise<boolean> {
    if (!options.silent) loading.value = true
    try {
      const { data } = await apiClient.get<{ items: QueueTask[]; total: number }>('/tasks', {
        params: { type: 'f2_import', page: page.value, size: F2_HISTORY_PAGE_SIZE },
      })
      const items = data.items || []
      tasks.value = items.map(normalizeQueueTask)
      resultTaskIds.value = new Set(items.filter((t) => hasBatchResults(t.result)).map((t) => t.id))
      total.value = data.total || 0
      // 记录减少后当前页可能越界，回退到最后一页
      page.value = Math.min(page.value, pageCount.value)
      return true
    } catch (e) {
      if (!options.silent) Message.error(getApiErrorMessage(e, '加载抖音采集历史失败'))
      return false
    } finally {
      loading.value = false
    }
  }

  function onPageChange(next: number) {
    page.value = next
    void loadTasks()
  }

  // 有任务在跑时轮询进度；跑完或空闲即停（新任务靠 WS 事件与首次加载兜底）。
  // 轮询失败（后端不可达）也停：继续每 5 秒打一次没有意义，等 WS 事件或下一次操作再启。
  const { start: startPoll, stop: stopPoll } = usePolling({
    intervalMs: POLL_INTERVAL_MS,
    immediate: false,
    callback: async () => {
      if (!hasActive.value) {
        stopPoll()
        return
      }
      if (!(await loadTasks({ silent: true }))) stopPoll()
    },
  })
  watch(hasActive, (active) => {
    if (active) startPoll()
    else stopPoll()
  })

  // WS 任务事件（新建 / 进度 / 终态）下做一次去抖刷新：
  // 后端自动调度创建的 f2 任务也能及时出现在这份历史里
  let reloadTimer: ReturnType<typeof setTimeout> | null = null
  subscribeWs('task_event', (raw) => {
    const ev = raw as unknown as TaskEventPayload
    if (!ev || typeof ev.task_id !== 'number') return
    if (reloadTimer) clearTimeout(reloadTimer)
    reloadTimer = setTimeout(() => {
      reloadTimer = null
      // 静默刷新：失败不弹错（WS 事件驱动的刷新不该打断用户）
      void loadTasks({ silent: true })
    }, 300)
  })

  const { cancelTask, deleteTask, pauseTask, resumeTask } = useTaskActions({
    onQueueTaskDeleted: () => {
      // 物理删除后页码可能变化，重新拉当前页
      void loadTasks({ silent: true })
    },
    reload: () => void loadTasks({ silent: true }),
  })

  return {
    tasks,
    loading,
    total,
    page,
    pageCount,
    hasActive,
    resultTaskIds,
    loadTasks,
    onPageChange,
    startPoll,
    stopPoll,
    cancelTask,
    deleteTask,
    pauseTask,
    resumeTask,
  }
}
