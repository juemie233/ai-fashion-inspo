/** 素材库首页的动作处理器（从 HomeView.vue 抽出，C-2）。
 *
 * 只做「调 API / 改 store / 弹提示」，不碰模板；依赖由调用方以 opts 传入。
 */

import { ref, type Ref } from 'vue'
import { Message, Notification } from '@arco-design/web-vue'
import type { useInspirationsStore } from '@/stores/inspirations'
import type { useTagsStore } from '@/stores/tags'
import { getApiErrorMessage } from '@/utils/apiError'
import {
  batchQualityCheck,
  updateQualityStatus,
  type BatchUpdateFields,
  type TrashReason,
} from '@/api/inspirations'
import { createCollection } from '@/api/collections'
import { buildSmartQuery } from '@/utils/collectionQuery'
import { QUALITY_BATCH_MAX, qualityBatchHint } from '@/utils/qualityBatch'

interface HomeActionDeps {
  store: ReturnType<typeof useInspirationsStore>
  tagsStore: ReturnType<typeof useTagsStore>
  batch: {
    batchFavorite: (isFavorite: boolean) => Promise<number>
    batchTrash: (reason: TrashReason) => Promise<number>
    batchAddTags: (names: string[]) => Promise<void>
    batchLinkBloggers: (ids: number[]) => Promise<void>
    batchUpdate: (fields: BatchUpdateFields) => Promise<number>
    exitBatchMode: () => void
    selectedIds: Ref<Set<string>>
  }
  filters: {
    filtersActive: Ref<boolean>
    sourceFilter: Ref<string>
    mediaFilter: Ref<string>
    statusFilter: Ref<string>
    qualityFilter: Ref<string>
    selectedTags: Ref<string[]>
    colorFilter: Ref<string>
    ratingMin: Ref<string>
  }
  currentPage: Ref<number>
  loadPage: (page: number) => void
}

export function useHomeActions(opts: HomeActionDeps) {
  const { store, tagsStore, batch, filters, currentPage, loadPage } = opts
  const {
    exitBatchMode,
    selectedIds,
    batchFavorite,
    batchTrash,
    batchAddTags,
    batchLinkBloggers,
    batchUpdate,
  } = batch
  const {
    filtersActive,
    sourceFilter,
    mediaFilter,
    statusFilter,
    qualityFilter,
    selectedTags,
    colorFilter,
    ratingMin,
  } = filters

  // ── 保存为合集（智能合集：序列化当前筛选条件） ──

  const saveCollectionOpen = ref(false)
  const saveCollectionName = ref('')

  function openSaveCollection() {
    saveCollectionName.value = ''
    saveCollectionOpen.value = true
  }

  async function confirmSaveCollection() {
    const name = saveCollectionName.value.trim()
    if (!name) {
      Message.warning('请输入合集名称')
      return
    }
    if (!filtersActive.value) {
      // 无条件 = 动态匹配全库，价值低且易误解，引导先筛选
      Message.warning('请先设置筛选条件，再保存为智能合集')
      return
    }
    const nameToId = (tagName: string) =>
      tagsStore.groups.flatMap((g) => g.tags).find((t) => t.name === tagName)?.id
    const query = buildSmartQuery(
      {
        source: sourceFilter.value,
        media: mediaFilter.value,
        status: statusFilter.value,
        quality: qualityFilter.value,
        tags: selectedTags.value,
        color: colorFilter.value,
        ratingMin: ratingMin.value,
        keyword: '',
      },
      nameToId,
    )
    try {
      await createCollection({ name, query_json: query })
      Message.success(`已创建智能合集「${name}」，可在「收藏合集」页查看`)
    } catch (e) {
      Message.error(getApiErrorMessage(e, '保存为合集失败'))
      return
    }
    saveCollectionOpen.value = false
  }

  // ── 批量加入合集 ──

  const collectionPickerOpen = ref(false)

  /** 批量加入合集：成功后退出批量模式（成员关系变化无需刷新卡片） */
  function handleAddToCollection(_collectionId: number, _added: number) {
    exitBatchMode()
  }

  // ── 删除/收藏 ──

  // 移入垃圾桶前必选原因：单个删除与批量删除共用一个弹窗
  const trashModalOpen = ref(false)
  const pendingTrashIds = ref<string[]>([])

  function handleDelete(id: string) {
    pendingTrashIds.value = [id]
    trashModalOpen.value = true
  }

  /** 弹窗确认：按所选原因移入垃圾桶 */
  async function confirmTrash(reason: TrashReason) {
    trashModalOpen.value = false
    const ids = [...pendingTrashIds.value]
    pendingTrashIds.value = []
    if (ids.length === 0) return
    try {
      if (ids.length === 1) {
        await store.remove(ids[0], reason)
        Message.success('已移入垃圾桶')
        if (store.items.length === 0 && currentPage.value > 1) {
          loadPage(currentPage.value - 1)
        }
      } else {
        const trashed = await batchTrash(reason)
        if (trashed > 0) {
          exitBatchMode()
          loadPage(currentPage.value)
        }
      }
    } catch {
      Message.error('操作失败')
    }
  }

  /** 批量移入垃圾桶：打开原因选择弹窗（确认后走 confirmTrash） */
  function handleBatchTrash() {
    pendingTrashIds.value = [...selectedIds.value]
    if (pendingTrashIds.value.length === 0) return
    trashModalOpen.value = true
  }

  async function handleToggleFavorite(id: string) {
    try {
      await store.toggleFavorite(id)
    } catch {
      Message.error('操作失败')
    }
  }

  /** 设置评分：同步 store（列表项与详情） */
  async function handleRate(id: string, value: number) {
    try {
      await store.setRating(id, value)
      Message.success(value > 0 ? `已评分 ${value} 星` : '已清除评分')
    } catch (e) {
      Message.error(getApiErrorMessage(e, '评分失败'))
    }
  }

  // ── 质量审核 ──

  const checkingQuality = ref(false)

  async function handleBatchQualityCheck() {
    checkingQuality.value = true
    try {
      // 单次上限与后端 le 对齐（此前写死 200：上万条待审核要点几十次）
      const r = await batchQualityCheck(QUALITY_BATCH_MAX)
      if (r.count === 0) {
        Message.info('没有待审核的素材')
      } else {
        // 后台任务异步执行：用带「查看进度」动作的通知引导用户去任务管理页
        Notification.success({
          title: `已提交 ${r.count} 个素材进行质量审核`,
          content: `任务在后台执行（${qualityBatchHint(r.count)}），可稍后在任务管理页查看进度、暂停或取消`,
          duration: 8000,
        })
      }
    } catch {
      Message.error('质量审核提交失败')
    } finally {
      checkingQuality.value = false
    }
  }

  const approvingIds = ref<Set<string>>(new Set())

  async function handleApprove(id: string) {
    if (approvingIds.value.has(id)) return
    approvingIds.value = new Set(approvingIds.value).add(id)
    try {
      await updateQualityStatus(id, 'approved')
      Message.success('已标记为通过')
      // 若当前筛选为「已拒绝」，从列表剔除并减总数；否则仅更新本地状态
      if (qualityFilter.value === 'rejected') {
        store.items = store.items.filter((i) => i.id !== id)
        store.total = Math.max(0, store.total - 1)
      } else {
        const item = store.items.find((i) => i.id === id)
        if (item) {
          item.quality_status = 'approved'
          item.quality_reason = null
        }
      }
    } catch {
      Message.error('操作失败')
    } finally {
      approvingIds.value = new Set(approvingIds.value)
      approvingIds.value.delete(id)
    }
  }

  // ── 批量多选操作 ──

  /** 当前页素材 ID（供全选/取消全选） */

  async function handleBatchFavorite(isFavorite: boolean) {
    const updated = await batchFavorite(isFavorite)
    if (updated > 0) {
      for (const item of store.items) {
        if (selectedIds.value.has(item.id)) item.is_favorite = isFavorite
      }
      exitBatchMode()
    }
  }

  /** 批量加标签：成功后刷新列表（卡片标签更新） */
  async function handleBatchAddTags(names: string[]) {
    await batchAddTags(names)
    exitBatchMode()
    loadPage(currentPage.value)
  }

  /** 批量关联穿搭博主：成功后刷新列表（卡片博主信息更新） */
  async function handleBatchAddBloggers(personIds: number[]) {
    await batchLinkBloggers(personIds)
    exitBatchMode()
    loadPage(currentPage.value)
  }

  /** 批量编辑元数据：成功后刷新列表 */
  async function handleBatchUpdate(fields: BatchUpdateFields) {
    const updated = await batchUpdate(fields)
    if (updated > 0) {
      exitBatchMode()
      loadPage(currentPage.value)
    }
  }

  return {
    saveCollectionOpen,
    saveCollectionName,
    openSaveCollection,
    confirmSaveCollection,
    collectionPickerOpen,
    handleAddToCollection,
    trashModalOpen,
    pendingTrashIds,
    handleDelete,
    confirmTrash,
    handleBatchTrash,
    handleToggleFavorite,
    handleRate,
    checkingQuality,
    handleBatchQualityCheck,
    handleApprove,
    approvingIds,
    handleBatchFavorite,
    handleBatchAddTags,
    handleBatchAddBloggers,
    handleBatchUpdate,
  }
}
