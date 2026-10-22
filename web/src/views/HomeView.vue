<script setup lang="ts">
/** 首页：瀑布流展示素材，支持筛选、排序、密度调节和分页。 */

import { ref, computed, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import MasonryGrid from '@/components/inspiration/MasonryGrid.vue'
import BatchActionBar from '@/components/inspiration/BatchActionBar.vue'
import HomeActiveFilters from '@/components/home/HomeActiveFilters.vue'
import HomeFilterBar from '@/components/home/HomeFilterBar.vue'
import TrashReasonModal from '@/components/inspiration/TrashReasonModal.vue'
import CollectionPickerModal from '@/components/collection/CollectionPickerModal.vue'
import { useInspirationsStore } from '@/stores/inspirations'
import { useTagsStore } from '@/stores/tags'
import { useBatchSelection } from '@/composables/useBatchSelection'
import { useInspirationFilters } from '@/composables/useInspirationFilters'
import { useHomeActions } from '@/composables/useHomeActions'
import { buildBrowseParams, storedBrowsePageSize, PAGE_SIZE_STORAGE_KEY } from '@/utils/browseQuery'

const router = useRouter()
const route = useRoute()
const store = useInspirationsStore()
const tagsStore = useTagsStore()
const {
  batchMode,
  selectedIds,
  selectedCount,
  enterBatchMode,
  exitBatchMode,
  toggleSelect,
  toggleSelectAll,
  batchFavorite,
  batchTrash,
  batchAddTags,
  batchLinkBloggers,
  batchUpdate,
} = useBatchSelection()

// ── 筛选状态（从 URL query 初始化）──

type Density = 'compact' | 'standard' | 'comfortable'

/** 筛选域（状态 + 选项 + setter）：视图自己只用其中一部分，另外整份传给筛选栏组件 */
const filters = useInspirationFilters({
  // 跨域回调：筛选/排序变化 → 退出定位 + 回第一页 + 重新加载 + 同步 URL
  reloadFirstPage: () => onFilterChange(),
  loadPage: (page: number) => loadPage(page),
})

/** 视图直接用到的那部分（构建请求参数 / 同步 URL / 定位模式 / 批量候选标签） */
const {
  sourceFilter,
  mediaFilter,
  statusFilter,
  qualityFilter,
  colorFilter,
  sortMode,
  ratingMin,
  selectedTags,
  focusedIds,
  allTagNames,
  filtersActive,
  loadDominantColors,
  clearFocus,
} = filters

const density = ref<Density>((localStorage.getItem('masonry-density') as Density) || 'standard')

const currentPage = ref(parseInt(route.query.page as string) || 1)
// 每页数量同样持久化，避免刷新后重置（与排序/密度偏好行为一致）
const pageSize = ref(storedBrowsePageSize())
watch(pageSize, (v) => {
  localStorage.setItem(PAGE_SIZE_STORAGE_KEY, String(v))
})

const totalPages = computed(() => Math.ceil(store.total / pageSize.value))

// ── URL 同步 ──

function syncUrl() {
  const query: Record<string, string> = {}
  if (focusedIds.value.length > 0) {
    query.focus = focusedIds.value.join(',')
  }
  if (sourceFilter.value !== 'all') query.source = sourceFilter.value
  if (mediaFilter.value !== 'all') query.media = mediaFilter.value
  if (statusFilter.value !== 'all') query.status = statusFilter.value
  if (qualityFilter.value !== 'all') query.quality = qualityFilter.value
  if (selectedTags.value.length > 0) query.tags = selectedTags.value.join(',')
  if (colorFilter.value) query.color = colorFilter.value
  if (ratingMin.value) query.rating_min = ratingMin.value
  if (sortMode.value !== 'newest') query.sort = sortMode.value
  if (currentPage.value > 1) query.page = String(currentPage.value)
  router.replace({ query })
}

// ── 数据加载 ──

/** 构建列表请求参数：与 DetailView 的浏览上下文共用同一映射，保证筛选排序一致 */
function buildParams(page: number) {
  // 定位模式：仅按 ID 精确展示被定位的素材，忽略其余筛选（保证素材一定可见）
  if (focusedIds.value.length > 0) {
    return { page, size: pageSize.value, ids: focusedIds.value.join(',') }
  }
  return buildBrowseParams(
    {
      source: sourceFilter.value,
      media: mediaFilter.value,
      status: statusFilter.value,
      quality: qualityFilter.value,
      tags: selectedTags.value.join(','),
      color: colorFilter.value,
      rating_min: ratingMin.value,
      sort: sortMode.value,
    },
    page,
    pageSize.value,
  )
}

function loadPage(page: number) {
  currentPage.value = page
  store.load(buildParams(page))
  syncUrl()
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

// ── 筛选/排序变更时重新加载第一页 ──

function onFilterChange() {
  // 定位模式下修改筛选 = 主动退出定位，回到完整列表按新条件浏览
  if (focusedIds.value.length > 0) {
    focusedIds.value = []
    Message.info('已退出定位模式')
  }
  currentPage.value = 1
  store.load(buildParams(1))
  syncUrl()
}

function setDensity(d: Density) {
  density.value = d
  localStorage.setItem('masonry-density', d)
}

const {
  saveCollectionOpen,
  saveCollectionName,
  openSaveCollection,
  confirmSaveCollection,
  collectionPickerOpen,
  handleAddToCollection,
  trashModalOpen,
  pendingTrashIds,
  handleDelete,
  confirmTrash,
  handleBatchTrash,
  handleToggleFavorite,
  handleRate,
  checkingQuality,
  handleBatchQualityCheck,
  handleApprove,
  handleBatchFavorite,
  handleBatchAddTags,
  handleBatchAddBloggers,
  handleBatchUpdate,
} = useHomeActions({
  store,
  tagsStore,
  batch: {
    batchFavorite,
    batchTrash,
    batchAddTags,
    batchLinkBloggers,
    batchUpdate,
    exitBatchMode,
    selectedIds,
  },
  filters: {
    filtersActive,
    sourceFilter,
    mediaFilter,
    statusFilter,
    qualityFilter,
    selectedTags,
    colorFilter,
    ratingMin,
  },
  currentPage,
  loadPage,
})

const currentPageIds = computed(() => store.items.map((i) => i.id))
/** 当前页是否已全选 */
const allSelected = computed(
  () => store.items.length > 0 && store.items.every((i) => selectedIds.value.has(i.id)),
)

/** 批量收藏/取消收藏：成功后同步本地卡片状态 */

// 初始加载（从 URL 恢复页码）+ 预载标签下拉选项与主色调色板
tagsStore.load()
loadDominantColors()
loadPage(currentPage.value)
</script>

<template>
  <div class="home-page">
    <!-- 顶部标题栏 -->
    <div class="page-header">
      <div class="header-left">
        <h2>素材库</h2>
        <span class="total-count">共 {{ store.total }} 条</span>
      </div>
      <div class="header-right">
        <a-button size="small" @click="openSaveCollection">保存为合集</a-button>
        <a-button size="small" :loading="checkingQuality" @click="handleBatchQualityCheck"
          >批量审核</a-button
        >
        <a-button size="small" @click="enterBatchMode()">批量选择</a-button>
      </div>
    </div>

    <!-- 筛选栏（四组按钮筛选 + 标签/评分/颜色 + 排序/密度/打开方式） -->
    <HomeFilterBar
      :filters="filters"
      :density="density"
      @update:density="setDensity"
      @change="onFilterChange"
    />

    <!-- 当前筛选提示 -->
    <HomeActiveFilters :filters="filters" />

    <!-- 定位模式横幅：展示被定位的素材并高亮，可一键退出回到完整列表 -->
    <div v-if="focusedIds.length > 0" class="focus-banner">
      <span>
        已定位 {{ focusedIds.length }} 张素材（来自「素材管理 → 手机图剪裁」跳过明细），
        列表中已高亮，其余素材暂时隐藏
        <template v-if="store.total < focusedIds.length">
          ；素材库中仅找到 {{ store.total }} 张，其余可能已在垃圾桶或文件缺失
        </template>
        ；修改筛选或点击按钮退出定位。
      </span>
      <a-button size="mini" type="primary" @click="clearFocus">清除定位，返回完整列表</a-button>
    </div>

    <!-- 批量选择操作栏 -->
    <BatchActionBar
      v-if="batchMode"
      :count="selectedCount"
      :all-selected="allSelected"
      :tag-options="allTagNames"
      @favorite="handleBatchFavorite"
      @trash="handleBatchTrash"
      @select-all="toggleSelectAll(currentPageIds)"
      @add-tags="handleBatchAddTags"
      @add-bloggers="handleBatchAddBloggers"
      @update="handleBatchUpdate"
      @add-collection="collectionPickerOpen = true"
      @exit="exitBatchMode()"
    />

    <!-- 加入合集选择器（批量操作） -->
    <CollectionPickerModal
      v-model:visible="collectionPickerOpen"
      :inspiration-ids="[...selectedIds]"
      @added="handleAddToCollection"
    />

    <!-- 移入垃圾桶原因选择（单个/批量共用，必选） -->
    <TrashReasonModal
      v-model:visible="trashModalOpen"
      :count="pendingTrashIds.length"
      @confirm="confirmTrash"
    />

    <!-- 保存为智能合集（命名弹窗） -->
    <a-modal v-model:visible="saveCollectionOpen" title="保存为智能合集" :width="460">
      <p v-if="filtersActive" style="color: #999; font-size: 12px; margin-top: 0">
        将把当前筛选条件保存为智能合集，合集内容随素材库动态更新。
      </p>
      <p v-else style="color: #f0a020; font-size: 12px; margin-top: 0">
        当前没有筛选条件——智能合集会匹配全部素材。建议先设置筛选条件。
      </p>
      <a-input
        v-model="saveCollectionName"
        placeholder="合集名称（1~50 字）"
        allow-clear
        max-length="50"
        @keyup.enter="confirmSaveCollection"
      />
      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 8px">
          <a-button size="small" @click="saveCollectionOpen = false">取消</a-button>
          <a-button size="small" type="primary" @click="confirmSaveCollection">保存</a-button>
        </div>
      </template>
    </a-modal>

    <!-- 瀑布流 -->
    <MasonryGrid
      :items="store.items"
      :loading="store.loading"
      :density="density"
      :hover-zoom="qualityFilter === 'ai'"
      :selectable="batchMode"
      :selected-ids="selectedIds"
      :show-view-button="batchMode"
      :show-actions="!batchMode"
      :focused-ids="focusedIds"
      @delete="handleDelete"
      @toggle-favorite="handleToggleFavorite"
      @rate="handleRate"
      @approve="handleApprove"
      @toggle-select="toggleSelect"
    />

    <!-- 分页 -->
    <div v-if="totalPages > 1" class="pagination-wrapper">
      <a-pagination
        v-model:current="currentPage"
        :total="store.total"
        :page-size="pageSize"
        show-page-size
        :page-size-options="[25, 50, 100]"
        @change="loadPage"
        @page-size-change="
          (s: number) => {
            pageSize = s
            loadPage(1)
          }
        "
      />
    </div>
  </div>
</template>

<style scoped>
.home-page {
  max-width: 1800px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.header-left {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.header-left h2 {
  margin: 0;
  font-size: 22px;
  font-weight: 600;
}

.total-count {
  font-size: 14px;
  color: #999;
}

.header-right {
  display: flex;
  gap: 8px;
}

/* 定位模式横幅（裁剪跳过素材跳转） */
.focus-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding: 8px 12px;
  margin-bottom: 10px;
  background: #fff7e6;
  border: 1px solid #ffd591;
  border-radius: 8px;
  font-size: 13px;
  color: #874d00;
}

.pagination-wrapper {
  display: flex;
  justify-content: center;
  padding: 24px 0;
}
</style>
