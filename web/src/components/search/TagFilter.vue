<script setup lang="ts">
/** 多维标签筛选面板：按类别分组展示，支持选中/排除，AND/OR 切换。 */

import { computed } from 'vue'
import { useTagsStore } from '@/stores/tags'

const tagsStore = useTagsStore()

/** 是否在基础色模式（暗色模式） */
const isDark = computed(() => false) // 当前固定为浅色

/** 标签是否被选中（包含） */
function isSelected(name: string): boolean {
  return tagsStore.selectedTags.has(name)
}

/** 标签是否被排除 */
function isExcluded(name: string): boolean {
  return tagsStore.excludedTags.has(name)
}
</script>

<template>
  <div class="tag-filter">
    <!-- 组合模式切换 -->
    <div class="filter-header">
      <n-radio-group v-model:value="tagsStore.combineMode" size="small">
        <n-radio-button value="AND">全部匹配</n-radio-button>
        <n-radio-button value="OR">任意匹配</n-radio-button>
      </n-radio-group>

      <n-button
        v-if="tagsStore.selectedTags.size > 0 || tagsStore.excludedTags.size > 0"
        size="small"
        text
        type="warning"
        @click="tagsStore.clearFilters()"
      >
        清除筛选
      </n-button>
    </div>

    <!-- 已选标签预览 -->
    <div v-if="tagsStore.selectedTags.size > 0" class="active-filters">
      <n-tag
        v-for="name in [...tagsStore.selectedTags]"
        :key="'inc-' + name"
        closable
        type="info"
        size="small"
        @close="tagsStore.toggleTag(name)"
      >
        ✓ {{ name }}
      </n-tag>
    </div>
    <div v-if="tagsStore.excludedTags.size > 0" class="active-filters excluded">
      <n-tag
        v-for="name in [...tagsStore.excludedTags]"
        :key="'exc-' + name"
        closable
        type="error"
        size="small"
        @close="tagsStore.toggleExcludeTag(name)"
      >
        ✕ {{ name }}
      </n-tag>
    </div>

    <!-- 标签分组列表 -->
    <n-collapse>
      <n-collapse-item
        v-for="group in tagsStore.groups"
        :key="group.category"
        :title="`${tagsStore.getCategoryLabel(group.category)} (${group.tags.length})`"
      >
        <div class="tag-chips">
          <span
            v-for="tag in group.tags"
            :key="tag.id"
            class="tag-chip"
            :class="{
              selected: isSelected(tag.name),
              excluded: isExcluded(tag.name),
            }"
            @click="tagsStore.toggleTag(tag.name)"
            @contextmenu.prevent="tagsStore.toggleExcludeTag(tag.name)"
            :title="`左键包含 / 右键排除 (使用 ${tag.usage_count} 次)`"
          >
            {{ tag.name }}
            <span class="usage">{{ tag.usage_count }}</span>
          </span>
        </div>
      </n-collapse-item>
    </n-collapse>

    <!-- 加载中 -->
    <n-spin v-if="tagsStore.loading" size="small" style="margin-top: 16px" />
  </div>
</template>

<style scoped>
.tag-filter {
  padding: 4px;
}

.filter-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.active-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 12px;
}

.tag-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.tag-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 10px;
  border-radius: 12px;
  font-size: 13px;
  cursor: pointer;
  background: #f5f5f5;
  border: 1px solid #e5e5e5;
  transition: all 0.15s;
}
.tag-chip:hover {
  border-color: #3b82f6;
}
.tag-chip.selected {
  background: #3b82f620;
  border-color: #3b82f6;
  color: #3b82f6;
}
.tag-chip.excluded {
  background: #ef444420;
  border-color: #ef4444;
  color: #ef4444;
  text-decoration: line-through;
}
.usage {
  font-size: 10px;
  color: #999;
}
</style>
