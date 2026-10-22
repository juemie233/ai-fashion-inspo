<script setup lang="ts">
/** 素材库「当前筛选」摘要条：把生效的筛选逐项列成可关闭的标签，并提供「清除全部」。
 *
 * 从 HomeView.vue 抽出（视图只做编排）。整条摘要依赖筛选域的状态、选项与 setter，
 * 所以直接用视图传入的 filters（与筛选栏同一个实例），不再单独维护任何状态。
 */

import { useInspirationFilters } from '@/composables/useInspirationFilters'

/** 筛选域返回值：状态 + 选项 + setter */
type Filters = ReturnType<typeof useInspirationFilters>

const props = defineProps<{
  /** 视图侧 useInspirationFilters() 的返回值 */
  filters: Filters
}>()

const {
  sourceFilter,
  mediaFilter,
  statusFilter,
  qualityFilter,
  selectedTags,
  colorFilter,
  sortMode,
  sourceOptions,
  mediaOptions,
  statusOptions,
  qualityOptions,
  sortOptions,
  setSourceFilter,
  setMediaFilter,
  setStatusFilter,
  setQualityFilter,
  setColorFilter,
  setSortMode,
  removeTagFilter,
  clearAllFilters,
} = props.filters
</script>

<template>
  <!-- 当前筛选提示 -->
  <div
    v-if="
      sourceFilter !== 'all' ||
      mediaFilter !== 'all' ||
      statusFilter !== 'all' ||
      qualityFilter !== 'all' ||
      selectedTags.length > 0 ||
      colorFilter !== '' ||
      sortMode !== 'newest'
    "
    class="active-filters"
  >
    当前筛选：
    <a-tag v-if="sourceFilter !== 'all'" size="small" closable @close="setSourceFilter('all')">
      {{ sourceOptions.find((o) => o.value === sourceFilter)?.label }}
    </a-tag>
    <a-tag v-if="mediaFilter !== 'all'" size="small" closable @close="setMediaFilter('all')">
      {{ mediaOptions.find((o) => o.value === mediaFilter)?.label }}
    </a-tag>
    <a-tag v-if="statusFilter !== 'all'" size="small" closable @close="setStatusFilter('all')">
      {{ statusOptions.find((o) => o.value === statusFilter)?.label }}
    </a-tag>
    <a-tag v-if="qualityFilter !== 'all'" size="small" closable @close="setQualityFilter('all')">
      {{ qualityOptions.find((o) => o.value === qualityFilter)?.label }}
    </a-tag>
    <a-tag
      v-for="tag in selectedTags"
      :key="tag"
      size="small"
      closable
      @close="removeTagFilter(tag)"
    >
      {{ tag }}
    </a-tag>
    <a-tag v-if="colorFilter" size="small" closable @close="setColorFilter('')">
      <span class="color-chip" :style="{ background: colorFilter }" /> {{ colorFilter }}
    </a-tag>
    <a-tag v-if="sortMode !== 'newest'" size="small" closable @close="setSortMode('newest')">
      {{ sortOptions.find((o) => o.value === sortMode)?.label }}
    </a-tag>
    <a-button size="mini" type="text" @click="clearAllFilters"> 清除全部 </a-button>
  </div>
</template>

<style scoped>
/* 活跃筛选提示 */
.active-filters {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 8px;
  font-size: 12px;
  color: #999;
  flex-wrap: wrap;
}
</style>
