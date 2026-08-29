<script setup lang="ts">
/** 类目标签：黑色细边框 + 深灰文字 + 类目色点，全站统一（卡片、搜索结果、详情页、相似推荐共用）。 */

/** 类目 → 色点颜色（design_detail/material 为类别体系优化新增的维度） */
const CATEGORY_COLORS: Record<string, string> = {
  style: '#8b5cf6',
  item_type: '#3b82f6',
  color: '#f59e0b',
  design_detail: '#14b8a6',
  material: '#a16207',
  fit: '#ec4899',
  attribute: '#6b7280',
  atmosphere: '#22c55e',
  expression: '#f472b6',
  leg_posture: '#0ea5e9',
  free: '#9ca3af',
  outfit: '#e11d48',
  body_part: '#10b981',
}

defineProps<{
  /** 标签类目，用于确定色点颜色 */
  category: string
  /** 尺寸：tiny 用于卡片，small 用于详情页 */
  size?: 'tiny' | 'small'
}>()
</script>

<template>
  <span class="category-tag" :class="'size-' + (size || 'tiny')">
    <span class="dot" :style="{ backgroundColor: CATEGORY_COLORS[category] || '#9ca3af' }" />
    <slot />
  </span>
</template>

<style scoped>
.category-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 1px solid #000;
  border-radius: 999px;
  color: #333;
  background: transparent;
  white-space: nowrap;
  line-height: 1.4;
}
.size-tiny {
  font-size: 12px;
  padding: 1px 8px;
}
.size-small {
  font-size: 13px;
  padding: 2px 10px;
}
.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}
</style>
