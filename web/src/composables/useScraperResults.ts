/** 采集任务结果预览域：结果加载、勾选与批量删除。 */

import { ref } from 'vue'
import { useMessage } from 'naive-ui'
import apiClient from '@/api/client'

interface ScraperResultsDeps {
  /** 删除后刷新任务列表（数量变化） */
  refreshTasks: () => void
}

/** 任务结果预览状态与操作，由 ScraperView 消费。 */
export function useScraperResults(deps: ScraperResultsDeps) {
  const message = useMessage()

  const resultsTaskId = ref<number | null>(null)
  const resultsItems = ref<any[]>([])
  const resultsTotal = ref(0)
  const resultsLoading = ref(false)
  const selectedIds = ref<Set<string>>(new Set())
  const deletingResults = ref(false)

  /** 打开/收起结果预览：再次点击同一任务则收起 */
  async function viewResults(taskId: number) {
    if (resultsTaskId.value === taskId) { resultsTaskId.value = null; resultsItems.value = []; selectedIds.value = new Set(); return }
    resultsTaskId.value = taskId
    resultsLoading.value = true
    selectedIds.value = new Set()
    try {
      const r = await apiClient.get(`/scraper/tasks/${taskId}/results`, { params: { size: 200 } })
      resultsItems.value = r.data.items
      resultsTotal.value = r.data.total
    } catch { message.error('加载失败'); resultsTaskId.value = null }
    finally { resultsLoading.value = false }
  }

  function toggleSelect(id: string) {
    const n = new Set(selectedIds.value)
    n.has(id) ? n.delete(id) : n.add(id)
    selectedIds.value = n
  }

  function selectAll() {
    selectedIds.value = selectedIds.value.size === resultsItems.value.length
      ? new Set()
      : new Set(resultsItems.value.map((i: any) => i.id))
  }

  async function deleteSelected() {
    if (!selectedIds.value.size) return
    deletingResults.value = true
    try {
      const r = await apiClient.post(`/scraper/tasks/${resultsTaskId.value}/results/batch-delete`, { ids: [...selectedIds.value] })
      message.success(`已删除 ${r.data.deleted_count} 个`)
      resultsItems.value = resultsItems.value.filter((i: any) => !selectedIds.value.has(i.id))
      selectedIds.value = new Set()
      resultsTotal.value = r.data.remaining
      if (!r.data.remaining) { resultsTaskId.value = null; resultsItems.value = [] }
      deps.refreshTasks()
    } catch { message.error('删除失败') } finally { deletingResults.value = false }
  }

  return {
    resultsTaskId, resultsItems, resultsTotal, resultsLoading, selectedIds, deletingResults,
    viewResults, toggleSelect, selectAll, deleteSelected,
  }
}
