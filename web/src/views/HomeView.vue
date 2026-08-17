<script setup lang="ts">
/** 首页：瀑布流展示素材，支持筛选、排序、密度调节和分页。 */

import { h, ref, computed, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useMessage, useNotification } from 'naive-ui'
import MasonryGrid from '@/components/inspiration/MasonryGrid.vue'
import BatchActionBar from '@/components/inspiration/BatchActionBar.vue'
import { useInspirationsStore } from '@/stores/inspirations'
import { useTagsStore } from '@/stores/tags'
import { useBatchSelection } from '@/composables/useBatchSelection'
import {
  batchQualityCheck,
  updateQualityStatus,
  fetchDominantColors,
  type DominantColorItem,
} from '@/api/inspirations'
import type { BatchUpdateFields } from '@/api/inspirations'
import {
  buildBrowseParams,
  storedBrowseSort,
  storedBrowsePageSize,
  PAGE_SIZE_STORAGE_KEY,
  parseFocusIds,
} from '@/utils/browseQuery'
import { buildSourceOptions } from '@/utils/sourceLabel'

const router = useRouter()
const route = useRoute()
const message = useMessage()
const notification = useNotification()
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
  batchUpdate,
} = useBatchSelection()

// ── 筛选状态（从 URL query 初始化）──

type SourceFilter =
  'all' | 'manual_upload' | 'scraper' | 'xiaohongshu' | 'douyin' | 'browser_extension'
type MediaFilter = 'all' | 'image' | 'video'
type StatusFilter = 'all' | 'done' | 'pending' | 'untagged' | 'favorites'
type QualityFilter = 'all' | 'pending' | 'approved' | 'rejected' | 'ai'
type SortMode = 'newest' | 'oldest' | 'updated' | 'largest' | 'tag_count' | 'random'
type Density = 'compact' | 'standard' | 'comfortable'

const sourceFilter = ref<SourceFilter>((route.query.source as SourceFilter) || 'all')
const mediaFilter = ref<MediaFilter>((route.query.media as MediaFilter) || 'all')
const statusFilter = ref<StatusFilter>((route.query.status as StatusFilter) || 'all')
const qualityFilter = ref<QualityFilter>((route.query.quality as QualityFilter) || 'all')
const sortMode = ref<SortMode>(
  (route.query.sort as SortMode) || (storedBrowseSort() as SortMode) || 'newest',
)
const density = ref<Density>((localStorage.getItem('masonry-density') as Density) || 'standard')

// 持久化浏览模式（排序，含「随机」）：刷新或再次进入素材库时保持上次的选择
watch(sortMode, (v) => {
  localStorage.setItem('masonry-sort', v)
})

const currentPage = ref(parseInt(route.query.page as string) || 1)
// 每页数量同样持久化，避免刷新后重置（与排序/密度偏好行为一致）
const pageSize = ref(storedBrowsePageSize())
watch(pageSize, (v) => {
  localStorage.setItem(PAGE_SIZE_STORAGE_KEY, String(v))
})

// ── 定位模式（裁剪跳过素材跳转）：/ ?focus=id1,id2 ──
// 从 URL query 恢复待定位素材 ID；定位期间列表仅展示这些素材并高亮，
// 修改任何筛选/排序会退出定位模式，回到完整列表。
const focusedIds = ref<string[]>(parseFocusIds(route.query))

/** 定位模式入口：重置筛选状态，仅按 ID 精确展示被定位的素材 */
function resetFiltersForFocus() {
  sourceFilter.value = 'all'
  mediaFilter.value = 'all'
  statusFilter.value = 'all'
  qualityFilter.value = 'all'
  selectedTags.value = []
  colorFilter.value = ''
  sortMode.value = 'newest'
}

/** 清除定位，回到完整列表 */
function clearFocus() {
  if (focusedIds.value.length === 0) return
  focusedIds.value = []
  loadPage(1)
}

// 同路由内 focus 参数变化（如从其他页面再次跳转定位）：重新进入定位模式
watch(
  () => route.query.focus,
  (v) => {
    const ids = typeof v === 'string' ? parseFocusIds({ focus: v }) : []
    if (ids.join(',') === focusedIds.value.join(',')) return
    focusedIds.value = ids
    if (ids.length > 0) {
      resetFiltersForFocus()
      loadPage(1)
    } else {
      loadPage(currentPage.value)
    }
  },
)

// ── 标签筛选 ──
// 从 URL query 恢复（逗号分隔），刷新/详情返回时保持
const selectedTags = ref<string[]>((route.query.tags as string)?.split(',').filter(Boolean) || [])
// 标签下拉：按类别分组，支持搜索与多选
const tagFilterOptions = computed(() =>
  tagsStore.groups.map((g) => ({
    type: 'group' as const,
    label: tagsStore.getCategoryLabel(g.category),
    key: g.category,
    children: g.tags.map((t) => ({ label: t.name, value: t.name })),
  })),
)
/** 全部已有标签名（供批量加标签候选，避免重复录入） */
const allTagNames = computed(() => tagsStore.groups.flatMap((g) => g.tags.map((t) => t.name)))

// ── 颜色筛选 ──
// 从 URL query 恢复选中的主色调（hex），刷新/详情返回时保持
const colorFilter = ref<string>((route.query.color as string) || '')
/** 库内实际出现的主色调（数据驱动，避免硬编码可能不存在的色板） */
const dominantColors = ref<DominantColorItem[]>([])

async function loadDominantColors() {
  try {
    dominantColors.value = await fetchDominantColors(30)
  } catch {
    dominantColors.value = []
  }
}

// ── 筛选选项配置 ──
// 来源选项由 sourceLabel.ts 统一生成（新增来源类型只改一处）
const sourceOptions = buildSourceOptions('all').map((o) => ({
  ...o,
  value: o.value as SourceFilter,
}))

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
  { label: '疑似 AI', value: 'ai' },
]

const sortOptions: { label: string; value: SortMode }[] = [
  { label: '最新在前', value: 'newest' },
  { label: '最旧在前', value: 'oldest' },
  { label: '最近更新', value: 'updated' },
  { label: '文件最大', value: 'largest' },
  { label: '标签最多', value: 'tag_count' },
  { label: '随机', value: 'random' },
]

const densityOptions: { label: string; value: Density }[] = [
  { label: '紧凑', value: 'compact' },
  { label: '标准', value: 'standard' },
  { label: '宽松', value: 'comfortable' },
]

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
    message.info('已退出定位模式')
  }
  currentPage.value = 1
  store.load(buildParams(1))
  syncUrl()
}

function onSortChange() {
  if (focusedIds.value.length > 0) {
    focusedIds.value = []
    message.info('已退出定位模式')
  }
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
    message.success('已移入垃圾桶')
    if (store.items.length === 0 && currentPage.value > 1) {
      loadPage(currentPage.value - 1)
    }
  } catch {
    message.error('操作失败')
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
      // 后台任务异步执行：用带「查看进度」动作的通知引导用户去任务管理页
      notification.success({
        title: `已提交 ${r.count} 个素材进行质量审核`,
        content: '任务在后台执行，可稍后在任务管理页查看进度与结果',
        duration: 8000,
        action: () =>
          h(
            'a',
            {
              href: '#',
              style: 'color:#2080f0; text-decoration:none',
              onClick: (e: MouseEvent) => {
                e.preventDefault()
                router.push('/tasks')
              },
            },
            '查看进度',
          ),
      })
    }
  } catch {
    message.error('质量审核提交失败')
  } finally {
    checkingQuality.value = false
  }
}

const approvingIds = ref<Set<string>>(new Set())

async function handleApprove(id: string) {
  if (approvingIds.value.has(id)) return
  approvingIds.value = new Set(approvingIds.value).add(id)
  try {
    await updateQualityStatus(id, 'approved')
    message.success('已标记为通过')
    // 若当前筛选为「已拒绝」，从列表剔除并减总数；否则仅更新本地状态
    if (qualityFilter.value === 'rejected') {
      store.items = store.items.filter((i) => i.id !== id)
      store.total = Math.max(0, store.total - 1)
    } else {
      const item = store.items.find((i) => i.id === id)
      if (item) {
        item.quality_status = 'approved'
        item.quality_reason = null
      }
    }
  } catch {
    message.error('操作失败')
  } finally {
    approvingIds.value = new Set(approvingIds.value)
    approvingIds.value.delete(id)
  }
}

// ── 批量多选操作 ──

/** 当前页素材 ID（供全选/取消全选） */
const currentPageIds = computed(() => store.items.map((i) => i.id))
/** 当前页是否已全选 */
const allSelected = computed(
  () => store.items.length > 0 && store.items.every((i) => selectedIds.value.has(i.id)),
)

/** 批量收藏/取消收藏：成功后同步本地卡片状态 */
async function handleBatchFavorite(isFavorite: boolean) {
  const updated = await batchFavorite(isFavorite)
  if (updated > 0) {
    for (const item of store.items) {
      if (selectedIds.value.has(item.id)) item.is_favorite = isFavorite
    }
    exitBatchMode()
  }
}

/** 批量移入垃圾桶：成功后刷新列表 */
async function handleBatchTrash() {
  const trashed = await batchTrash()
  if (trashed > 0) {
    exitBatchMode()
    loadPage(currentPage.value)
  }
}

/** 批量加标签：成功后刷新列表（卡片标签更新） */
async function handleBatchAddTags(names: string[]) {
  await batchAddTags(names)
  exitBatchMode()
  loadPage(currentPage.value)
}

/** 批量编辑元数据：成功后刷新列表 */
async function handleBatchUpdate(fields: BatchUpdateFields) {
  const updated = await batchUpdate(fields)
  if (updated > 0) {
    exitBatchMode()
    loadPage(currentPage.value)
  }
}

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
        <n-button size="small" :loading="checkingQuality" @click="handleBatchQualityCheck"
          >批量审核</n-button
        >
        <n-button size="small" @click="enterBatchMode()">批量选择</n-button>
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
          @click="
            sourceFilter = opt.value
            onFilterChange()
          "
        >
          {{ opt.label }}
        </n-button>
      </div>

      <n-divider vertical style="height: 20px" />

      <!-- 媒体筛选 -->
      <div class="filter-group">
        <n-button
          v-for="opt in mediaOptions"
          :key="opt.value"
          size="tiny"
          :type="mediaFilter === opt.value ? 'primary' : 'default'"
          @click="
            mediaFilter = opt.value
            onFilterChange()
          "
        >
          {{ opt.label }}
        </n-button>
      </div>

      <n-divider vertical style="height: 20px" />

      <!-- 状态筛选 -->
      <div class="filter-group">
        <n-button
          v-for="opt in statusOptions"
          :key="opt.value"
          size="tiny"
          :type="statusFilter === opt.value ? 'primary' : 'default'"
          @click="
            statusFilter = opt.value
            onFilterChange()
          "
        >
          {{ opt.label }}
        </n-button>
      </div>

      <n-divider vertical style="height: 20px" />

      <!-- 质量审核筛选 -->
      <div class="filter-group">
        <n-button
          v-for="opt in qualityOptions"
          :key="opt.value"
          size="tiny"
          :type="qualityFilter === opt.value ? 'primary' : 'default'"
          @click="
            qualityFilter = opt.value
            onFilterChange()
          "
        >
          {{ opt.label }}
        </n-button>
      </div>

      <n-divider vertical style="height: 20px" />

      <!-- 标签筛选（多选，需同时包含） -->
      <div class="filter-group">
        <n-select
          v-model:value="selectedTags"
          multiple
          filterable
          clearable
          :options="tagFilterOptions"
          placeholder="按标签筛选"
          size="tiny"
          style="width: 220px"
          @update:value="onFilterChange"
        />
      </div>

      <n-divider vertical style="height: 20px" />

      <!-- 颜色筛选（主色调色板，数据驱动） -->
      <div v-if="dominantColors.length > 0" class="filter-group color-filter" title="按主色调筛选">
        <span
          v-for="c in dominantColors"
          :key="c.color"
          class="color-swatch"
          :class="{ active: colorFilter === c.color }"
          :style="{ background: c.color }"
          :title="`${c.color}（${c.count} 个素材）`"
          @click="
            colorFilter = colorFilter === c.color ? '' : c.color
            onFilterChange()
          "
        />
        <n-button
          v-if="colorFilter"
          size="tiny"
          quaternary
          @click="
            colorFilter = ''
            onFilterChange()
          "
        >
          清除颜色
        </n-button>
      </div>

      <div style="flex: 1" />

      <!-- 排序 + 密度 -->
      <div class="control-group">
        <n-select
          v-model:value="sortMode"
          :options="sortOptions"
          size="tiny"
          style="width: 110px"
          @update:value="onSortChange"
        />

        <n-button-group size="tiny">
          <n-button
            v-for="d in densityOptions"
            :key="d.value"
            :type="density === d.value ? 'primary' : 'default'"
            @click="setDensity(d.value)"
          >
            {{ d.label }}
          </n-button>
        </n-button-group>
      </div>
    </div>

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
      <n-tag
        v-if="sourceFilter !== 'all'"
        size="tiny"
        closable
        @close="
          sourceFilter = 'all'
          onFilterChange()
        "
      >
        {{ sourceOptions.find((o) => o.value === sourceFilter)?.label }}
      </n-tag>
      <n-tag
        v-if="mediaFilter !== 'all'"
        size="tiny"
        closable
        @close="
          mediaFilter = 'all'
          onFilterChange()
        "
      >
        {{ mediaOptions.find((o) => o.value === mediaFilter)?.label }}
      </n-tag>
      <n-tag
        v-if="statusFilter !== 'all'"
        size="tiny"
        closable
        @close="
          statusFilter = 'all'
          onFilterChange()
        "
      >
        {{ statusOptions.find((o) => o.value === statusFilter)?.label }}
      </n-tag>
      <n-tag
        v-if="qualityFilter !== 'all'"
        size="tiny"
        closable
        @close="
          qualityFilter = 'all'
          onFilterChange()
        "
      >
        {{ qualityOptions.find((o) => o.value === qualityFilter)?.label }}
      </n-tag>
      <n-tag
        v-for="tag in selectedTags"
        :key="tag"
        size="tiny"
        closable
        @close="
          selectedTags = selectedTags.filter((t) => t !== tag)
          onFilterChange()
        "
      >
        {{ tag }}
      </n-tag>
      <n-tag
        v-if="colorFilter"
        size="tiny"
        closable
        @close="
          colorFilter = ''
          onFilterChange()
        "
      >
        <span class="color-chip" :style="{ background: colorFilter }" /> {{ colorFilter }}
      </n-tag>
      <n-tag
        v-if="sortMode !== 'newest'"
        size="tiny"
        closable
        @close="
          sortMode = 'newest'
          onSortChange()
        "
      >
        {{ sortOptions.find((o) => o.value === sortMode)?.label }}
      </n-tag>
      <n-button
        size="tiny"
        text
        @click="
          sourceFilter = 'all'
          mediaFilter = 'all'
          statusFilter = 'all'
          qualityFilter = 'all'
          selectedTags = []
          colorFilter = ''
          sortMode = 'newest'
          onFilterChange()
        "
      >
        清除全部
      </n-button>
    </div>

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
      <n-button size="tiny" type="primary" @click="clearFocus">清除定位，返回完整列表</n-button>
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
      @update="handleBatchUpdate"
      @exit="exitBatchMode()"
    />

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
      @approve="handleApprove"
      @toggle-select="toggleSelect"
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
        @update:page-size="
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

/* 颜色筛选色板 */
.color-filter {
  align-items: center;
}
.color-swatch {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  cursor: pointer;
  border: 2px solid transparent;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.08);
  transition:
    transform 0.1s,
    border-color 0.1s;
}
.color-swatch:hover {
  transform: scale(1.15);
}
.color-swatch.active {
  border-color: #2080f0;
}
.color-chip {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  margin-right: 4px;
  vertical-align: middle;
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
