/** 标签管理页核心数据与操作：加载、筛选排序、选中、删除/合并/置顶/重复扫描等。 */

import { ref, computed } from 'vue'
import { useMessage } from 'naive-ui'
import {
  fetchTagsGrouped,
  updateTag,
  mergeTags,
  batchDeleteTags,
  deleteUnusedTags,
  fetchTagStats,
  findDuplicates,
  reorderTags,
  createAlias,
  CATEGORY_LABELS,
  type TagCategoryGroup,
  type TagItem,
  type TagStats,
  type DuplicatePair,
} from '@/api/tags'

/** 标签管理页数据模型与业务操作集合，由 TagManageView 及其子组件消费。 */
export function useTagManage() {
  const message = useMessage()

  // ===== 数据 =====
  const groups = ref<TagCategoryGroup[]>([])
  const loading = ref(false)
  const stats = ref<TagStats | null>(null)

  // ===== 筛选与排序 =====
  const searchQuery = ref('')
  const filterCategory = ref<string | null>(null)
  const filterSource = ref<string | null>(null)
  const sortMode = ref<'usage' | 'name' | 'custom'>('usage')

  // ===== 选中（批量操作） =====
  const selectedIds = ref<Set<number>>(new Set())

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
          const t = g.tags.find(x => x.id === selectedTag.value!.id)
          if (t) { fresh = t; break }
        }
        selectedTag.value = fresh
      }
    } catch { message.error('加载失败') } finally { loading.value = false }
  }

  // ===== 过滤与排序 =====
  const filteredGroups = computed(() => {
    let result = groups.value.map(g => ({ ...g, tags: [...g.tags] }))

    // 类别筛选
    if (filterCategory.value) {
      result = result.filter(g => g.category === filterCategory.value)
    }
    // 来源筛选
    if (filterSource.value) {
      result = result.map(g => ({
        ...g,
        tags: g.tags.filter(t => t.source === filterSource.value),
      }))
    }
    // 搜索过滤
    if (searchQuery.value.trim()) {
      const q = searchQuery.value.trim().toLowerCase()
      result = result.map(g => ({
        ...g,
        tags: g.tags.filter(t => t.name.toLowerCase().includes(q)),
      }))
    }
    // 排序（custom 模式保持后端 sort_order 顺序，不做前端排序）
    for (const g of result) {
      // 置顶优先：无论「使用次数」还是「名称」排序，置顶标签始终排本组最前
      const pinnedFirst = (a: TagItem, b: TagItem) =>
        Number(b.pinned) - Number(a.pinned)
      if (sortMode.value === 'usage') {
        g.tags.sort((a, b) => pinnedFirst(a, b) || (b.usage_count - a.usage_count))
      } else if (sortMode.value === 'name') {
        g.tags.sort((a, b) => pinnedFirst(a, b) || a.name.localeCompare(b.name, 'zh'))
      }
    }
    // 移除空组
    return result.filter(g => g.tags.length > 0)
  })

  /** 是否存在搜索/类别/来源筛选：筛选态下可见顺序与完整分组不一致，禁用自定义排序拖拽 */
  const hasActiveFilter = computed(() =>
    !!searchQuery.value.trim() || !!filterCategory.value || !!filterSource.value
  )

  // ===== 删除（单个 / 批量） =====
  async function handleDelete(tagId: number, tagName: string) {
    try {
      await batchDeleteTags([tagId])
      message.success(`已删除 "${tagName}"`)
      // Set 是响应式的：原地 delete 不会触发视图更新，须整体替换
      const next = new Set(selectedIds.value)
      next.delete(tagId)
      selectedIds.value = next
      await loadAll()
    } catch { message.error('删除失败') }
  }

  async function handleBatchDelete() {
    if (selectedIds.value.size === 0) return
    const ids = Array.from(selectedIds.value)
    try {
      await batchDeleteTags(ids)
      message.success(`已删除 ${ids.length} 个标签`)
      selectedIds.value = new Set()
      await loadAll()
    } catch { message.error('批量删除失败') }
  }

  async function handleDeleteUnused() {
    try {
      const data = await deleteUnusedTags()
      message.success(data.message)
      await loadAll()
    } catch (e: any) {
      const detail = e?.response?.data?.detail || e?.message || '未知错误'
      message.error('删除失败：' + detail)
    }
  }

  // ===== 重复扫描 =====
  async function scanDuplicates() {
    scanningDuplicates.value = true
    try {
      const data = await findDuplicates(duplicateThreshold.value)
      duplicatePairs.value = data.duplicates
      showDuplicatesPanel.value = true
      if (data.total === 0) message.success('未发现重复标签')
      else message.info(`发现 ${data.total} 对相似标签`)
    } catch { message.error('扫描失败') }
    finally { scanningDuplicates.value = false }
  }

  /** 重复面板：将 a 合并进 b */
  async function quickMerge(a: number, b: number) {
    try {
      await mergeTags(a, b)
      message.success('已快速合并')
      // 移除所有引用被合并标签的 pair（a 已被删除）
      duplicatePairs.value = duplicatePairs.value.filter(
        p => p.tag_a.id !== a && p.tag_b.id !== a
      )
      await loadAll()
    } catch { message.error('合并失败') }
  }

  /** 重复面板：将源标签合并到目标并设为其别名 */
  async function quickSetAlias(sourceId: number, targetId: number, sourceName: string) {
    try {
      await mergeTags(sourceId, targetId)
      await createAlias(targetId, sourceName)
      message.success(`已合并并将「${sourceName}」设为别名`)
      duplicatePairs.value = duplicatePairs.value.filter(
        (p) => p.tag_a.id !== sourceId && p.tag_b.id !== sourceId,
      )
      await loadAll()
    } catch (e: any) {
      message.error(e.response?.data?.detail || '操作失败')
    }
  }

  // ===== 标签详情 =====
  function selectTag(tag: TagItem) {
    selectedTag.value = tag
  }

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

  // ===== 选中逻辑 =====
  function toggleSelect(id: number) {
    if (selectedIds.value.has(id)) selectedIds.value.delete(id)
    else selectedIds.value.add(id)
    selectedIds.value = new Set(selectedIds.value)
  }
  function selectAllInGroup(group: TagCategoryGroup) {
    for (const t of group.tags) selectedIds.value.add(t.id)
    selectedIds.value = new Set(selectedIds.value)
  }
  function deselectAll() { selectedIds.value = new Set() }

  // ===== 置顶 =====
  async function togglePin(tag: TagItem) {
    try {
      await updateTag(tag.id, { pinned: !tag.pinned })
      message.success(tag.pinned ? '已取消置顶' : '已置顶')
      await loadAll()
    } catch { message.error('操作失败') }
  }

  // ===== 拖拽改类别 =====
  async function onDrop(tag: TagItem, category: string) {
    if (tag.category === category) return
    try {
      await updateTag(tag.id, { category })
      message.success(`已将 "${tag.name}" 移至 ${CATEGORY_LABELS[category] || category}`)
      await loadAll()
    } catch { message.error('移动失败') }
  }

  // ===== 自定义排序拖拽 =====
  async function onTagDrop(target: TagItem, dragged: TagItem) {
    if (sortMode.value !== 'custom') return
    if (hasActiveFilter.value) {
      message.warning('筛选状态下不支持拖动排序，请清除筛选后重试')
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
      message.success('排序已更新')
      await loadAll()
    } catch { message.error('排序失败') }
  }

  return {
    groups, loading, stats, searchQuery, filterCategory, filterSource, sortMode,
    selectedIds, selectedTag, scanningDuplicates, duplicatePairs, showDuplicatesPanel,
    duplicateThreshold, filteredGroups, hasActiveFilter,
    loadAll, selectTag, onGridChanged, toggleSelect, selectAllInGroup, deselectAll,
    handleDelete, handleBatchDelete, handleDeleteUnused, scanDuplicates,
    quickMerge, quickSetAlias, togglePin, onDrop, onTagDrop,
  }
}
