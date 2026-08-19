<script setup lang="ts">
/** 多维标签筛选面板：分组展示、内部搜索、共现提示、AND/OR 切换。 */

import { ref, computed } from 'vue'
import { useTagsStore } from '@/stores/tags'
import { fetchCooccurrence } from '@/api/search'

const tagsStore = useTagsStore()

/** 标签面板内搜索 */
const filterSearch = ref('')
/** 共现标签推荐 */
const cooccurrenceTags = ref<Array<{ name: string; shared_count: number }>>([])
const cooccurrenceFor = ref('')

/** 过滤后的标签分组 */
const filteredGroups = computed(() => {
  const q = filterSearch.value.trim().toLowerCase()
  if (!q) return tagsStore.groups
  return tagsStore.groups
    .map((g) => ({
      ...g,
      tags: g.tags.filter((t) => t.name.toLowerCase().includes(q)),
    }))
    .filter((g) => g.tags.length > 0)
})

function isSelected(name: string): boolean {
  return tagsStore.selectedTags.has(name)
}

function isExcluded(name: string): boolean {
  return tagsStore.excludedTags.has(name)
}

/** 加载共现标签（请求序号守卫：快速切换标签时旧响应不覆盖新结果） */
let cooccurrenceSeq = 0

async function loadCooccurrence(tagName: string) {
  if (cooccurrenceFor.value === tagName) {
    cooccurrenceFor.value = ''
    cooccurrenceTags.value = []
    return
  }
  const seq = ++cooccurrenceSeq
  try {
    const data = await fetchCooccurrence(tagName)
    if (seq !== cooccurrenceSeq) return
    cooccurrenceFor.value = tagName
    cooccurrenceTags.value = data.related.slice(0, 5)
  } catch {
    if (seq !== cooccurrenceSeq) return
    cooccurrenceTags.value = []
  }
}

/** 标签项点击 */
function onTagClick(tagName: string) {
  tagsStore.toggleTag(tagName)
  if (tagsStore.selectedTags.has(tagName)) {
    loadCooccurrence(tagName)
  }
}

/** 组合逻辑描述 */
const combineDesc = computed(() => {
  const selected = [...tagsStore.selectedTags]
  if (selected.length < 2) return ''
  const sep = tagsStore.combineMode === 'AND' ? ' ∩ ' : ' ∪ '
  return selected.join(sep)
})
</script>

<template>
  <div class="tag-filter">
    <!-- 组合模式切换 -->
    <div class="filter-header">
      <a-radio-group v-model="tagsStore.combineMode" type="button" size="small">
        <a-radio value="AND" title="素材必须同时包含所有已选标签">全部匹配 (AND)</a-radio>
        <a-radio value="OR" title="素材包含任意一个已选标签即可">任意匹配 (OR)</a-radio>
      </a-radio-group>

      <a-button
        v-if="tagsStore.selectedTags.size > 0 || tagsStore.excludedTags.size > 0"
        size="small"
        type="text"
        status="warning"
        @click="tagsStore.clearFilters()"
      >
        清除
      </a-button>
    </div>

    <!-- AND/OR 逻辑描述 -->
    <div v-if="combineDesc" class="combine-desc" title="当前组合逻辑">
      {{ combineDesc }}
    </div>

    <!-- 已选标签预览 -->
    <div v-if="tagsStore.selectedTags.size > 0" class="active-filters">
      <span style="font-size: 11px; color: #999">包含:</span>
      <a-tag
        v-for="name in [...tagsStore.selectedTags]"
        :key="'inc-' + name"
        closable
        color="arcoblue"
        size="small"
        @close="tagsStore.toggleTag(name)"
      >
        {{ name }}
      </a-tag>
    </div>
    <div v-if="tagsStore.excludedTags.size > 0" class="active-filters excluded">
      <span style="font-size: 11px; color: #999">排除:</span>
      <a-tag
        v-for="name in [...tagsStore.excludedTags]"
        :key="'exc-' + name"
        closable
        color="red"
        size="small"
        @close="tagsStore.toggleExcludeTag(name)"
      >
        {{ name }}
      </a-tag>
    </div>

    <!-- 共现标签提示 -->
    <div v-if="cooccurrenceTags.length > 0" class="cooccurrence-box">
      <div class="cooccurrence-title">
        「{{ cooccurrenceFor }}」常与以下标签一起出现：
        <a-button
          size="mini"
          type="text"
          @click="
            cooccurrenceTags = []
            cooccurrenceFor = ''
          "
          >✕</a-button
        >
      </div>
      <div class="cooccurrence-chips">
        <a-tag
          v-for="ct in cooccurrenceTags"
          :key="ct.name"
          size="small"
          color="green"
          style="cursor: pointer"
          @click="tagsStore.toggleTag(ct.name)"
        >
          + {{ ct.name }} ({{ ct.shared_count }})
        </a-tag>
      </div>
    </div>

    <!-- 面板内搜索 -->
    <a-input
      v-model="filterSearch"
      size="small"
      placeholder="在标签中搜索..."
      allow-clear
      style="margin-bottom: 8px"
    />

    <!-- 标签分组列表 -->
    <a-collapse>
      <a-collapse-item
        v-for="group in filteredGroups"
        :key="group.category"
        :header="`${tagsStore.getCategoryLabel(group.category)} (${group.tags.length})`"
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
            @click="onTagClick(tag.name)"
            @contextmenu.prevent="tagsStore.toggleExcludeTag(tag.name)"
            :title="`左键包含 / 右键排除 (使用 ${tag.usage_count} 次)`"
          >
            {{ tag.name }}
            <span class="usage">{{ tag.usage_count }}</span>
          </span>
        </div>
      </a-collapse-item>
    </a-collapse>

    <a-spin v-if="tagsStore.loading" :size="16" style="margin-top: 16px" />
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
  margin-bottom: 8px;
}

.combine-desc {
  font-size: 11px;
  color: #3b82f6;
  padding: 2px 8px;
  margin-bottom: 8px;
  background: #eff6ff;
  border-radius: 4px;
  word-break: break-all;
  font-family: monospace;
}

.active-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 8px;
  align-items: center;
}

.cooccurrence-box {
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  border-radius: 6px;
  padding: 6px 8px;
  margin-bottom: 8px;
}

.cooccurrence-title {
  font-size: 11px;
  color: #166534;
  margin-bottom: 4px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.cooccurrence-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
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
