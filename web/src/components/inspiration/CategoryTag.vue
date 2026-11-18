<script setup lang="ts">
/** 类目标签：类目色点 + 方角细线，全站统一（卡片、搜索结果、详情页、相似推荐共用）。
 *
 * 色点只承担「类目」这一个真实信息；色板取印样台的低饱和墨色系，
 * 避免彩色胶囊与朱砂/松烟绿主色抢注意力。 */

/** 类目 → 色点颜色（印样台低饱和色板；design_detail/material 为类别体系优化新增的维度） */
const CATEGORY_COLORS: Record<string, string> = {
  style: '#6b5a6e',
  item_type: '#3f5d6b',
  color: '#a0763a',
  design_detail: '#4f6b60',
  material: '#7a5c43',
  fit: '#8a5a5f',
  attribute: '#6e726d',
  atmosphere: '#4e6b54',
  expression: '#8a5f6a',
  leg_posture: '#46606e',
  free: '#8c8f8a',
  outfit: '#a6402f',
  body_part: '#5d6e52',
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
    <span class="dot" :style="{ backgroundColor: CATEGORY_COLORS[category] || '#8c8f8a' }" />
    <slot />
  </span>
</template>

<style scoped>
.category-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 1px solid var(--line);
  border-radius: 2px;
  color: var(--ink-2);
  background: transparent;
  white-space: nowrap;
  line-height: 1.4;
}
.size-tiny {
  font-size: 11px;
  padding: 0 5px;
}
.size-small {
  font-size: 12px;
  padding: 1px 7px;
}
.dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  flex-shrink: 0;
}
</style>
