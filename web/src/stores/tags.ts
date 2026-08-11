/** 标签状态管理：标签列表、分类、选中状态。 */

import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  fetchTagsGrouped,
  type TagCategoryGroup,
  type TagItem,
  CATEGORY_LABELS,
} from '@/api/tags'

export const useTagsStore = defineStore('tags', () => {
  /** 已加载的标签分组 */
  const groups = ref<TagCategoryGroup[]>([])
  /** 是否正在加载 */
  const loading = ref(false)
  /** 当前搜索选中的标签名称集合 */
  const selectedTags = ref<Set<string>>(new Set())
  /** 排除的标签名称集合 */
  const excludedTags = ref<Set<string>>(new Set())
  /** 组合逻辑 */
  const combineMode = ref<'AND' | 'OR'>('AND')

  /** 加载标签列表 */
  async function load() {
    if (groups.value.length > 0) return // 已加载则跳过
    loading.value = true
    try {
      groups.value = await fetchTagsGrouped()
    } catch (e) {
      console.error('加载标签失败', e)
    } finally {
      loading.value = false
    }
  }

  /** 切换标签的选中状态 */
  function toggleTag(name: string) {
    if (selectedTags.value.has(name)) {
      selectedTags.value.delete(name)
    } else {
      selectedTags.value.add(name)
    }
    // 触发响应式更新
    selectedTags.value = new Set(selectedTags.value)
  }

  /** 切换排除标签 */
  function toggleExcludeTag(name: string) {
    if (excludedTags.value.has(name)) {
      excludedTags.value.delete(name)
    } else {
      excludedTags.value.add(name)
    }
    excludedTags.value = new Set(excludedTags.value)
  }

  /** 清除所有筛选 */
  function clearFilters() {
    selectedTags.value = new Set()
    excludedTags.value = new Set()
  }

  /** 获取类别显示名称 */
  function getCategoryLabel(category: string): string {
    return CATEGORY_LABELS[category] || category
  }

  return {
    groups,
    loading,
    selectedTags,
    excludedTags,
    combineMode,
    load,
    toggleTag,
    toggleExcludeTag,
    clearFilters,
    getCategoryLabel,
  }
})
