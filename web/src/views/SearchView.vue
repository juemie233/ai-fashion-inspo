<script setup lang="ts">
/** 高级搜索页：多维标签筛选 + 搜索结果展示。 */

import { onMounted, ref } from 'vue'
import { useMessage } from 'naive-ui'
import SearchBar from '@/components/search/SearchBar.vue'
import TagFilter from '@/components/search/TagFilter.vue'
import MasonryGrid from '@/components/inspiration/MasonryGrid.vue'
import { useTagsStore } from '@/stores/tags'
import { useInspirationsStore } from '@/stores/inspirations'
import { searchInspirations, type SearchQuery } from '@/api/search'
import type { InspirationOut } from '@/api/inspirations'

const message = useMessage()
const tagsStore = useTagsStore()
const inspStore = useInspirationsStore()

/** 搜索结果 */
const results = ref<InspirationOut[]>([])
/** 结果总数 */
const total = ref(0)
/** 是否正在搜索 */
const searching = ref(false)

onMounted(() => {
  tagsStore.load()
})

/** 执行搜索 */
async function doSearch(tagsFromSearch?: string[]) {
  searching.value = true

  const query: SearchQuery = {
    combine: tagsStore.combineMode,
    page: 1,
    size: 50,
  }

  // 合并搜索栏和标签筛选的标签
  const allIncludeTags = [
    ...Array.from(tagsStore.selectedTags),
    ...(tagsFromSearch || []),
  ]

  if (allIncludeTags.length > 0) {
    query.include_tags = allIncludeTags.join(',')
  }

  if (tagsStore.excludedTags.size > 0) {
    query.exclude_tags = Array.from(tagsStore.excludedTags).join(',')
  }

  try {
    const data = await searchInspirations(query)
    results.value = data.items
    total.value = data.total
  } catch (e) {
    message.error('搜索失败')
  } finally {
    searching.value = false
  }
}

/** 标签筛选变化时自动搜索 */
function onFilterChange() {
  // 仅在有筛选条件时自动搜索
  if (tagsStore.selectedTags.size > 0 || tagsStore.excludedTags.size > 0) {
    doSearch()
  }
}

async function handleDelete(id: string) {
  try {
    await inspStore.remove(id)
    results.value = results.value.filter((r) => r.id !== id)
  } catch {
    message.error('删除失败')
  }
}

async function handleToggleFavorite(id: string) {
  try {
    await inspStore.toggleFavorite(id)
  } catch {
    message.error('操作失败')
  }
}
</script>

<template>
  <div class="search-page">
    <h2>高级搜索</h2>

    <!-- 搜索栏 -->
    <div class="search-section">
      <SearchBar @search="doSearch" />
    </div>

    <!-- 标签筛选 + 结果 -->
    <div class="search-layout">
      <!-- 左侧筛选面板 -->
      <aside class="filter-panel">
        <TagFilter @filter-change="onFilterChange" />
        <n-button
          type="primary"
          block
          style="margin-top: 16px"
          @click="doSearch()"
          :loading="searching"
        >
          搜索
        </n-button>
      </aside>

      <!-- 右侧结果 -->
      <main class="result-panel">
        <div v-if="total > 0" class="result-header">
          找到 {{ total }} 条结果
        </div>
        <MasonryGrid
          :items="results"
          :loading="searching"
          :has-more="false"
          @delete="handleDelete"
          @toggle-favorite="handleToggleFavorite"
        />
      </main>
    </div>
  </div>
</template>

<style scoped>
.search-page {
  max-width: 1600px;
  margin: 0 auto;
}

.search-section {
  margin-bottom: 24px;
}

.search-layout {
  display: flex;
  gap: 24px;
}

.filter-panel {
  width: 280px;
  flex-shrink: 0;
}

.result-panel {
  flex: 1;
  min-width: 0;
}

.result-header {
  color: #666;
  margin-bottom: 12px;
  font-size: 14px;
}

@media (max-width: 900px) {
  .search-layout {
    flex-direction: column;
  }
  .filter-panel {
    width: 100%;
  }
}
</style>
