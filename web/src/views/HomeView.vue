<script setup lang="ts">
/** 首页：瀑布流展示素材，支持筛选、排序、密度调节和分页。 */

import { ref, computed, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useMessage } from 'naive-ui'
import MasonryGrid from '@/components/inspiration/MasonryGrid.vue'
import { useInspirationsStore } from '@/stores/inspirations'
import { batchQualityCheck, updateQualityStatus } from '@/api/inspirations'

const router = useRouter()
const route = useRoute()
const message = useMessage()
const store = useInspirationsStore()

// ── 筛选状态（从 URL query 初始化）──

type SourceFilter = 'all' | 'manual_upload' | 'scraper' | 'xiaohongshu' | 'douyin' | 'browser_extension'
type MediaFilter = 'all' | 'image' | 'video'
type StatusFilter = 'all' | 'done' | 'pending' | 'untagged' | 'favorites'
type QualityFilter = 'all' | 'pending' | 'approved' | 'rejected'
type SortMode = 'newest' | 'oldest' | 'updated' | 'largest'
type Density = 'compact' | 'standard' | 'comfortable'

const sourceFilter = ref<SourceFilter>((route.query.source as SourceFilter) || 'all')
const mediaFilter = ref<MediaFilter>((route.query.media as MediaFilter) || 'all')
const statusFilter = ref<StatusFilter>((route.query.status as StatusFilter) || 'all')
const qualityFilter = ref<QualityFilter>((route.query.quality as QualityFilter) || 'all')
const sortMode = ref<SortMode>((route.query.sort as SortMode) || 'newest')
const density = ref<Density>((localStorage.getItem('masonry-density') as Density) || 'standard')

const currentPage = ref(1)
const pageSize = ref(50)

// ── 筛选选项配置 ──

const sourceOptions: { label: string; value: SourceFilter }[] = [
  { label: '全部来源', value: 'all' },
  { label: '手动上传', value: 'manual_upload' },
  { label: '自动采集', value: 'scraper' },
  { label: '小红书', value: 'xiaohongshu' },
  { label: '抖音', value: 'douyin' },
  { label: '浏览器插件', value: 'browser_extension' },
]

const mediaOptions: { label: string; value: MediaFilter }[] = [
  { label: '全部', value: 'all' },
  { label: '图片', value: 'image' },
  { label: '视频', value: 'video' },
]

const statusOptions: { label: string; value: StatusFilter }[] = [
  { label: '全部状态', value: 'all' },
  { label: '已分析', value: 'done' },
  { label: '未分析', value: 'pending' },
  { label: '无标签', value: 'untagged' },
  { label: '仅收藏', value: 'favorites' },
]

const qualityOptions: { label: string; value: QualityFilter }[] = [
  { label: '全部审核', value: 'all' },
  { label: '待审核', value: 'pending' },
  { label: '已通过', value: 'approved' },
  { label: '已拒绝', value: 'rejected' },
]

const sortOptions: { label: string; value: SortMode }[] = [
  { label: '最新在前', value: 'newest' },
  { label: '最旧在前', value: 'oldest' },
  { label: '最近更新', value: 'updated' },
  { label: '文件最大', value: 'largest' },
]

const densityOptions: { label: string; value: Density; icon: string }[] = [
  { label: '紧凑', value: 'compact', icon: '⊞' },
  { label: '标准', value: 'standard', icon: '⊟' },
  { label: '宽松', value: 'comfortable', icon: '⊠' },
]

const totalPages = computed(() => Math.ceil(store.total / pageSize.value))

// ── URL 同步 ──

function syncUrl() {
  const query: Record<string, string> = {}
  if (sourceFilter.value !== 'all') query.source = sourceFilter.value
  if (mediaFilter.value !== 'all') query.media = mediaFilter.value
  if (statusFilter.value !== 'all') query.status = statusFilter.value
  if (qualityFilter.value !== 'all') query.quality = qualityFilter.value
  if (sortMode.value !== 'newest') query.sort = sortMode.value
  router.replace({ query })
}

// ── 数据加载 ──

function buildParams(page: number) {
  return {
    page,
    size: pageSize.value,
    source_type: sourceFilter.value !== 'all' ? sourceFilter.value : undefined,
    media_type: mediaFilter.value !== 'all' ? mediaFilter.value : undefined,
    is_favorite: statusFilter.value === 'favorites' ? true : undefined,
    analysis_status: (statusFilter.value === 'done' || statusFilter.value === 'pending') ? statusFilter.value : undefined,
    tag_status: statusFilter.value === 'untagged' ? 'untagged' : undefined,
    quality_status: qualityFilter.value !== 'all' ? qualityFilter.value : undefined,
    sort: sortMode.value,
  }
}

function loadPage(page: number) {
  currentPage.value = page
  store.load(buildParams(page))
  syncUrl()
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

// ── 筛选/排序变更时重新加载第一页 ──

function onFilterChange() {
  currentPage.value = 1
  store.load(buildParams(1))
  syncUrl()
}

function onSortChange() {
  currentPage.value = 1
  store.load(buildParams(1))
  syncUrl()
}

function setDensity(d: Density) {
  density.value = d
  localStorage.setItem('masonry-density', d)
}

// ── 删除/收藏 ──

async function handleDelete(id: string) {
  try {
    await store.remove(id)
    message.success('已删除')
    if (store.items.length === 0 && currentPage.value > 1) {
      loadPage(currentPage.value - 1)
    }
  } catch {
    message.error('删除失败')
  }
}

async function handleToggleFavorite(id: string) {
  try {
    await store.toggleFavorite(id)
  } catch {
    message.error('操作失败')
  }
}

// ── 质量审核 ──

const checkingQuality = ref(false)

async function handleBatchQualityCheck() {
  checkingQuality.value = true
  try {
    const r = await batchQualityCheck(200)
    if (r.count === 0) {
      message.info('没有待审核的素材')
    } else {
      message.success(`已提交 ${r.count} 个素材进行质量审核，稍后刷新查看结果`)
    }
  } catch {
    message.error('质量审核提交失败')
  } finally {
    checkingQuality.value = false
  }
}

async function handleApprove(id: string) {
  try {
    await updateQualityStatus(id, 'approved')
    message.success('已标记为通过')
    // 更新本地状态，无需重新加载
    const item = store.items.find((i) => i.id === id)
    if (item) {
      item.quality_status = 'approved'
      item.quality_reason = null
    }
  } catch {
    message.error('操作失败')
  }
}

// 初始加载
loadPage(1)
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
        <n-button size="small" :loading="checkingQuality" @click="handleBatchQualityCheck">批量审核</n-button>
        <n-button type="primary" @click="router.push('/upload')">上传素材</n-button>
      </div>
    </div>

    <!-- 筛选栏 -->
    <div class="filter-bar">
      <!-- 来源筛选 -->
      <div class="filter-group">
        <n-button
          v-for="opt in sourceOptions"
          :key="opt.value"
          size="tiny"
          :type="sourceFilter === opt.value ? 'primary' : 'default'"
          @click="sourceFilter = opt.value; onFilterChange()"
        >
          {{ opt.label }}
        </n-button>
      </div>

      <n-divider vertical style="height:20px" />

      <!-- 媒体筛选 -->
      <div class="filter-group">
        <n-button
          v-for="opt in mediaOptions"
          :key="opt.value"
          size="tiny"
          :type="mediaFilter === opt.value ? 'primary' : 'default'"
          @click="mediaFilter = opt.value; onFilterChange()"
        >
          {{ opt.label }}
        </n-button>
      </div>

      <n-divider vertical style="height:20px" />

      <!-- 状态筛选 -->
      <div class="filter-group">
        <n-button
          v-for="opt in statusOptions"
          :key="opt.value"
          size="tiny"
          :type="statusFilter === opt.value ? 'primary' : 'default'"
          @click="statusFilter = opt.value; onFilterChange()"
        >
          {{ opt.label }}
        </n-button>
      </div>

      <n-divider vertical style="height:20px" />

      <!-- 质量审核筛选 -->
      <div class="filter-group">
        <n-button
          v-for="opt in qualityOptions"
          :key="opt.value"
          size="tiny"
          :type="qualityFilter === opt.value ? 'primary' : 'default'"
          @click="qualityFilter = opt.value; onFilterChange()"
        >
          {{ opt.label }}
        </n-button>
      </div>

      <div style="flex:1" />

      <!-- 排序 + 密度 -->
      <div class="control-group">
        <n-select
          v-model:value="sortMode"
          :options="sortOptions"
          size="tiny"
          style="width:110px"
          @update:value="onSortChange"
        />

        <n-button-group size="tiny">
          <n-button
            v-for="d in densityOptions"
            :key="d.value"
            :type="density === d.value ? 'primary' : 'default'"
            :title="d.label"
            @click="setDensity(d.value)"
          >
            {{ d.icon }}
          </n-button>
        </n-button-group>
      </div>
    </div>

    <!-- 当前筛选提示 -->
    <div v-if="sourceFilter !== 'all' || mediaFilter !== 'all' || statusFilter !== 'all' || qualityFilter !== 'all' || sortMode !== 'newest'" class="active-filters">
      当前筛选：
      <n-tag v-if="sourceFilter !== 'all'" size="tiny" closable @close="sourceFilter = 'all'; onFilterChange()">
        {{ sourceOptions.find(o => o.value === sourceFilter)?.label }}
      </n-tag>
      <n-tag v-if="mediaFilter !== 'all'" size="tiny" closable @close="mediaFilter = 'all'; onFilterChange()">
        {{ mediaOptions.find(o => o.value === mediaFilter)?.label }}
      </n-tag>
      <n-tag v-if="statusFilter !== 'all'" size="tiny" closable @close="statusFilter = 'all'; onFilterChange()">
        {{ statusOptions.find(o => o.value === statusFilter)?.label }}
      </n-tag>
      <n-tag v-if="qualityFilter !== 'all'" size="tiny" closable @close="qualityFilter = 'all'; onFilterChange()">
        {{ qualityOptions.find(o => o.value === qualityFilter)?.label }}
      </n-tag>
      <n-tag v-if="sortMode !== 'newest'" size="tiny" closable @close="sortMode = 'newest'; onSortChange()">
        {{ sortOptions.find(o => o.value === sortMode)?.label }}
      </n-tag>
      <n-button size="tiny" text @click="sourceFilter='all';mediaFilter='all';statusFilter='all';qualityFilter='all';sortMode='newest';onFilterChange()">
        清除全部
      </n-button>
    </div>

    <!-- 瀑布流 -->
    <MasonryGrid
      :items="store.items"
      :loading="store.loading"
      :density="density"
      @delete="handleDelete"
      @toggle-favorite="handleToggleFavorite"
      @approve="handleApprove"
    />

    <!-- 分页 -->
    <div v-if="totalPages > 1" class="pagination-wrapper">
      <n-pagination
        v-model:page="currentPage"
        :page-count="totalPages"
        :page-size="pageSize"
        show-size-picker
        :page-sizes="[25, 50, 100]"
        @update:page="loadPage"
        @update:page-size="(s: number) => { pageSize = s; loadPage(1) }"
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

/* 筛选栏 */
.filter-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  margin-bottom: 8px;
  background: #f8f9fa;
  border-radius: 8px;
  flex-wrap: wrap;
}

.filter-group {
  display: flex;
  gap: 4px;
}

.control-group {
  display: flex;
  align-items: center;
  gap: 6px;
}

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

.pagination-wrapper {
  display: flex;
  justify-content: center;
  padding: 24px 0;
}
</style>
