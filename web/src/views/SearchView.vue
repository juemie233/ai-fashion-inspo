<script setup lang="ts">
/** 高级搜索页：关键词搜索 + 多维标签筛选 + 高级筛选 + 相似推荐 + 搜索历史。 */

import SearchBar from '@/components/search/SearchBar.vue'
import VectorSearchBar from '@/components/search/VectorSearchBar.vue'
import SearchHistoryBar from '@/components/search/SearchHistoryBar.vue'
import SearchContextBar from '@/components/search/SearchContextBar.vue'
import SearchFilterPanel from '@/components/search/SearchFilterPanel.vue'
import SearchResultPanel from '@/components/search/SearchResultPanel.vue'
import { useSearch } from '@/composables/useSearch'

const {
  searchBarRef,
  tagsStore,
  results,
  total,
  searching,
  filterVisible,
  keyword,
  currentPage,
  pageSize,
  sortMode,
  sourceFilter,
  mediaFilter,
  analysisFilter,
  dateFrom,
  dateTo,
  ratingMin,
  density,
  vectorMode,
  semanticText,
  vectorLoading,
  vectorQueryLabel,
  imagePreviewUrl,
  searchHistory,
  vectorBadges,
  totalPages,
  sortOptions,
  copySearchLink,
  doSearch,
  handleSearchBar,
  doSemanticSearch,
  handleImagePicked,
  exitVectorMode,
  handleDelete,
  handleToggleFavorite,
  handleRate,
  applyHistory,
  clearHistory,
} = useSearch()
</script>

<template>
  <div class="search-page">
    <h2>高级搜索</h2>

    <!-- 搜索栏 -->
    <div class="search-section">
      <SearchBar ref="searchBarRef" @search="handleSearchBar" />
    </div>

    <!-- 向量搜索入口：语义搜索 + 以图搜图 -->
    <VectorSearchBar
      v-model:semantic-text="semanticText"
      :loading="vectorLoading"
      @semantic-search="doSemanticSearch"
      @image-picked="handleImagePicked"
    />

    <!-- 搜索历史 -->
    <SearchHistoryBar
      :history="searchHistory"
      :keyword="keyword"
      @apply="applyHistory"
      @clear="clearHistory"
    />

    <!-- 当前筛选信息 -->
    <SearchContextBar
      :keyword="keyword"
      :selected-tags="[...tagsStore.selectedTags]"
      :excluded-tags="[...tagsStore.excludedTags]"
      :combine-mode="tagsStore.combineMode"
    />

    <!-- 标签筛选 + 结果 -->
    <div class="search-layout">
      <!-- 左侧筛选面板 -->
      <SearchFilterPanel
        v-model:visible="filterVisible"
        v-model:source-filter="sourceFilter"
        v-model:media-filter="mediaFilter"
        v-model:analysis-filter="analysisFilter"
        v-model:date-from="dateFrom"
        v-model:date-to="dateTo"
        v-model:rating-filter="ratingMin"
        :searching="searching"
        @filter-change="doSearch(1)"
        @search="doSearch(1)"
      />

      <!-- 右侧结果 -->
      <SearchResultPanel
        v-model:filter-visible="filterVisible"
        v-model:density="density"
        v-model:sort-mode="sortMode"
        :total="total"
        :searching="searching"
        :keyword="keyword"
        :selected-tag-count="tagsStore.selectedTags.size"
        :excluded-tag-count="tagsStore.excludedTags.size"
        :combine-mode="tagsStore.combineMode"
        :results="results"
        :vector-mode="vectorMode"
        :vector-query-label="vectorQueryLabel"
        :image-preview-url="imagePreviewUrl"
        :vector-badges="vectorMode !== 'none' ? vectorBadges : undefined"
        :sort-options="sortOptions"
        :current-page="currentPage"
        :total-pages="totalPages"
        :page-size="pageSize"
        @copy-link="copySearchLink"
        @exit-vector="exitVectorMode"
        @delete="handleDelete"
        @toggle-favorite="handleToggleFavorite"
        @rate="handleRate"
        @sort-change="doSearch(1)"
        @search="doSearch"
        @update:page-size="(s: number) => { pageSize = s; doSearch(1) }"
      />
    </div>
  </div>
</template>

<style scoped>
.search-page {
  max-width: 1800px;
  margin: 0 auto;
}

.search-section {
  margin-bottom: 8px;
}

.search-layout {
  display: flex;
  gap: 16px;
}

@media (max-width: 900px) {
  .search-layout { flex-direction: column; }
}
</style>
