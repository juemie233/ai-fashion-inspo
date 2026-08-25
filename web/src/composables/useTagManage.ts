/** 标签管理页核心数据与操作：加载、筛选排序、选中、删除/合并/置顶/重复扫描等。 */

import { getApiErrorMessage } from '@/utils/apiError'
import { getCurrentInstance, ref, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import { useTagSelection } from './useTagSelection'
import { useTagEvents } from './useTagEvents'
import {
  fetchTagsGrouped,
  updateTag,
  batchDeleteTags,
  deleteUnusedTags,
  fetchTagStats,
  findDuplicates,
  reorderTags,
  type TagCategoryGroup,
  type TagItem,
  type TagStats,
  type DuplicatePair,
} from '@/api/tags'
import { CATEGORY_LABELS } from '@/constants/tag'

/** 标签管理页数据模型与业务操作集合，由 TagManageView 及其子组件消费。 */
export function useTagManage() {
  const route = useRoute()
  const router = useRouter()

  // ===== 数据 =====
  const groups = ref<TagCategoryGroup[]>([])
  const loading = ref(false)
  const stats = ref<TagStats | null>(null)

  // ===== 筛选与排序（初始值从 URL query 恢复：刷新 / 详情页返回后保持浏览状态） =====
  const searchQuery = ref((route.query.q as string) || '')
  const filterCategory = ref<string | null>((route.query.category as string) || null)
  const filterSource = ref<string | null>((route.query.source as string) || null)
  const sortMode = ref<'usage' | 'name' | 'custom'>(
    route.query.sort === 'name' || route.query.sort === 'custom' ? route.query.sort : 'usage',
  )

  // ===== 选中（批量操作，复用统一的多选 composable） =====
  const {
    selectedIds,
    toggle: toggleSelect,
    addMany: _addMany,
    clear: deselectAll,
    remove: _remove,
  } = useTagSelection()

  /** 选中某分组下的全部标签 */
  function selectAllInGroup(group: TagCategoryGroup) {
    _addMany(group.tags.map((t) => t.id))
  }

  // ===== 选中标签 → 右侧素材面板 =====
  const selectedTag = ref<TagItem | null>(null)

  // ===== 重复扫描 =====
  const scanningDuplicates = ref(false)
  const duplicatePairs = ref<DuplicatePair[]>([])
  const showDuplicatesPanel = ref(false)
  /** 相似度阈值（可调）：低于该值不视为重复 */
  const duplicateThreshold = ref(0.75)

  // ===== 加载全部 =====
  async function loadAll() {
    loading.value = true
    try {
      groups.value = await fetchTagsGrouped()
      stats.value = await fetchTagStats()
      // 数据刷新后按 id 重新查找选中标签并替换引用，右侧面板显示最新名称/使用次数；
      // 标签已不存在（被删除/合并）时清空选中，右侧回到空态
      if (selectedTag.value) {
        let fresh: TagItem | null = null
        for (const g of groups.value) {
          const t = g.tags.find((x) => x.id === selectedTag.value!.id)
          if (t) {
            fresh = t
            break
          }
        }
        selectedTag.value = fresh
      } else {
        // 从 URL query 恢复上次浏览的标签（刷新 / 点击素材详情返回后，右侧面板仍展示原标签素材）
        const tagId = Number(route.query.tag)
        if (Number.isFinite(tagId) && tagId > 0) {
          for (const g of groups.value) {
            const t = g.tags.find((x) => x.id === tagId)
            if (t) {
              selectedTag.value = t
              break
            }
          }
        }
      }
    } catch {
      Message.error('加载失败')
    } finally {
      loading.value = false
    }
  }

  // ===== 过滤与排序 =====
  const filteredGroups = computed(() => {
    let result = groups.value.map((g) => ({ ...g, tags: [...g.tags] }))

    // 类别筛选
    if (filterCategory.value) {
      result = result.filter((g) => g.category === filterCategory.value)
    }
    // 来源筛选
    if (filterSource.value) {
      result = result.map((g) => ({
        ...g,
        tags: g.tags.filter((t) => t.source === filterSource.value),
      }))
    }
    // 搜索过滤
    if (searchQuery.value.trim()) {
      const q = searchQuery.value.trim().toLowerCase()
      result = result.map((g) => ({
        ...g,
        tags: g.tags.filter((t) => t.name.toLowerCase().includes(q)),
      }))
    }
    // 排序（custom 模式保持后端 sort_order 顺序，不做前端排序）
    for (const g of result) {
      // 置顶优先：无论「使用次数」还是「名称」排序，置顶标签始终排本组最前
      const pinnedFirst = (a: TagItem, b: TagItem) => Number(b.pinned) - Number(a.pinned)
      if (sortMode.value === 'usage') {
        g.tags.sort((a, b) => pinnedFirst(a, b) || b.usage_count - a.usage_count)
      } else if (sortMode.value === 'name') {
        g.tags.sort((a, b) => pinnedFirst(a, b) || a.name.localeCompare(b.name, 'zh'))
      }
    }
    // 移除空组
    return result.filter((g) => g.tags.length > 0)
  })

  /** 是否存在搜索/类别/来源筛选：筛选态下可见顺序与完整分组不一致，禁用自定义排序拖拽 */
  const hasActiveFilter = computed(
    () => !!searchQuery.value.trim() || !!filterCategory.value || !!filterSource.value,
  )

  // ===== 删除（单个 / 批量） =====
  async function handleDelete(tagId: number, tagName: string) {
    try {
      await batchDeleteTags([tagId])
      Message.success(`已删除 "${tagName}"`)
      _remove(tagId)
      await loadAll()
    } catch {
      Message.error('删除失败')
    }
  }

  async function handleBatchDelete() {
    if (selectedIds.value.size === 0) return
    const ids = Array.from(selectedIds.value)
    try {
      await batchDeleteTags(ids)
      Message.success(`已删除 ${ids.length} 个标签`)
      deselectAll()
      await loadAll()
    } catch {
      Message.error('批量删除失败')
    }
  }

  async function handleDeleteUnused() {
    try {
      const data = await deleteUnusedTags()
      Message.success(data.message)
      await loadAll()
    } catch (e) {
      Message.error('删除失败：' + getApiErrorMessage(e, '未知错误'))
    }
  }

  // ===== 重复扫描 =====
  async function scanDuplicates() {
    scanningDuplicates.value = true
    try {
      const data = await findDuplicates(duplicateThreshold.value)
      duplicatePairs.value = data.duplicates
      showDuplicatesPanel.value = true
      if (data.total === 0) Message.success('未发现重复标签')
      else Message.info(`发现 ${data.total} 对相似标签`)
    } catch {
      Message.error('扫描失败')
    } finally {
      scanningDuplicates.value = false
    }
  }

  const { onTagChanged } = useTagEvents()

  // 标签在其他入口（高级管理页、图片对比弹窗等）被改名/合并/删除/批量编辑后，
  // 自动刷新本页数据与重复列表；仅在组件 setup 中订阅，单测中裸调用不挂载。
  if (getCurrentInstance()) {
    onTagChanged(
      (payload) => {
        // 合并时被合并的源标签已删除，从本地重复列表即时移除其所有 pair
        if (payload.type === 'merged' && payload.tagIds) {
          const removed = new Set(payload.tagIds)
          duplicatePairs.value = duplicatePairs.value.filter(
            (p) =>
              p.tag_a !== null &&
              p.tag_b !== null &&
              !removed.has(p.tag_a.id) &&
              !removed.has(p.tag_b.id),
          )
        }
        loadAll()
      },
      ['updated', 'merged', 'deleted', 'batch-edited', 'created'],
    )
  }

  // ===== 标签详情 =====
  function selectTag(tag: TagItem) {
    selectedTag.value = tag
  }

  // ===== URL 持久化：浏览状态（搜索/筛选/排序/当前标签）同步到 query =====
  /** 把当前浏览状态写入 URL query（replace 不产生历史记录；默认值不写入，保持 URL 干净） */
  function syncQuery() {
    const query: Record<string, string> = {}
    if (searchQuery.value.trim()) query.q = searchQuery.value.trim()
    if (filterCategory.value) query.category = filterCategory.value
    if (filterSource.value) query.source = filterSource.value
    if (sortMode.value !== 'usage') query.sort = sortMode.value
    if (selectedTag.value) query.tag = String(selectedTag.value.id)
    router.replace({ query })
  }

  // 任一浏览状态变化即同步 URL：刷新或从素材详情返回后，页面恢复为离开前的浏览状态
  watch(
    [searchQuery, filterCategory, filterSource, sortMode, () => selectedTag.value?.id],
    syncQuery,
  )

  /** 素材关联数变化后，同步左侧标签 usage_count 与统计数字 */
  function onGridChanged(payload: { removed: number }) {
    // 批量添加标签（removed=0）后整体刷新，确保统计与 usage_count 准确
    if (payload.removed === 0) {
      loadAll()
      return
    }
    if (selectedTag.value) {
      selectedTag.value.usage_count = Math.max(0, selectedTag.value.usage_count - payload.removed)
    }
    if (stats.value) {
      stats.value.total_links = Math.max(0, stats.value.total_links - payload.removed)
    }
  }

  // ===== 置顶 =====
  async function togglePin(tag: TagItem) {
    try {
      await updateTag(tag.id, { pinned: !tag.pinned })
      Message.success(tag.pinned ? '已取消置顶' : '已置顶')
      await loadAll()
    } catch {
      Message.error('操作失败')
    }
  }

  // ===== 拖拽改类别 =====
  async function onDrop(tag: TagItem, category: string) {
    if (tag.category === category) return
    try {
      await updateTag(tag.id, { category })
      Message.success(`已将 "${tag.name}" 移至 ${CATEGORY_LABELS[category] || category}`)
      await loadAll()
    } catch {
      Message.error('移动失败')
    }
  }

  // ===== 自定义排序拖拽 =====
  async function onTagDrop(target: TagItem, dragged: TagItem) {
    if (sortMode.value !== 'custom') return
    if (hasActiveFilter.value) {
      Message.warning('筛选状态下不支持拖动排序，请清除筛选后重试')
      return
    }
    if (dragged.id === target.id) return
    const group = groups.value.find((g) => g.tags.some((t) => t.id === target.id))
    if (!group || !group.tags.some((t) => t.id === dragged.id)) return
    const tags = [...group.tags]
    const fromIdx = tags.findIndex((t) => t.id === dragged.id)
    const toIdx = tags.findIndex((t) => t.id === target.id)
    if (fromIdx < 0 || toIdx < 0) return
    // off-by-one 修复：向后拖（toIdx > fromIdx）时，删除源项会让目标索引前移一位
    const insertIdx = fromIdx < toIdx ? toIdx - 1 : toIdx
    const [moved] = tags.splice(fromIdx, 1)
    tags.splice(insertIdx, 0, moved)
    try {
      await reorderTags(tags.map((t, i) => ({ id: t.id, sort_order: i })))
      Message.success('排序已更新')
      await loadAll()
    } catch {
      Message.error('排序失败')
    }
  }

  return {
    groups,
    loading,
    stats,
    searchQuery,
    filterCategory,
    filterSource,
    sortMode,
    selectedIds,
    selectedTag,
    scanningDuplicates,
    duplicatePairs,
    showDuplicatesPanel,
    duplicateThreshold,
    filteredGroups,
    hasActiveFilter,
    loadAll,
    selectTag,
    onGridChanged,
    toggleSelect,
    selectAllInGroup,
    deselectAll,
    handleDelete,
    handleBatchDelete,
    handleDeleteUnused,
    scanDuplicates,
    togglePin,
    onDrop,
    onTagDrop,
  }
}
