<script setup lang="ts">
/** 首页：瀑布流展示素材，支持筛选、排序、密度调节和分页。 */

import { ref, computed, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { Message, Notification } from '@arco-design/web-vue'
import MasonryGrid from '@/components/inspiration/MasonryGrid.vue'
import BatchActionBar from '@/components/inspiration/BatchActionBar.vue'
import TrashReasonModal from '@/components/inspiration/TrashReasonModal.vue'
import CollectionPickerModal from '@/components/collection/CollectionPickerModal.vue'
import SmartQueryEditorModal from '@/components/collection/SmartQueryEditorModal.vue'
import { useInspirationsStore } from '@/stores/inspirations'
import { useTagsStore } from '@/stores/tags'
import { useBatchSelection } from '@/composables/useBatchSelection'
import { getApiErrorMessage } from '@/utils/apiError'
import {
  batchQualityCheck,
  updateQualityStatus,
  fetchDominantColors,
  type DominantColorItem,
  type TrashReason,
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
import { createCollection } from '@/api/collections'
import { buildSmartQuery, hasActiveFilters, type BrowseFilterState } from '@/utils/collectionQuery'

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

type SourceFilter =
  'all' | 'manual_upload' | 'scraper' | 'xiaohongshu' | 'douyin' | 'browser_extension'
type MediaFilter = 'all' | 'image' | 'video'
type StatusFilter = 'all' | 'done' | 'pending' | 'untagged' | 'favorites'
type QualityFilter = 'all' | 'pending' | 'approved' | 'rejected' | 'ai'
type SortMode =
  'newest' | 'oldest' | 'updated' | 'largest' | 'tag_count' | 'random' | 'rating' | 'rating_asc'
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
  ratingMin.value = ''
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

// ── 评分筛选 ──
// 从 URL query 恢复（rating >= 指定值），刷新/详情返回时保持
const ratingMin = ref<string>((route.query.rating_min as string) || '')

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
  { label: '评分最高', value: 'rating' },
  { label: '评分最低', value: 'rating_asc' },
  { label: '文件最大', value: 'largest' },
  { label: '标签最多', value: 'tag_count' },
  { label: '随机', value: 'random' },
]

/** 评分筛选选项（rating >= 指定值） */
const ratingOptions: { label: string; value: string }[] = [
  { label: '全部评分', value: '' },
  { label: '★ 1 分及以上', value: '1' },
  { label: '★ 2 分及以上', value: '2' },
  { label: '★ 3 分及以上', value: '3' },
  { label: '★ 4 分及以上', value: '4' },
  { label: '★ 5 分', value: '5' },
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

function onSortChange() {
  if (focusedIds.value.length > 0) {
    focusedIds.value = []
    Message.info('已退出定位模式')
  }
  currentPage.value = 1
  store.load(buildParams(1))
  syncUrl()
}

// ── 筛选/排序快捷入口 ──
// 模板事件统一走函数：内联多语句表达式（a = b; fn()）会被格式化工具拆成
// 换行形式导致 Vue 模板编译失败，故全部收敛为具名函数。

function setSourceFilter(v: SourceFilter) {
  sourceFilter.value = v
  onFilterChange()
}

function setMediaFilter(v: MediaFilter) {
  mediaFilter.value = v
  onFilterChange()
}

function setStatusFilter(v: StatusFilter) {
  statusFilter.value = v
  onFilterChange()
}

function setQualityFilter(v: QualityFilter) {
  qualityFilter.value = v
  onFilterChange()
}

function setColorFilter(v: string) {
  colorFilter.value = v
  onFilterChange()
}

function setSortMode(v: SortMode) {
  sortMode.value = v
  onSortChange()
}

/** 移除单个标签筛选 */
function removeTagFilter(tag: string) {
  selectedTags.value = selectedTags.value.filter((t) => t !== tag)
  onFilterChange()
}

/** 清除全部筛选（不含定位模式） */
function clearAllFilters() {
  sourceFilter.value = 'all'
  mediaFilter.value = 'all'
  statusFilter.value = 'all'
  qualityFilter.value = 'all'
  selectedTags.value = []
  colorFilter.value = ''
  sortMode.value = 'newest'
  onFilterChange()
}

function setDensity(d: Density) {
  density.value = d
  localStorage.setItem('masonry-density', d)
}

// ── 保存为合集（智能合集：序列化当前筛选条件） ──

const saveCollectionOpen = ref(false)
const saveCollectionName = ref('')

/** 当前筛选是否含实质条件（无条件下保存=动态全库合集，需二次确认） */
const filtersActive = computed(() =>
  hasActiveFilters({
    source: sourceFilter.value,
    media: mediaFilter.value,
    status: statusFilter.value,
    quality: qualityFilter.value,
    tags: selectedTags.value,
    color: colorFilter.value,
    ratingMin: ratingMin.value,
    keyword: '',
  } satisfies BrowseFilterState),
)

function openSaveCollection() {
  saveCollectionName.value = ''
  saveCollectionOpen.value = true
}

async function confirmSaveCollection() {
  const name = saveCollectionName.value.trim()
  if (!name) {
    Message.warning('请输入合集名称')
    return
  }
  if (!filtersActive.value) {
    // 无条件 = 动态匹配全库，价值低且易误解，引导先筛选
    Message.warning('请先设置筛选条件，再保存为智能合集')
    return
  }
  const nameToId = (tagName: string) =>
    tagsStore.groups.flatMap((g) => g.tags).find((t) => t.name === tagName)?.id
  const query = buildSmartQuery(
    {
      source: sourceFilter.value,
      media: mediaFilter.value,
      status: statusFilter.value,
      quality: qualityFilter.value,
      tags: selectedTags.value,
      color: colorFilter.value,
      ratingMin: ratingMin.value,
      keyword: '',
    },
    nameToId,
  )
  try {
    await createCollection({ name, query_json: query })
    Message.success(`已创建智能合集「${name}」，可在「收藏合集」页查看`)
  } catch (e) {
    Message.error(getApiErrorMessage(e, '保存为合集失败'))
    return
  }
  saveCollectionOpen.value = false
}

// ── 批量加入合集 ──

const collectionPickerOpen = ref(false)

/** 批量加入合集：成功后退出批量模式（成员关系变化无需刷新卡片） */
function handleAddToCollection(_collectionId: number, _added: number) {
  exitBatchMode()
}

// ── 删除/收藏 ──

// 移入垃圾桶前必选原因：单个删除与批量删除共用一个弹窗
const trashModalOpen = ref(false)
const pendingTrashIds = ref<string[]>([])

function handleDelete(id: string) {
  pendingTrashIds.value = [id]
  trashModalOpen.value = true
}

/** 弹窗确认：按所选原因移入垃圾桶 */
async function confirmTrash(reason: TrashReason) {
  trashModalOpen.value = false
  const ids = [...pendingTrashIds.value]
  pendingTrashIds.value = []
  if (ids.length === 0) return
  try {
    if (ids.length === 1) {
      await store.remove(ids[0], reason)
      Message.success('已移入垃圾桶')
      if (store.items.length === 0 && currentPage.value > 1) {
        loadPage(currentPage.value - 1)
      }
    } else {
      const trashed = await batchTrash(reason)
      if (trashed > 0) {
        exitBatchMode()
        loadPage(currentPage.value)
      }
    }
  } catch {
    Message.error('操作失败')
  }
}

/** 批量移入垃圾桶：打开原因选择弹窗（确认后走 confirmTrash） */
function handleBatchTrash() {
  pendingTrashIds.value = [...selectedIds.value]
  if (pendingTrashIds.value.length === 0) return
  trashModalOpen.value = true
}

async function handleToggleFavorite(id: string) {
  try {
    await store.toggleFavorite(id)
  } catch {
    Message.error('操作失败')
  }
}

/** 设置评分：同步 store（列表项与详情） */
async function handleRate(id: string, value: number) {
  try {
    await store.setRating(id, value)
    Message.success(value > 0 ? `已评分 ${value} 星` : '已清除评分')
  } catch (e) {
    Message.error(getApiErrorMessage(e, '评分失败'))
  }
}

// ── 质量审核 ──

const checkingQuality = ref(false)

async function handleBatchQualityCheck() {
  checkingQuality.value = true
  try {
    const r = await batchQualityCheck(200)
    if (r.count === 0) {
      Message.info('没有待审核的素材')
    } else {
      // 后台任务异步执行：用带「查看进度」动作的通知引导用户去任务管理页
      Notification.success({
        title: `已提交 ${r.count} 个素材进行质量审核`,
        content: '任务在后台执行，可稍后在任务管理页查看进度与结果',
        duration: 8000,
      })
    }
  } catch {
    Message.error('质量审核提交失败')
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
    Message.success('已标记为通过')
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
    Message.error('操作失败')
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

/** 批量加标签：成功后刷新列表（卡片标签更新） */
async function handleBatchAddTags(names: string[]) {
  await batchAddTags(names)
  exitBatchMode()
  loadPage(currentPage.value)
}

/** 批量关联穿搭博主：成功后刷新列表（卡片博主信息更新） */
async function handleBatchAddBloggers(personIds: number[]) {
  await batchLinkBloggers(personIds)
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
        <a-button size="small" @click="openSaveCollection">保存为合集</a-button>
        <a-button size="small" :loading="checkingQuality" @click="handleBatchQualityCheck"
          >批量审核</a-button
        >
        <a-button size="small" @click="enterBatchMode()">批量选择</a-button>
      </div>
    </div>

    <!-- 筛选栏 -->
    <div class="filter-bar">
      <!-- 来源筛选 -->
      <div class="filter-group">
        <a-button
          v-for="opt in sourceOptions"
          :key="opt.value"
          size="mini"
          :type="sourceFilter === opt.value ? 'primary' : 'secondary'"
          @click="setSourceFilter(opt.value)"
        >
          {{ opt.label }}
        </a-button>
      </div>

      <a-divider direction="vertical" style="height: 20px" />

      <!-- 媒体筛选 -->
      <div class="filter-group">
        <a-button
          v-for="opt in mediaOptions"
          :key="opt.value"
          size="mini"
          :type="mediaFilter === opt.value ? 'primary' : 'secondary'"
          @click="setMediaFilter(opt.value)"
        >
          {{ opt.label }}
        </a-button>
      </div>

      <a-divider direction="vertical" style="height: 20px" />

      <!-- 状态筛选 -->
      <div class="filter-group">
        <a-button
          v-for="opt in statusOptions"
          :key="opt.value"
          size="mini"
          :type="statusFilter === opt.value ? 'primary' : 'secondary'"
          @click="setStatusFilter(opt.value)"
        >
          {{ opt.label }}
        </a-button>
      </div>

      <a-divider direction="vertical" style="height: 20px" />

      <!-- 质量审核筛选 -->
      <div class="filter-group">
        <a-button
          v-for="opt in qualityOptions"
          :key="opt.value"
          size="mini"
          :type="qualityFilter === opt.value ? 'primary' : 'secondary'"
          @click="setQualityFilter(opt.value)"
        >
          {{ opt.label }}
        </a-button>
      </div>

      <a-divider direction="vertical" style="height: 20px" />

      <!-- 标签筛选（多选，需同时包含） -->
      <div class="filter-group">
        <a-select
          v-model="selectedTags"
          multiple
          filterable
          allow-clear
          :options="tagFilterOptions"
          placeholder="按标签筛选"
          size="mini"
          style="width: 220px"
          @change="onFilterChange"
        />
      </div>

      <a-divider direction="vertical" style="height: 20px" />

      <!-- 颜色筛选（主色调色板，数据驱动） -->
      <div v-if="dominantColors.length > 0" class="filter-group color-filter" title="按主色调筛选">
        <span
          v-for="c in dominantColors"
          :key="c.color"
          class="color-swatch"
          :class="{ active: colorFilter === c.color }"
          :style="{ background: c.color }"
          :title="`${c.color}（${c.count} 个素材）`"
          @click="setColorFilter(colorFilter === c.color ? '' : c.color)"
        />
        <a-button v-if="colorFilter" size="mini" type="text" @click="setColorFilter('')">
          清除颜色
        </a-button>
      </div>

      <a-divider v-if="dominantColors.length > 0" direction="vertical" style="height: 20px" />

      <!-- 评分筛选（评分 >= 指定值） -->
      <div class="filter-group">
        <a-select
          v-model="ratingMin"
          :options="ratingOptions"
          placeholder="评分筛选"
          size="mini"
          style="width: 130px"
          @change="onFilterChange"
        />
      </div>

      <div style="flex: 1" />

      <!-- 排序 + 密度 -->
      <div class="control-group">
        <a-select
          v-model="sortMode"
          :options="sortOptions"
          size="mini"
          style="width: 110px"
          @change="onSortChange"
        />

        <a-button-group size="mini">
          <a-button
            v-for="d in densityOptions"
            :key="d.value"
            :type="density === d.value ? 'primary' : 'secondary'"
            @click="setDensity(d.value)"
          >
            {{ d.label }}
          </a-button>
        </a-button-group>
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
      <a-tag v-if="sourceFilter !== 'all'" size="small" closable @close="setSourceFilter('all')">
        {{ sourceOptions.find((o) => o.value === sourceFilter)?.label }}
      </a-tag>
      <a-tag v-if="mediaFilter !== 'all'" size="small" closable @close="setMediaFilter('all')">
        {{ mediaOptions.find((o) => o.value === mediaFilter)?.label }}
      </a-tag>
      <a-tag v-if="statusFilter !== 'all'" size="small" closable @close="setStatusFilter('all')">
        {{ statusOptions.find((o) => o.value === statusFilter)?.label }}
      </a-tag>
      <a-tag v-if="qualityFilter !== 'all'" size="small" closable @close="setQualityFilter('all')">
        {{ qualityOptions.find((o) => o.value === qualityFilter)?.label }}
      </a-tag>
      <a-tag
        v-for="tag in selectedTags"
        :key="tag"
        size="small"
        closable
        @close="removeTagFilter(tag)"
      >
        {{ tag }}
      </a-tag>
      <a-tag v-if="colorFilter" size="small" closable @close="setColorFilter('')">
        <span class="color-chip" :style="{ background: colorFilter }" /> {{ colorFilter }}
      </a-tag>
      <a-tag v-if="sortMode !== 'newest'" size="small" closable @close="setSortMode('newest')">
        {{ sortOptions.find((o) => o.value === sortMode)?.label }}
      </a-tag>
      <a-button size="mini" type="text" @click="clearAllFilters"> 清除全部 </a-button>
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
