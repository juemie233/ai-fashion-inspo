<script setup lang="ts">
/** 高级搜索页：多维标签筛选 + 搜索结果展示。 */

import { onMounted, ref, watch } from 'vue'
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
/** 筛选面板是否展开 */
const filterVisible = ref(true)

onMounted(() => {
  tagsStore.load()
  // 初始加载：无条件展示所有素材
  doSearch()
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
watch(
  () => [tagsStore.selectedTags, tagsStore.excludedTags, tagsStore.combineMode] as const,
  () => {
    if (tagsStore.selectedTags.size > 0 || tagsStore.excludedTags.size > 0) {
      doSearch()
    }
  },
  { deep: false }
)

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
      <!-- 左侧筛选面板（可折叠） -->
      <transition name="slide">
        <aside v-if="filterVisible" class="filter-panel">
          <n-card title="标签筛选" size="small" :bordered="true">
            <template #header-extra>
              <n-button size="tiny" text @click="filterVisible = false" title="隐藏筛选面板">
                收起 ✕
              </n-button>
            </template>
            <TagFilter />
            <n-button
              type="primary"
              block
              style="margin-top: 12px"
              @click="doSearch()"
              :loading="searching"
            >
              搜索
            </n-button>
          </n-card>
        </aside>
      </transition>

      <!-- 右侧结果 -->
      <main class="result-panel">
        <n-card size="small" :bordered="true">
          <template #header>
            <div class="result-header-row">
              <span v-if="!filterVisible">
                <n-button size="tiny" type="primary" secondary @click="filterVisible = true">
                  展开筛选 ◂
                </n-button>
              </span>
              <span v-if="total > 0" class="result-count">
                找到 <strong>{{ total }}</strong> 条结果
                <span v-if="tagsStore.selectedTags.size > 0" style="font-size:12px;color:#999">
                  · 筛选标签:
                  <n-tag
                    v-for="name in [...tagsStore.selectedTags]"
                    :key="name"
                    size="tiny"
                    type="info"
                    closable
                    @close="tagsStore.toggleTag(name)"
                    style="margin-left:4px"
                  >
                    {{ name }}
                  </n-tag>
                </span>
              </span>
              <span v-else style="color:#999">
                点击左侧标签筛选，或直接浏览全部素材
              </span>
            </div>
          </template>
          <MasonryGrid
            :items="results"
            :loading="searching"
            :has-more="false"
            @delete="handleDelete"
            @toggle-favorite="handleToggleFavorite"
          />
        </n-card>
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
  margin-bottom: 16px;
}

.search-layout {
  display: flex;
  gap: 16px;
}

.filter-panel {
  width: 290px;
  flex-shrink: 0;
}

.result-panel {
  flex: 1;
  min-width: 0;
}

.result-header-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.result-count {
  color: #666;
  font-size: 14px;
}

/* 侧边栏折叠动画 */
.slide-enter-active,
.slide-leave-active {
  transition: width 0.25s ease, opacity 0.2s ease;
  overflow: hidden;
}
.slide-enter-from,
.slide-leave-to {
  width: 0;
  opacity: 0;
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
