<script setup lang="ts">
/** 收藏合集页：左侧合集列表（拖拽排序）+ 右侧合集内容瀑布流。
 *
 * 手动合集：瀑布流 + 批量多选移出 + 「编辑排序」模式（缩略图条带拖拽编排展示顺序）；
 * 智能合集：徽标 + 条件摘要，无加入/移出/排序（内容由条件动态决定），
 * 支持「编辑条件」「转手动（固化）」。
 */

import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import MasonryGrid from '@/components/inspiration/MasonryGrid.vue'
import CollectionContentHeader from '@/components/collection/CollectionContentHeader.vue'
import CollectionOrderingStrip from '@/components/collection/CollectionOrderingStrip.vue'
import CollectionSidebar from '@/components/collection/CollectionSidebar.vue'
import CollectionTargetPickerModal from '@/components/collection/CollectionTargetPickerModal.vue'
import SmartQueryEditorModal from '@/components/collection/SmartQueryEditorModal.vue'
import {
  fetchCollectionInspirations,
  fetchCollections,
  mergeCollections,
  removeFromCollection,
  transferCollectionItems,
  type CollectionOut,
} from '@/api/collections'
import {
  CONTENT_SORT_OPTIONS,
  MEDIA_OPTIONS,
  QUALITY_OPTIONS,
  RATING_OPTIONS,
  buildCollectionContentParams,
  contentSortLabel as formatContentSortLabel,
  defaultContentSort,
  isContentFilterActive,
  type ContentFilterState,
  type ContentMedia,
  type ContentQuality,
  type ContentRatingMin,
  type ContentSort,
} from '@/components/collection/contentFilters'
import { getApiErrorMessage } from '@/utils/apiError'
import { SOURCE_TYPE_LABELS } from '@/utils/sourceLabel'
import { getFileUrl, toggleFavorite } from '@/api/inspirations'
import { describeSmartQuery } from '@/utils/collectionQuery'
import { useTagsStore } from '@/stores/tags'
import { useBatchSelection } from '@/composables/useBatchSelection'
import { useCollectionMutations } from '@/composables/useCollectionMutations'
import { useCollectionListDnd } from '@/composables/useCollectionListDnd'
import { useCollectionItemOrdering } from '@/composables/useCollectionItemOrdering'
import { usePageTitle } from '@/composables/usePageTitle'
import type { InspirationOut } from '@/api/inspirations'

const tagsStore = useTagsStore()
const {
  batchMode,
  selectedIds,
  selectedCount,
  enterBatchMode,
  exitBatchMode,
  toggleSelect,
  toggleSelectAll,
} = useBatchSelection()

// ── 合集列表 ──

const collections = ref<CollectionOut[]>([])
const listLoading = ref(false)
const currentId = ref<number | null>(null)
const current = computed(() => collections.value.find((c) => c.id === currentId.value) ?? null)

// 浏览器标题：收藏合集-<合集名>（合集是页面内选中态、不在 URL 里，故由视图维护）
usePageTitle(() => (current.value ? `收藏合集-${current.value.name}` : '收藏合集'))
/** 当前二级收藏夹所属一级的名字（一级自己的内容区不显示面包屑） */
const parentName = computed(() => {
  const parentId = current.value?.parent_id
  if (parentId == null) return null
  return collections.value.find((c) => c.id === parentId)?.name ?? null
})
/** 按父级分组：parent_id → 直属二级收藏夹（侧栏与内容区共用同一份，避免两处各写一遍过滤） */
const childrenByParent = computed(() => {
  const map = new Map<number, CollectionOut[]>()
  for (const c of collections.value) {
    if (c.parent_id === null) continue
    const list = map.get(c.parent_id)
    if (list) list.push(c)
    else map.set(c.parent_id, [c])
  }
  return map
})

/** 当前合集的二级收藏夹（非空 = 这是分类节点：内容区展示收藏夹卡片） */
const currentChildren = computed(() => {
  const id = current.value?.id
  return id == null ? [] : (childrenByParent.value.get(id) ?? [])
})

async function loadCollections() {
  listLoading.value = true
  try {
    collections.value = await fetchCollections()
    // 当前选中项被删除时回到第一项
    if (currentId.value === null || !collections.value.some((c) => c.id === currentId.value)) {
      currentId.value = collections.value[0]?.id ?? null
    }
  } catch {
    Message.error('加载合集列表失败')
  } finally {
    listLoading.value = false
  }
}

// ── 合集内容 ──

const items = ref<InspirationOut[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(50)
const contentLoading = ref(false)

// ── 合集内容筛选/排序（工具条）──

/** 合集内排序类型与默认排序/参数拼装见 components/collection/contentFilters.ts */
type ContentSortValue = ContentSort
type ContentMediaValue = ContentMedia
type ContentQualityValue = ContentQuality
type ContentRatingMinValue = ContentRatingMin

const contentKeyword = ref('')
const contentMedia = ref<ContentMediaValue>('')
const contentQuality = ref<ContentQualityValue>('')
const contentRatingMin = ref<ContentRatingMinValue>('')
const contentFavoriteOnly = ref(false)
const contentSort = ref<ContentSortValue>('manual')
/** 关键词防抖定时器（切合集/离开页面时清掉，避免旧请求回来覆盖新合集） */
let keywordTimer: ReturnType<typeof setTimeout> | null = null

// 下拉选项直接复用纯逻辑模块里的常量（与参数拼装同一份真源）
const mediaOptions = MEDIA_OPTIONS
const qualityOptions = QUALITY_OPTIONS
const ratingOptions = RATING_OPTIONS
const contentSortOptions = CONTENT_SORT_OPTIONS

const contentSortLabel = computed(() => formatContentSortLabel(contentSort.value))
/** 手动合集 + 手动顺序才允许编辑排序（其它排序下拖动会与当前视图乱序） */
const canEditOrder = computed(
  () => current.value?.kind === 'manual' && contentSort.value === 'manual',
)
/** 当前筛选状态（传给纯函数做参数拼装/是否激活判断） */
function currentContentFilterState(): ContentFilterState {
  return {
    keyword: contentKeyword.value,
    mediaType: contentMedia.value,
    quality: contentQuality.value,
    ratingMin: contentRatingMin.value,
    favoriteOnly: contentFavoriteOnly.value,
    sort: contentSort.value,
  }
}
/** 除关键词外是否有筛选条件（决定是否展示「清除筛选」） */
const contentFiltersActive = computed(() => isContentFilterActive(currentContentFilterState()))

/** 当前合集的默认排序：手动合集按手动顺序，智能合集按最新 */
function defaultSortForCurrent(): ContentSort {
  return defaultContentSort(current.value?.kind)
}

/** 切合集/重置时清空筛选与排序（避免把上一个合集的筛选带到新合集） */
function resetContentFilters(): void {
  if (keywordTimer !== null) {
    clearTimeout(keywordTimer)
    keywordTimer = null
  }
  contentKeyword.value = ''
  contentMedia.value = ''
  contentQuality.value = ''
  contentRatingMin.value = ''
  contentFavoriteOnly.value = false
  contentSort.value = defaultSortForCurrent()
}

/** 关键词防抖 300ms：输入过程中不请求，停手后回第一页加载 */
function onKeywordInput(value: string): void {
  contentKeyword.value = value
  if (keywordTimer !== null) clearTimeout(keywordTimer)
  keywordTimer = setTimeout(() => {
    keywordTimer = null
    page.value = 1
    loadContent()
  }, 300)
}

/** 其它筛选/排序变化：立即回第一页加载；离开手动顺序时退出排序编辑模式 */
function onContentFilterChange(): void {
  page.value = 1
  if (contentSort.value !== 'manual') ordering.value = false
  loadContent()
}

/** 排序变化单独处理：非手动顺序时不允许继续编辑排序（并给出说明） */
function onContentSortChange(value: unknown): void {
  contentSort.value = (String(value ?? '') || defaultSortForCurrent()) as ContentSort
  onContentFilterChange()
}

/** 清除筛选与排序：保留当前合集，回到该合集的默认排序 */
function clearContentFilters(): void {
  resetContentFilters()
  page.value = 1
  loadContent()
}

async function loadContent() {
  if (currentId.value === null) {
    items.value = []
    total.value = 0
    return
  }
  contentLoading.value = true
  try {
    const params = buildCollectionContentParams(
      currentContentFilterState(),
      page.value,
      pageSize.value,
    )
    const data = await fetchCollectionInspirations(currentId.value, params)
    items.value = data.items
    total.value = data.total
  } catch (e) {
    Message.error(getApiErrorMessage(e, '加载合集内容失败'))
  } finally {
    contentLoading.value = false
  }
}

watch(currentId, () => {
  page.value = 1
  exitBatchMode()
  ordering.value = false
  resetContentFilters()
  loadContent()
})

function onPageChange(p: number) {
  page.value = p
  loadContent()
}

// ── 新建 / 重命名 / 删除 ──

// 手动合集新建/重命名共用弹窗
const {
  renameModalOpen,
  renameTargetId,
  renameParentId,
  renameForm,
  openCreateManual,
  openCreateChild,
  openRename,
  confirmRename,
  confirmDelete,
  editingSmart,
  smartEditorVisible,
  smartEditorId,
  openEditSmart,
  openCreateSmartWithId,
  handleSmartSaved,
  confirmSolidify,
  addOpen,
  addIdInput,
  confirmAddByIds,
} = useCollectionMutations({ collections, current, currentId, loadCollections, loadContent })

const currentPageIds = computed(() => items.value.map((i) => i.id))

/** 收藏请求中的素材 id：禁用按钮防连点 */
const favoritePendingIds = ref<Set<string>>(new Set())

/** 合集内容卡片收藏：系统收藏根/智能合集取消时立即从当前列表移除 */
async function handleContentFavorite(id: string) {
  if (favoritePendingIds.value.has(id)) return
  const item = items.value.find((i) => i.id === id)
  if (!item) return
  const previous = Boolean(item.is_favorite)
  const next = !previous
  favoritePendingIds.value = new Set(favoritePendingIds.value).add(id)
  item.is_favorite = next
  try {
    await toggleFavorite(id, next)
    // 系统入库手动收藏 = {is_favorite:true} 智能合集：取消后当前列表应收敛；
    // 其它智能合集可能仍匹配（例如仅按标签筛），不能一律移除
    const favoriteSmart =
      current.value?.kind === 'smart' &&
      Boolean((current.value.query_json as { is_favorite?: boolean } | null)?.is_favorite)
    if (!next && favoriteSmart) {
      items.value = items.value.filter((i) => i.id !== id)
      total.value = Math.max(0, total.value - 1)
    }
  } catch (e) {
    item.is_favorite = previous
    Message.error(getApiErrorMessage(e, '收藏失败'))
  } finally {
    const set = new Set(favoritePendingIds.value)
    set.delete(id)
    favoritePendingIds.value = set
  }
}
const allSelected = computed(
  () => items.value.length > 0 && items.value.every((i) => selectedIds.value.has(i.id)),
)

async function handleBatchRemove() {
  const c = current.value
  if (!c || selectedIds.value.size === 0) return
  try {
    const { removed } = await removeFromCollection(c.id, [...selectedIds.value])
    Message.success(`已移出 ${removed} 个素材`)
    exitBatchMode()
    loadContent()
    loadCollections()
  } catch (e) {
    Message.error(getApiErrorMessage(e, '移出失败'))
  }
}

// ── 批量移动/复制到其它合集（仅手动合集的批量选择栏）──

const transferOpen = ref(false)
const transferMode = ref<'move' | 'copy'>('move')
const transferSubmitting = ref(false)

function openTransfer(mode: 'move' | 'copy') {
  if (selectedIds.value.size === 0) return
  transferMode.value = mode
  transferOpen.value = true
}

async function confirmTransfer(targetIds: number[]) {
  const source = current.value
  const ids = [...selectedIds.value]
  if (!source || ids.length === 0 || targetIds.length === 0) return
  const action = transferMode.value === 'move' ? '移动' : '复制'
  transferSubmitting.value = true
  try {
    const result = await transferCollectionItems(source.id, ids, targetIds, transferMode.value)
    Message.success(`已${action} ${result.added} 个素材到 ${targetIds.length} 个合集`)
    if (result.skipped > 0) Message.info(`跳过 ${result.skipped} 个（目标合集已有）`)
    transferOpen.value = false
    exitBatchMode()
    // 源合集内容/计数与目标合集计数都可能变化：列表与内容一起刷新
    await Promise.all([loadContent(), loadCollections()])
  } catch (e) {
    Message.error(getApiErrorMessage(e, `${action}失败`))
  } finally {
    transferSubmitting.value = false
  }
}

// ── 合并当前手动合集到目标合集（合并后删除源，用户已确认）──

const mergeOpen = ref(false)
const mergeSubmitting = ref(false)

function openMerge() {
  mergeOpen.value = true
}

async function confirmMerge(targetIds: number[]) {
  const source = current.value
  const target = targetIds[0]
  if (!source || !target) return
  mergeSubmitting.value = true
  try {
    const result = await mergeCollections(target, [source.id])
    Message.success(
      `已合并 ${result.merged_collections} 个合集：新增 ${result.added} 个素材，源合集已删除`,
    )
    if (result.skipped > 0) Message.info(`跳过 ${result.skipped} 个（目标已有或无法合并）`)
    mergeOpen.value = false
    await loadCollections()
    // 源合集已删除：选中目标合集，让用户直接看到合并后的内容
    currentId.value = target
  } catch (e) {
    // 后端失败时前端不改任何本地状态（源合集仍在，无需回滚），只提示
    Message.error(getApiErrorMessage(e, '合并合集失败'))
  } finally {
    mergeSubmitting.value = false
  }
}

// ── 拖拽排序：合集列表（实现见 composables/useCollectionListDnd.ts）──

const { dragOverId, onCollectionDragStart, onCollectionDrop, onCollectionMove } =
  useCollectionListDnd({
    collections,
    loadCollections,
  })

// ── 拖拽排序：合集内素材（实现见 composables/useCollectionItemOrdering.ts）──

const {
  ordering,
  orderedItems,
  onItemDragStart,
  enterOrdering,
  onItemDrop,
  onItemMove,
  saveItemOrder,
} = useCollectionItemOrdering({ current, total, loadContent })

// ── 条件摘要 ──

const conditionSummary = computed(() => {
  const idToName = (id: number) =>
    tagsStore.groups.flatMap((g) => g.tags).find((t) => t.id === id)?.name
  return describeSmartQuery(
    current.value?.query_json ?? null,
    idToName,
    (v) => SOURCE_TYPE_LABELS[v] ?? v,
  )
})

onUnmounted(() => {
  if (keywordTimer !== null) {
    clearTimeout(keywordTimer)
    keywordTimer = null
  }
})

onMounted(() => {
  void loadCollections().then(() => loadContent())
  // 预热标签库：智能合集编辑器的标签下拉首次打开即可用（避免首次点开时等待标签接口）
  void tagsStore.load()
})
</script>

<template>
  <div class="collections-page">
    <!-- 左侧合集列表（两级树 + 拖拽排序 + 新建入口） -->
    <CollectionSidebar
      v-model:current-id="currentId"
      v-model:drag-over-id="dragOverId"
      :collections="collections"
      :children-by-parent="childrenByParent"
      :loading="listLoading"
      @drag-start="onCollectionDragStart"
      @drop="onCollectionDrop"
      @move="onCollectionMove"
      @create-manual="openCreateManual"
      @create-smart="openCreateSmartWithId"
      @create-child="openCreateChild"
    />

    <!-- 右侧内容区 -->
    <section class="collection-content">
      <template v-if="current">
        <!-- 详情头：合集名 / 标记 / 素材数 + 操作按钮组 -->
        <CollectionContentHeader
          :current="current"
          :parent-name="parentName"
          :total="total"
          :ordering="ordering"
          :can-order="canEditOrder"
          @edit-smart="openEditSmart"
          @solidify="confirmSolidify"
          @enter-ordering="enterOrdering"
          @save-order="saveItemOrder"
          @cancel-ordering="ordering = false"
          @rename="openRename"
          @delete="confirmDelete"
          @merge="openMerge"
        />

        <!-- 智能合集条件摘要 -->
        <a-alert v-if="current.kind === 'smart'" type="info" style="margin-bottom: 12px">
          匹配条件：{{ conditionSummary }}
        </a-alert>

        <!-- 合集内容工具条：搜索/媒体/质量/评分/仅收藏/排序（分类节点不显示） -->
        <div v-if="!currentChildren.length" class="content-filter-bar">
          <div class="content-filter-row">
            <a-input
              :model-value="contentKeyword"
              placeholder="搜索合集内素材（标签/作者/关键词）"
              allow-clear
              class="content-keyword"
              style="width: 240px"
              @input="onKeywordInput"
              @clear="onKeywordInput('')"
            />
            <a-select
              v-model="contentMedia"
              :options="mediaOptions"
              size="small"
              style="width: 118px"
              @change="() => onContentFilterChange()"
            />
            <a-select
              v-model="contentQuality"
              :options="qualityOptions"
              size="small"
              style="width: 118px"
              @change="() => onContentFilterChange()"
            />
            <a-select
              v-model="contentRatingMin"
              :options="ratingOptions"
              size="small"
              style="width: 128px"
              @change="() => onContentFilterChange()"
            />
            <a-checkbox v-model="contentFavoriteOnly" @change="() => onContentFilterChange()">
              仅收藏
            </a-checkbox>
            <span class="content-filter-spacer" />
            <a-select
              v-model="contentSort"
              :options="contentSortOptions"
              size="small"
              style="width: 168px"
              @change="onContentSortChange"
            />
            <a-button
              v-if="contentFiltersActive"
              size="mini"
              type="text"
              @click="clearContentFilters"
            >
              清除筛选
            </a-button>
          </div>
          <div
            v-if="current.kind === 'manual' && contentSort !== 'manual'"
            class="content-sort-hint"
          >
            当前按「{{ contentSortLabel }}」排序；切回「手动顺序」后可编辑排序。
          </div>
        </div>

        <!-- 一级收藏夹：只当分类，列出二级收藏夹卡片（不做混合素材墙） -->
        <div v-if="currentChildren.length" class="folder-list">
          <p class="folder-tip">
            这是分类节点：素材按抖音上的收藏夹分别存放，点开某个收藏夹查看素材。
          </p>
          <div class="folder-grid">
            <div
              v-for="child in currentChildren"
              :key="child.id"
              class="folder-card"
              @click="currentId = child.id"
            >
              <div class="folder-cover">
                <img
                  v-if="child.cover_thumbnail_path"
                  :src="getFileUrl(child.cover_thumbnail_path)"
                  alt=""
                />
                <span v-else class="folder-cover-empty">暂无封面</span>
              </div>
              <div class="folder-name" :title="child.name">{{ child.name }}</div>
              <div class="folder-count">{{ child.item_count ?? 0 }} 个素材</div>
            </div>
          </div>
          <div v-if="current.item_count" class="folder-unclassified">
            另有 {{ current.item_count }} 个素材没有归到任何收藏夹（平铺收藏/归属缺失），见下方。
          </div>
        </div>

        <!-- 排序模式：缩略图条带拖拽编排 -->
        <CollectionOrderingStrip
          v-if="ordering"
          :items="orderedItems"
          @drag-start="onItemDragStart"
          @drop="onItemDrop"
          @move="onItemMove"
        />

        <!-- 瀑布流内容 -->
        <template v-else>
          <!-- 只有「能装素材的合集」才给添加入口。判定必须与后端一致
               （collection_service._require_container）：被拦的是**分类节点**——
               一级同步节点（parent_id 为空且 auto_source 非空）与已经有子夹的合集。
               同步的**二级**收藏夹是正常容器，仍要给添加入口，否则界面会把后端允许的
               能力藏起来（曾写成「任何 auto_source 都藏」，与本意不符）。 -->
          <div
            v-if="
              current.kind === 'manual' &&
              !currentChildren.length &&
              !(current.parent_id === null && current.auto_source)
            "
            class="content-toolbar"
          >
            <a-button size="small" @click="addOpen = true">＋ 添加素材</a-button>
            <a-button v-if="!batchMode" size="small" @click="enterBatchMode()">批量选择</a-button>
          </div>

          <div v-if="batchMode" class="remove-bar">
            <span>已选 {{ selectedCount }} 个</span>
            <a-button size="mini" @click="toggleSelectAll(currentPageIds)">
              {{ allSelected ? '取消全选' : '全选本页' }}
            </a-button>
            <a-popconfirm
              :content="`将所选 ${selectedCount} 个素材移出合集？素材本体不受影响`"
              @ok="handleBatchRemove"
            >
              <a-button size="mini" status="danger">移出合集</a-button>
            </a-popconfirm>
            <template v-if="current.kind === 'manual'">
              <a-button size="mini" @click="openTransfer('move')">移动到…</a-button>
              <a-button size="mini" @click="openTransfer('copy')">复制到…</a-button>
            </template>
            <a-button size="mini" type="text" @click="exitBatchMode()">退出批量</a-button>
          </div>

          <template v-if="!currentChildren.length || items.length">
            <MasonryGrid
              :items="items"
              :loading="contentLoading"
              density="standard"
              :selectable="batchMode"
              :selected-ids="selectedIds"
              :show-view-button="batchMode"
              :show-actions="false"
              :show-favorite="true"
              :pending-favorite-ids="favoritePendingIds"
              @toggle-favorite="handleContentFavorite"
              @toggle-select="toggleSelect"
            />

            <div v-if="total > pageSize" class="pagination-wrapper">
              <a-pagination
                :current="page"
                :total="total"
                :page-size="pageSize"
                @change="onPageChange"
              />
            </div>
          </template>
        </template>
      </template>
      <!-- 空状态：垂直水平居中撑满内容区，避免孤零零浮在左上角 -->
      <div v-else class="content-empty">
        <a-empty description="选择左侧合集，或新建一个" />
      </div>
    </section>

    <!-- 智能合集条件编辑器 -->
    <SmartQueryEditorModal
      v-model:visible="smartEditorVisible"
      :collection-id="smartEditorId"
      :initial-query="editingSmart?.query_json ?? null"
      :initial-name="editingSmart?.name ?? ''"
      @saved="handleSmartSaved"
    />

    <!-- 手动合集新建/重命名 -->
    <a-modal
      :visible="renameModalOpen"
      :title="
        renameTargetId !== null
          ? '编辑合集信息'
          : renameParentId !== null
            ? `在「${collections.find((c) => c.id === renameParentId)?.name ?? ''}」下新建二级收藏夹`
            : '新建手动合集'
      "
      :width="460"
      @update:visible="renameModalOpen = $event"
    >
      <a-form
        :model="renameForm"
        label-align="left"
        :label-col-style="{ width: '70px' }"
        size="small"
      >
        <a-form-item label="名称">
          <a-input
            v-model="renameForm.name"
            placeholder="1~50 字"
            allow-clear
            max-length="50"
            @keyup.enter="confirmRename"
          />
        </a-form-item>
        <a-form-item label="描述">
          <a-input
            v-model="renameForm.description"
            placeholder="可选"
            allow-clear
            max-length="200"
          />
        </a-form-item>
      </a-form>
      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 8px">
          <a-button size="small" @click="renameModalOpen = false">取消</a-button>
          <a-button size="small" type="primary" @click="confirmRename">保存</a-button>
        </div>
      </template>
    </a-modal>

    <!-- 手动合集快速添加素材（按 ID） -->
    <a-modal v-model:visible="addOpen" title="添加素材到合集" :width="460">
      <p style="color: var(--ink-3); font-size: 12px">
        输入素材 ID（可在素材库/详情页查看），多个用逗号或空格分隔。推荐在素材库用「批量选择 →
        加入合集」。
      </p>
      <a-textarea v-model="addIdInput" placeholder="粘贴素材 ID 列表" :auto-size="{ minRows: 3 }" />
      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 8px">
          <a-button size="small" @click="addOpen = false">取消</a-button>
          <a-button size="small" type="primary" @click="confirmAddByIds">加入</a-button>
        </div>
      </template>
    </a-modal>
    <!-- 批量移动/复制目标合集 -->
    <CollectionTargetPickerModal
      v-model:visible="transferOpen"
      :title="transferMode === 'move' ? '移动素材到合集' : '复制素材到合集'"
      :exclude-ids="current ? [current.id] : []"
      :submitting="transferSubmitting"
      @confirm="confirmTransfer"
    />

    <!-- 合并当前合集到目标合集（成功后会删除源合集） -->
    <CollectionTargetPickerModal
      v-model:visible="mergeOpen"
      title="合并到目标合集（合并后删除当前合集）"
      :multiple="false"
      :exclude-ids="current ? [current.id] : []"
      :submitting="mergeSubmitting"
      @confirm="confirmMerge"
    />
  </div>
</template>

<style scoped>
.collections-page {
  display: flex;
  gap: 16px;
  max-width: 1800px;
  margin: 0 auto;
  align-items: flex-start;
}

/* iPad 竖屏/分屏：合集列表移到顶部，内容区拿回完整宽度 */
@media (max-width: 900px) {
  .collections-page {
    flex-direction: column;
  }

  .collection-content {
    width: 100%;
  }
}

/* 右侧内容 */
.collection-content {
  flex: 1;
  min-width: 0;
}
/* 空状态：居中撑满可视区 */
.content-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 420px;
  background: var(--sheet);
  border: 1px dashed #e5e7eb;
  border-radius: var(--radius-md);
}
/* 分类节点（一级）的收藏夹卡片墙：像抖音的收藏夹列表，而不是混合素材墙 */
.folder-list {
  margin-bottom: 16px;
}
.folder-tip {
  margin: 0 0 10px;
  font-size: 12px;
  color: var(--ink-3);
}
.folder-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 12px;
}
.folder-card {
  border: 1px solid #e5e6eb;
  border-radius: var(--radius-md);
  overflow: hidden;
  cursor: pointer;
  background: var(--sheet);
  transition: all 0.15s;
}
.folder-card:hover {
  border-color: var(--ink-3);
  box-shadow: 0 2px 8px rgb(0 0 0 / 8%);
}
.folder-cover {
  height: 96px;
  background: var(--sheet-2);
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}
.folder-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.folder-cover-empty {
  font-size: 28px;
  opacity: 0.5;
}
.folder-name {
  padding: 8px 10px 0;
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.folder-count {
  padding: 2px 10px 10px;
  font-size: 11px;
  color: var(--ink-3);
}
.folder-unclassified {
  margin-top: 10px;
  font-size: 12px;
  color: var(--amber);
}
.content-toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
}

/* 合集内容工具条：搜索/筛选/排序（纸面条，与首页索引条同一语言） */
.content-filter-bar {
  padding: 10px 12px;
  margin-bottom: 12px;
  background: var(--sheet);
  border: 1px solid var(--line);
}

.content-filter-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.content-filter-spacer {
  flex: 1;
}

.content-sort-hint {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--ink-3);
}

@media (max-width: 767px) {
  .content-filter-row {
    gap: 6px;
  }

  .content-filter-row .content-keyword {
    width: 100% !important;
  }

  .content-filter-spacer {
    display: none;
  }
}
.remove-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  margin-bottom: 8px;
  background: var(--sheet-2);
  border: 1px solid #ffd591;
  border-radius: var(--radius-md);
  font-size: 13px;
}

.pagination-wrapper {
  display: flex;
  justify-content: center;
  padding: 24px 0;
}

/* ── 印样台：收藏合集（左索引 + 右印样纸）── */
.collections-page {
  gap: 20px;
  max-width: 1680px;
  padding-bottom: 32px;
}

.folder-tip {
  color: var(--ink-2);
  font-size: 13px;
}

.folder-grid {
  gap: 14px;
}

.folder-card {
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--sheet);
  transition: border-color 0.12s ease;
}

.folder-card:hover {
  border-color: var(--ink-3);
  box-shadow: none;
}

.folder-cover {
  background: var(--sheet-2);
  border-bottom: 1px solid var(--line);
  color: var(--ink-3);
}

.folder-cover-empty {
  font-size: 11px;
  opacity: 1;
}

.folder-name {
  font-size: 13px;
  color: var(--ink);
}

.folder-count {
  color: var(--ink-3);
}

.folder-unclassified {
  color: var(--amber);
}

.content-toolbar {
  margin-bottom: 12px;
}

.remove-bar {
  background: var(--sheet);
  border: 1px solid var(--line);
  border-left: 2px solid var(--amber);
  border-radius: 0;
}

.collection-empty {
  background: var(--sheet);
  border: 1px dashed var(--line-strong);
  border-radius: var(--radius-md);
}

/* 900 以下：左列合集索引改为上下堆叠，宽度跟随内容区 */
@media (max-width: 900px) {
  .collections-page {
    flex-direction: column;
  }

  .collections-page :deep(.collection-list) {
    width: 100% !important;
    max-width: 100%;
    border-right: 0;
    border-bottom: 1px solid var(--line);
  }
}

/* 内容区最小宽度归零：窄屏下由内部组件自己换行/横滑，而不是把父容器撑宽 */
.collections-page,
.collections-page :deep(.collection-content) {
  min-width: 0;
}

@media (max-width: 900px) {
  .collections-page {
    width: 100%;
  }

  .collections-page :deep(.collection-content) {
    width: 100%;
    max-width: 100%;
  }
}

/* 左栏是 padding 盒：width:100% 必须配 border-box，否则 padding 会把内容顶出视口 */
.collections-page :deep(.collection-list) {
  box-sizing: border-box;
}
</style>
