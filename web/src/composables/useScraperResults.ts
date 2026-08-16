/** 采集任务结果预览域：结果加载（分页追加）、勾选与批量删除。 */

import { ref, computed } from 'vue'
import { useMessage } from 'naive-ui'
import apiClient from '@/api/client'

interface ScraperResultsDeps {
  /** 删除后刷新任务列表（数量变化） */
  refreshTasks: () => void
}

/** 结果预览每页加载数量 */
const RESULTS_PAGE_SIZE = 100

/** 任务结果预览状态与操作，由 ScraperView 消费。 */
export function useScraperResults(deps: ScraperResultsDeps) {
  const message = useMessage()

  const resultsTaskId = ref<number | null>(null)
  const resultsItems = ref<any[]>([])
  const resultsTotal = ref(0)
  const resultsLoading = ref(false)
  const resultsPage = ref(1)
  const selectedIds = ref<Set<string>>(new Set())
  const deletingResults = ref(false)

  /** 是否还有更多结果可加载 */
  const hasMoreResults = computed(() => resultsItems.value.length < resultsTotal.value)

  /** 拉取指定任务某一页的结果；append 为真时追加去重，否则替换 */
  async function fetchResults(taskId: number, page: number, append: boolean) {
    resultsLoading.value = true
    try {
      const r = await apiClient.get(`/scraper/tasks/${taskId}/results`, {
        params: { page, size: RESULTS_PAGE_SIZE },
      })
      // 竞态防护：加载更多在途时用户切换了任务，旧任务的响应直接丢弃
      if (resultsTaskId.value !== taskId) return
      if (append) {
        const existing = new Set(resultsItems.value.map((i: any) => i.id))
        for (const item of r.data.items) {
          if (!existing.has(item.id)) resultsItems.value.push(item)
        }
      } else {
        resultsItems.value = r.data.items
      }
      resultsTotal.value = r.data.total
    } catch { message.error('加载失败') }
    finally { resultsLoading.value = false }
  }

  /** 打开/收起结果预览：再次点击同一任务则收起 */
  async function viewResults(taskId: number) {
    if (resultsTaskId.value === taskId) {
      resultsTaskId.value = null
      resultsItems.value = []
      resultsTotal.value = 0
      resultsPage.value = 1
      selectedIds.value = new Set()
      return
    }
    resultsTaskId.value = taskId
    resultsPage.value = 1
    selectedIds.value = new Set()
    await fetchResults(taskId, 1, false)
  }

  /** 加载更多：追加下一页（跨页勾选状态保留） */
  async function loadMoreResults() {
    if (resultsLoading.value || !hasMoreResults.value || resultsTaskId.value === null) return
    resultsPage.value += 1
    await fetchResults(resultsTaskId.value, resultsPage.value, true)
  }

  function toggleSelect(id: string) {
    const n = new Set(selectedIds.value)
    n.has(id) ? n.delete(id) : n.add(id)
    selectedIds.value = n
  }

  /** 全选/取消全选：作用于所有已加载的结果 */
  function selectAll() {
    const loadedIds = resultsItems.value.map((i: any) => i.id)
    const allSelected = loadedIds.every((id) => selectedIds.value.has(id))
    selectedIds.value = allSelected ? new Set() : new Set(loadedIds)
  }

  async function deleteSelected() {
    if (!selectedIds.value.size || resultsTaskId.value === null) return
    deletingResults.value = true
    try {
      const r = await apiClient.post(`/scraper/tasks/${resultsTaskId.value}/results/batch-delete`, { ids: [...selectedIds.value] })
      message.success(`已删除 ${r.data.deleted_count} 个`)
      resultsItems.value = resultsItems.value.filter((i: any) => !selectedIds.value.has(i.id))
      selectedIds.value = new Set()
      resultsTotal.value = r.data.remaining
      // 全部删完则收起面板；当前页删空但仍有剩余时回到第一页重新加载
      if (!r.data.remaining) {
        resultsTaskId.value = null
        resultsItems.value = []
        resultsPage.value = 1
      } else if (resultsItems.value.length === 0) {
        resultsPage.value = 1
        await fetchResults(resultsTaskId.value, 1, false)
      }
      deps.refreshTasks()
    } catch { message.error('删除失败') } finally { deletingResults.value = false }
  }

  return {
    resultsTaskId, resultsItems, resultsTotal, resultsLoading, selectedIds, deletingResults,
    hasMoreResults,
    viewResults, loadMoreResults, toggleSelect, selectAll, deleteSelected,
  }
}
