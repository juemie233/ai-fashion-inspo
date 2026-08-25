/** 博主主页信息补全逻辑：缺失列表勾选、任务提交与轮询、失败重试、跳过/解除跳过管理。 */

import { computed, onUnmounted, reactive, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { getApiErrorMessage } from '@/utils/apiError'
import apiClient from '@/api/client'
import {
  enrichMissingProfiles,
  fetchEnrichSkips,
  fetchMissingProfiles,
  skipEnrichBloggers,
  unskipEnrichBloggers,
  type EnrichSkipItem,
  type MissingProfileBlogger,
} from '@/api/persons'

/** 补全任务状态（/api/tasks/{id} 结构） */
export interface EnrichTaskState {
  id: number
  status: string
  progress: number
  done: number
  total: number
  error: string | null
  result: Record<string, unknown> | null
}

interface Options {
  /** 弹窗关闭（完成/取消）后回调：父级刷新缺失计数与列表 */
  onFinished?: () => void
}

export function useBloggerEnrich({ onFinished }: Options = {}) {
  /** 缺失博主列表（弹窗内勾选范围，默认全选） */
  const missingItems = ref<MissingProfileBlogger[]>([])
  /** 勾选的博主 ID */
  const selectedMissingIds = ref<Set<number>>(new Set())
  const enrichBusy = ref(false)
  /** 补全任务状态（轮询展示） */
  const enrichTask = ref<EnrichTaskState | null>(null)
  let enrichPollTimer: number | null = null

  /** 打开补全弹窗：拉取缺失博主列表并默认全选 */
  async function openEnrichModal() {
    enrichTask.value = null
    try {
      const data = await fetchMissingProfiles()
      missingItems.value = data.items
      selectedMissingIds.value = new Set(data.items.map((b) => b.id))
    } catch (e) {
      Message.error(getApiErrorMessage(e, '加载缺失博主失败'))
    }
  }

  function toggleMissing(id: number, checked: boolean) {
    const next = new Set(selectedMissingIds.value)
    if (checked) {
      next.add(id)
    } else {
      next.delete(id)
    }
    selectedMissingIds.value = next
  }

  /** 轮询补全任务直到终态（2s 间隔） */
  async function pollEnrichTask(taskId: number) {
    while (true) {
      const { data } = await apiClient.get<EnrichTaskState>(`/tasks/${taskId}`)
      enrichTask.value = data
      if (!['pending', 'running'].includes(data.status)) return
      await new Promise((r) => setTimeout(r, 2000))
    }
  }

  /** 开始补全勾选的博主 */
  async function startEnrich() {
    const ids = [...selectedMissingIds.value]
    if (ids.length === 0) {
      Message.warning('请至少勾选一位博主')
      return
    }
    enrichBusy.value = true
    enrichTask.value = null
    try {
      const data = await enrichMissingProfiles(ids)
      Message.success(data.message)
      await pollEnrichTask(data.task_id)
    } catch (e) {
      Message.error(getApiErrorMessage(e, '创建补全任务失败'))
    } finally {
      enrichBusy.value = false
    }
  }

  /** 失败博主单独重试（用任务结果中的失败 blogger_id 发起新任务） */
  async function retryFailed() {
    const results =
      (
        enrichTask.value?.result as {
          results?: Array<{ blogger_id: number; status: string }>
        } | null
      )?.results ?? []
    const failedIds = results.filter((r) => r.status === 'failed').map((r) => r.blogger_id)
    if (failedIds.length === 0) {
      Message.info('没有失败的博主')
      return
    }
    enrichBusy.value = true
    enrichTask.value = null
    try {
      const data = await enrichMissingProfiles(failedIds)
      Message.success(data.message)
      await pollEnrichTask(data.task_id)
    } catch (e) {
      Message.error(getApiErrorMessage(e, '创建重试任务失败'))
    } finally {
      enrichBusy.value = false
    }
  }

  /** 关闭弹窗 */
  function closeEnrich() {
    enrichTask.value = null
    onFinished?.()
  }

  /** 补全任务结果明细（供模板展示失败原因） */
  const enrichResults = computed(
    () =>
      (
        enrichTask.value?.result as {
          results?: Array<{ blogger_id: number; name: string; status: string; reason?: string }>
        } | null
      )?.results ?? [],
  )
  const enrichUpdated = computed(
    () => (enrichTask.value?.result as { updated?: number } | null)?.updated ?? 0,
  )
  const enrichSkipped = computed(
    () => (enrichTask.value?.result as { skipped?: number } | null)?.skipped ?? 0,
  )
  const enrichFailed = computed(
    () => (enrichTask.value?.result as { failed?: number } | null)?.failed ?? 0,
  )
  /** 临时性失败（可重试）；确定性失败已自动跳过 */
  const enrichFailedItems = computed(() => enrichResults.value.filter((r) => r.status === 'failed'))
  /** 本次自动跳过的（确定性无法获取，展示原因） */
  const enrichSkippedItems = computed(() =>
    enrichResults.value.filter((r) => r.status === 'skipped'),
  )

  // ── 跳过管理（确定性无法补全的博主，可解除重新纳入）──
  const skipManageOpen = ref(false)
  const skippedItems = ref<EnrichSkipItem[]>([])
  const skipBusy = ref(false)

  async function loadSkipList() {
    try {
      const data = await fetchEnrichSkips()
      skippedItems.value = data.items
    } catch {
      // 加载失败静默
    }
  }

  /** 手动跳过指定博主（任务结果里的失败项） */
  async function skipFailedBloggers(ids: number[], reason = '手动跳过（无法获取信息）') {
    if (ids.length === 0) return
    skipBusy.value = true
    try {
      const r = await skipEnrichBloggers(ids, reason)
      Message.success(`已跳过 ${r.skipped} 位博主（解除后可重新纳入）`)
      await loadSkipList()
      onFinished?.()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '跳过失败'))
    } finally {
      skipBusy.value = false
    }
  }

  /** 解除跳过（重新纳入补全范围） */
  async function unskipBloggers(ids: number[]) {
    if (ids.length === 0) return
    skipBusy.value = true
    try {
      const r = await unskipEnrichBloggers(ids)
      Message.success(`已解除 ${r.unskipped} 位博主（重新纳入补全范围）`)
      await loadSkipList()
      onFinished?.()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '解除失败'))
    } finally {
      skipBusy.value = false
    }
  }

  onUnmounted(() => {
    if (enrichPollTimer !== null) {
      window.clearInterval(enrichPollTimer)
      enrichPollTimer = null
    }
  })

  // 返回 reactive 对象：供组件模板整体使用（嵌套 ref 自动解包，赋值自动写回 .value）
  return reactive({
    missingItems,
    selectedMissingIds,
    enrichBusy,
    enrichTask,
    openEnrichModal,
    toggleMissing,
    startEnrich,
    retryFailed,
    closeEnrich,
    enrichUpdated,
    enrichSkipped,
    enrichFailed,
    enrichFailedItems,
    enrichSkippedItems,
    skipManageOpen,
    skippedItems,
    skipBusy,
    loadSkipList,
    skipFailedBloggers,
    unskipBloggers,
  })
}
