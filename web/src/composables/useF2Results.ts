/** f2「一键获取素材」结果浏览与审查：按批次清单拉取本批素材，支持筛选、勾选与审查动作。
 *
 * 归属口径：后端以该任务落盘的批次清单为准（f2 素材没有 scraper_task_id 可关联），
 * 列表里的状态是**当前库内状态**（在库/垃圾桶/已彻底删除 + 质量审核状态）。
 */

import { computed, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import apiClient from '@/api/client'
import { getApiErrorMessage } from '@/utils/apiError'
import type { TrashReason } from '@/api/inspirations'

/** 单条素材的审查状态：质量审核状态 / 垃圾桶 / 已彻底删除 */
export type F2ResultState = 'pending' | 'approved' | 'rejected' | 'trash' | 'gone'

/** 筛选口径（与后端 RESULT_FILTERS 对齐） */
export type F2ResultFilter = 'all' | F2ResultState

/** 结果条目（后端已把清单字段与库内现状合并好） */
export interface F2ResultItem {
  id: string
  state: F2ResultState
  quality_status: string | null
  media_type: string
  caption: string
  author: string
  hashtags: string[]
  file_path: string | null
  thumbnail_path: string | null
  is_favorite: boolean
  trash_reason: string | null
  source_platform_id: string
  created_at: string
}

/** 整批口径的计数（不受筛选影响） */
export interface F2ResultCounts {
  total: number
  live: number
  trash: number
  gone: number
  pending: number
  approved: number
  rejected: number
}

/** 来源任务的简要信息 */
export interface F2ResultTaskBrief {
  id: number
  status: string
  created_at: string
  updated_at: string
  error: string | null
  imported: number
  failed: number
  fetch_ok: number
  fetch_total: number
}

/** 结果面板每页加载数量（与后端 size 上限 200 对齐留余量） */
const PAGE_SIZE = 60

const EMPTY_COUNTS: F2ResultCounts = {
  total: 0,
  live: 0,
  trash: 0,
  gone: 0,
  pending: 0,
  approved: 0,
  rejected: 0,
}

/** 结果浏览与审查状态机（由 F2ResultsPanel 消费） */
export function useF2Results() {
  const openTaskId = ref<number | null>(null)
  const task = ref<F2ResultTaskBrief | null>(null)
  const batchId = ref('')
  const items = ref<F2ResultItem[]>([])
  const total = ref(0)
  const counts = ref<F2ResultCounts>({ ...EMPTY_COUNTS })
  const authors = ref<{ name: string; count: number }[]>([])
  const filter = ref<F2ResultFilter>('all')
  const authorFilter = ref('')
  const loading = ref(false)
  const acting = ref(false)
  const selectedIds = ref<Set<string>>(new Set())

  let page = 1
  const hasMore = computed(() => items.value.length < total.value)
  const selectedItems = computed(() => items.value.filter((i) => selectedIds.value.has(i.id)))
  /** 可移入垃圾桶（在库）与可还原（在垃圾桶）的选中项：按钮据此显示数量与禁用态。
   *  「已彻底删除」的既不能移入也不能还原，必须排除，否则动作会带上无效 ID。 */
  const selectedLive = computed(() =>
    selectedItems.value.filter((i) => i.state !== 'trash' && i.state !== 'gone'),
  )
  const selectedTrashed = computed(() => selectedItems.value.filter((i) => i.state === 'trash'))

  /** 拉取结果：append 为真时追加（加载更多），否则替换（切任务/切筛选） */
  async function fetchResults(taskId: number, targetPage: number, append: boolean) {
    loading.value = true
    try {
      const { data } = await apiClient.get(`/scraper/f2-tasks/${taskId}/results`, {
        params: {
          page: targetPage,
          size: PAGE_SIZE,
          state: filter.value,
          author: authorFilter.value || undefined,
        },
      })
      // 竞态防护：请求在途时用户切换了任务，旧响应直接丢弃
      if (openTaskId.value !== taskId) return
      const incoming = (data.items || []) as F2ResultItem[]
      // 一律赋新数组（不原地 push）：既不改写响应对象，也让追加保持响应式稳定
      if (append) {
        const existing = new Set(items.value.map((i) => i.id))
        items.value = [...items.value, ...incoming.filter((i) => !existing.has(i.id))]
      } else {
        items.value = [...incoming]
      }
      total.value = data.total || 0
      counts.value = data.counts || { ...EMPTY_COUNTS }
      authors.value = data.authors || []
      task.value = data.task || null
      batchId.value = data.batch_id || ''
      page = targetPage
    } catch (e) {
      // 带上后端 detail（批次清单缺失 / 素材 ID 非法等都有可操作的原因）
      Message.error(getApiErrorMessage(e, '加载本批素材失败'))
    } finally {
      loading.value = false
    }
  }

  /** 打开某任务的结果面板（同一任务再次打开则收起） */
  async function open(taskId: number) {
    if (openTaskId.value === taskId) {
      close()
      return
    }
    openTaskId.value = taskId
    items.value = []
    total.value = 0
    counts.value = { ...EMPTY_COUNTS }
    selectedIds.value = new Set()
    filter.value = 'all'
    authorFilter.value = ''
    await fetchResults(taskId, 1, false)
  }

  function close() {
    openTaskId.value = null
    items.value = []
    total.value = 0
    counts.value = { ...EMPTY_COUNTS }
    authors.value = []
    batchId.value = ''
    task.value = null
    selectedIds.value = new Set()
    page = 1
  }

  /** 切换筛选：回到第一页并清空勾选（避免勾选项在新筛选下不可见却被操作） */
  async function reload() {
    if (openTaskId.value === null) return
    selectedIds.value = new Set()
    await fetchResults(openTaskId.value, 1, false)
  }

  async function setFilter(next: F2ResultFilter) {
    filter.value = next
    await reload()
  }

  async function setAuthor(next: string) {
    authorFilter.value = next
    await reload()
  }

  async function loadMore() {
    if (openTaskId.value === null || loading.value || !hasMore.value) return
    await fetchResults(openTaskId.value, page + 1, true)
  }

  function toggleSelect(id: string) {
    const next = new Set(selectedIds.value)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    selectedIds.value = next
  }

  /** 全选/取消全选：只作用于已加载且可操作的条目（已彻底删除的不可操作） */
  function selectAllLoaded() {
    const selectable = items.value.filter((i) => i.state !== 'gone').map((i) => i.id)
    const allSelected = selectable.length > 0 && selectable.every((id) => selectedIds.value.has(id))
    selectedIds.value = allSelected ? new Set() : new Set(selectable)
  }

  /** 审查动作共同的收尾：清空勾选、按当前筛选重新拉取（计数与现状都会变） */
  async function afterAction() {
    selectedIds.value = new Set()
    if (openTaskId.value === null) return
    await fetchResults(openTaskId.value, 1, false)
  }

  async function trashSelected(reason: TrashReason) {
    const targets = selectedLive.value.map((i) => i.id)
    if (!targets.length) {
      Message.warning('选中的素材都不在库（已在垃圾桶或已彻底删除）')
      return
    }
    acting.value = true
    try {
      const { data } = await apiClient.post(`/scraper/f2-tasks/${openTaskId.value}/results/trash`, {
        ids: targets,
        reason,
      })
      const { trashed, skipped } = data as { trashed: number; skipped: number }
      Message.success(
        skipped ? `已移入垃圾桶 ${trashed} 个（${skipped} 个跳过）` : `已移入垃圾桶 ${trashed} 个`,
      )
      await afterAction()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '移入垃圾桶失败'))
    } finally {
      acting.value = false
    }
  }

  async function restoreSelected() {
    const targets = selectedTrashed.value.map((i) => i.id)
    if (!targets.length) {
      Message.warning('选中的素材都不在垃圾桶里')
      return
    }
    acting.value = true
    try {
      const { data } = await apiClient.post(
        `/scraper/f2-tasks/${openTaskId.value}/results/restore`,
        { ids: targets },
      )
      const { restored, skipped } = data as { restored: number; skipped: number }
      Message.success(
        skipped ? `已还原 ${restored} 个（${skipped} 个跳过）` : `已还原 ${restored} 个`,
      )
      await afterAction()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '还原失败'))
    } finally {
      acting.value = false
    }
  }

  async function deleteSelected() {
    const targets = selectedItems.value.map((i) => i.id)
    if (!targets.length) return
    acting.value = true
    try {
      const { data } = await apiClient.post(
        `/scraper/f2-tasks/${openTaskId.value}/results/delete`,
        { ids: targets },
      )
      const { count, task_id: deleteTaskId } = data as { count: number; task_id: number }
      Message.success(
        `已提交彻底删除任务 #${deleteTaskId}（共 ${count} 个，不可恢复）；` +
          'worker 执行完成后点「刷新」即可看到「已彻底删除」状态',
      )
      await afterAction()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '提交彻底删除失败'))
    } finally {
      acting.value = false
    }
  }

  /**
   * 手工补登记本批未绑定博主的来源作者（幂等，可重复点）。
   *
   * 自动路径在「我的喜欢」入库后（未登记的来源作者自动建成抖音博主并绑定素材）；
   * 这个入口用于老批次、或建任务时关掉了自动登记的情况。补建的博主标记为
   * 「自动登记」，不算已登记博主、不进一键获取素材的下载白名单。
   */
  async function registerBloggers() {
    if (openTaskId.value === null) return
    acting.value = true
    try {
      const { data } = await apiClient.post(
        `/scraper/f2-tasks/${openTaskId.value}/results/register-bloggers`,
      )
      const { created, reused, linked, ambiguous } = data as {
        created: number
        reused: number
        linked: number
        ambiguous: number
      }
      const parts = [
        created ? `新建博主 ${created} 个` : '',
        linked ? `绑定素材 ${linked} 条` : '',
        reused ? `复用已有博主 ${reused} 个` : '',
        ambiguous ? `${ambiguous} 个同名多候选已跳过（去博主管理手工绑定）` : '',
      ].filter(Boolean)
      const text = parts.join(' · ') || '本批没有需要登记的来源作者'
      if (created || linked || reused) Message.success(text)
      else Message.info(text)
      await afterAction()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '登记来源作者博主失败'))
    } finally {
      acting.value = false
    }
  }

  return {
    // 状态
    openTaskId,
    task,
    batchId,
    items,
    total,
    counts,
    authors,
    filter,
    authorFilter,
    loading,
    acting,
    selectedIds,
    hasMore,
    selectedItems,
    selectedLive,
    selectedTrashed,
    // 操作
    open,
    close,
    reload,
    setFilter,
    setAuthor,
    loadMore,
    toggleSelect,
    selectAllLoaded,
    trashSelected,
    restoreSelected,
    deleteSelected,
    registerBloggers,
  }
}
