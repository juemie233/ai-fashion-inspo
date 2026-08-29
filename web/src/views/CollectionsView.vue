<script setup lang="ts">
/** 收藏合集页：左侧合集列表（拖拽排序）+ 右侧合集内容瀑布流。
 *
 * 手动合集：瀑布流 + 批量多选移出 + 「编辑排序」模式（缩略图条带拖拽编排展示顺序）；
 * 智能合集：⚡ 徽标 + 条件摘要，无加入/移出/排序（内容由条件动态决定），
 * 支持「编辑条件」「转手动（固化）」。
 */

import { computed, onMounted, reactive, ref, watch } from 'vue'
import { Message, Modal } from '@arco-design/web-vue'
import MasonryGrid from '@/components/inspiration/MasonryGrid.vue'
import SmartQueryEditorModal from '@/components/collection/SmartQueryEditorModal.vue'
import {
  addToCollection,
  createCollection,
  deleteCollection,
  fetchCollectionInspirations,
  fetchCollections,
  removeFromCollection,
  reorderCollectionItems,
  reorderCollections,
  solidifyCollection,
  type CollectionOut,
  type SmartCollectionQuery,
} from '@/api/collections'
import { getApiErrorMessage } from '@/utils/apiError'
import { SOURCE_TYPE_LABELS } from '@/utils/sourceLabel'
import { getFileUrl } from '@/api/inspirations'
import { describeSmartQuery } from '@/utils/collectionQuery'
import { useTagsStore } from '@/stores/tags'
import { useInspirationsStore } from '@/stores/inspirations'
import { useBatchSelection } from '@/composables/useBatchSelection'
import type { InspirationOut } from '@/api/inspirations'

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
} = useBatchSelection()

// ── 合集列表 ──

const collections = ref<CollectionOut[]>([])
const listLoading = ref(false)
const currentId = ref<number | null>(null)
const current = computed(() => collections.value.find((c) => c.id === currentId.value) ?? null)

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

async function loadContent() {
  if (currentId.value === null) {
    items.value = []
    total.value = 0
    return
  }
  contentLoading.value = true
  try {
    const data = await fetchCollectionInspirations(currentId.value, {
      page: page.value,
      size: pageSize.value,
    })
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
  loadContent()
})

function onPageChange(p: number) {
  page.value = p
  loadContent()
}

// ── 新建 / 重命名 / 删除 ──

const editorVisible = ref(false)
const editingCollection = ref<CollectionOut | null>(null)

function openCreateSmart() {
  editingCollection.value = null
  editorVisible.value = true
}

async function handleEditorSaved() {
  await loadCollections()
}

// 手动合集新建/重命名共用弹窗
const renameModalOpen = ref(false)
const renameTargetId = ref<number | null>(null) // null = 新建手动合集
const renameForm = reactive({ name: '', description: '' })

function openCreateManual() {
  renameTargetId.value = null
  renameForm.name = ''
  renameForm.description = ''
  renameModalOpen.value = true
}

function openRename() {
  const c = current.value
  if (!c) return
  renameTargetId.value = c.id
  renameForm.name = c.name
  renameForm.description = c.description ?? ''
  renameModalOpen.value = true
}

async function confirmRename() {
  const name = renameForm.name.trim()
  if (!name) {
    Message.warning('请输入合集名称')
    return
  }
  try {
    const { updateCollection: update, createCollection: create } = await import('@/api/collections')
    if (renameTargetId.value === null) {
      await create({ name, description: renameForm.description.trim() || null })
      Message.success('已创建合集')
    } else {
      await update(renameTargetId.value, {
        name,
        description: renameForm.description.trim() || null,
      })
      Message.success('已保存')
    }
    renameModalOpen.value = false
    loadCollections()
  } catch (e) {
    Message.error(getApiErrorMessage(e, '保存失败'))
  }
}

function confirmDelete() {
  const c = current.value
  if (!c) return
  const isSmart = c.kind === 'smart'
  Modal.confirm({
    title: `删除合集「${c.name}」？`,
    content: isSmart
      ? '智能合集仅删除条件本身，素材不受任何影响。'
      : '仅删除合集与成员关联，素材本体与标签不受影响，此操作不可恢复。',
    okText: '删除',
    okButtonProps: { status: 'danger' },
    onOk: async () => {
      try {
        await deleteCollection(c.id)
        Message.success('合集已删除')
        await loadCollections()
        loadContent()
      } catch (e) {
        Message.error(getApiErrorMessage(e, '删除合集失败'))
      }
    },
  })
}

// ── 智能合集：编辑条件 / 固化 ──

const editingSmart = ref<CollectionOut | null>(null)
const smartEditorVisible = ref(false)
const smartEditorId = ref<number | null>(null)

function openEditSmart() {
  const c = current.value
  if (!c || c.kind !== 'smart') return
  editingSmart.value = c
  smartEditorId.value = c.id
  smartEditorVisible.value = true
}

/** 从空条件创建智能合集（编辑器 collectionId=null 走创建分支） */
function openCreateSmartWithId() {
  editingSmart.value = null
  smartEditorId.value = null
  smartEditorVisible.value = true
}

async function handleSmartSaved() {
  await loadCollections()
  loadContent()
}

function confirmSolidify() {
  const c = current.value
  if (!c || c.kind !== 'smart') return
  Modal.confirm({
    title: `把「${c.name}」转为手动合集？`,
    content: '将当前匹配到的素材固化为合集成员，之后可手动增删与排序；筛选条件将不再生效。',
    onOk: async () => {
      try {
        await solidifyCollection(c.id)
        Message.success('已转为手动合集')
        await loadCollections()
        loadContent()
      } catch (e) {
        Message.error(getApiErrorMessage(e, '转换失败'))
      }
    },
  })
}

// ── 手动合集：加入素材（快速添加：输入素材 ID） ──

const addOpen = ref(false)
const addIdInput = ref('')

async function confirmAddByIds() {
  const c = current.value
  if (!c || c.kind !== 'manual') return
  const ids = addIdInput.value
    .split(/[,，\s]+/)
    .map((x) => x.trim())
    .filter(Boolean)
  if (ids.length === 0) {
    Message.warning('请输入至少一个素材 ID')
    return
  }
  try {
    await addToCollection(c.id, ids)
    Message.success('已加入')
    addOpen.value = false
    addIdInput.value = ''
    loadContent()
    loadCollections()
  } catch (e) {
    Message.error(getApiErrorMessage(e, '加入失败'))
  }
}

// ── 手动合集：批量移出 ──

const currentPageIds = computed(() => items.value.map((i) => i.id))
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

// ── 拖拽排序：合集列表 ──

const dragOverId = ref<number | null>(null)
let draggingCollectionId: number | null = null

function onCollectionDragStart(id: number) {
  draggingCollectionId = id
}

async function onCollectionDrop(targetId: number) {
  const from = draggingCollectionId
  draggingCollectionId = null
  dragOverId.value = null
  if (from === null || from === targetId) return
  const ids = collections.value.map((c) => c.id)
  const fromIdx = ids.indexOf(from)
  const toIdx = ids.indexOf(targetId)
  if (fromIdx === -1 || toIdx === -1) return
  ids.splice(toIdx, 0, ids.splice(fromIdx, 1)[0])
  // 乐观更新 + 后端持久化
  collections.value.sort((x, y) => ids.indexOf(x.id) - ids.indexOf(y.id))
  try {
    await reorderCollections(ids)
    await loadCollections()
  } catch (e) {
    Message.error(getApiErrorMessage(e, '排序保存失败'))
    loadCollections()
  }
}

// ── 拖拽排序：合集内素材（排序模式下的缩略图条带） ──

const ordering = ref(false)
let draggingItemId: string | null = null

function enterOrdering() {
  if (total.value > 200) {
    Message.warning('素材过多，建议先用筛选精简后再编排顺序')
    return
  }
  ordering.value = true
  // 排序模式需要全量成员（分页瀑布流不便于编排），一次拉取
  void loadAllForOrdering()
}

const orderedItems = ref<InspirationOut[]>([])

async function loadAllForOrdering() {
  const c = current.value
  if (!c) return
  try {
    const data = await fetchCollectionInspirations(c.id, { page: 1, size: 500 })
    orderedItems.value = data.items
  } catch (e) {
    Message.error(getApiErrorMessage(e, '加载成员失败'))
  }
}

async function onItemDrop(targetId: string) {
  const from = draggingItemId
  draggingItemId = null
  if (!from || from === targetId) return
  const ids = orderedItems.value.map((i) => i.id)
  const fromIdx = ids.indexOf(from)
  const toIdx = ids.indexOf(targetId)
  if (fromIdx === -1 || toIdx === -1) return
  ids.splice(toIdx, 0, ids.splice(fromIdx, 1)[0])
  orderedItems.value = ids
    .map((id) => orderedItems.value.find((i) => i.id === id))
    .filter((i): i is InspirationOut => !!i)
}

async function saveItemOrder() {
  const c = current.value
  if (!c) return
  try {
    await reorderCollectionItems(
      c.id,
      orderedItems.value.map((i) => i.id),
    )
    Message.success('顺序已保存')
    ordering.value = false
    loadContent()
  } catch (e) {
    Message.error(getApiErrorMessage(e, '保存顺序失败'))
  }
}

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

onMounted(() => {
  void loadCollections().then(() => loadContent())
  // 预热标签库：智能合集编辑器的标签下拉首次打开即可用（避免首次点开时等待标签接口）
  void tagsStore.load()
})
</script>

<template>
  <div class="collections-page">
    <!-- 左侧合集列表 -->
    <aside class="collection-list">
      <div class="list-header">
        <span>合集（{{ collections.length }}）</span>
        <div style="display: flex; gap: 4px">
          <a-button size="mini" type="primary" @click="openCreateManual">＋ 手动</a-button>
          <a-button size="mini" @click="openCreateSmartWithId">⚡ 智能</a-button>
        </div>
      </div>

      <a-spin :loading="listLoading" style="display: block">
        <div
          v-for="c in collections"
          :key="c.id"
          class="collection-item"
          :class="{ active: c.id === currentId, 'drag-over': dragOverId === c.id }"
          draggable="true"
          @dragstart="onCollectionDragStart(c.id)"
          @dragover.prevent="dragOverId = c.id"
          @dragleave="dragOverId = null"
          @drop.prevent="onCollectionDrop(c.id)"
          @click="currentId = c.id"
        >
          <span class="collection-kind">{{ c.kind === 'smart' ? '⚡' : '📁' }}</span>
          <div class="collection-info">
            <div class="collection-name" :title="c.name">{{ c.name }}</div>
            <div class="collection-meta">
              {{ c.kind === 'smart' ? '智能' : `${c.item_count ?? 0} 个素材` }}
            </div>
          </div>
        </div>
        <a-empty
          v-if="collections.length === 0 && !listLoading"
          description="暂无合集，点击上方按钮新建"
        />
      </a-spin>
      <div class="drag-hint">拖动调整合集顺序</div>
    </aside>

    <!-- 右侧内容区 -->
    <section class="collection-content">
      <template v-if="current">
        <div class="content-header">
          <div class="content-title">
            <h2>{{ current.name }}</h2>
            <a-tag v-if="current.kind === 'smart'" color="arcoblue" size="small">⚡ 智能合集</a-tag>
            <span class="content-total">共 {{ total }} 个素材</span>
          </div>
          <div class="content-actions">
            <template v-if="current.kind === 'smart'">
              <a-button size="small" @click="openEditSmart">编辑条件</a-button>
              <a-popconfirm content="将当前匹配内容固化为手动合集？" @ok="confirmSolidify">
                <a-button size="small">转手动</a-button>
              </a-popconfirm>
            </template>
            <template v-else>
              <a-button v-if="!ordering" size="small" @click="enterOrdering">编辑排序</a-button>
              <template v-else>
                <a-button size="small" type="primary" @click="saveItemOrder">保存顺序</a-button>
                <a-button size="small" @click="ordering = false">取消排序</a-button>
              </template>
              <a-button size="small" @click="openRename">重命名</a-button>
            </template>
            <a-button size="small" status="danger" type="text" @click="confirmDelete"
              >删除合集</a-button
            >
          </div>
        </div>

        <!-- 智能合集条件摘要 -->
        <a-alert v-if="current.kind === 'smart'" type="info" style="margin-bottom: 12px">
          匹配条件：{{ conditionSummary }}
        </a-alert>

        <!-- 排序模式：缩略图条带拖拽编排 -->
        <div v-if="ordering" class="ordering-strip">
          <p class="ordering-tip">拖动缩略图调整展示顺序，完成后点击「保存顺序」。</p>
          <div class="ordering-grid">
            <div
              v-for="it in orderedItems"
              :key="it.id"
              class="ordering-card"
              draggable="true"
              @dragstart="draggingItemId = it.id"
              @dragover.prevent
              @drop.prevent="onItemDrop(it.id)"
            >
              <img :src="getFileUrl(it.thumbnail_path ?? it.file_path)" alt="" />
            </div>
          </div>
        </div>

        <!-- 瀑布流内容 -->
        <template v-else>
          <div v-if="current.kind === 'manual'" class="content-toolbar">
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
            <a-button size="mini" type="text" @click="exitBatchMode()">退出批量</a-button>
          </div>

          <MasonryGrid
            :items="items"
            :loading="contentLoading"
            density="standard"
            :selectable="batchMode"
            :selected-ids="selectedIds"
            :show-view-button="batchMode"
            :show-actions="false"
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

    <!-- 智能合集创建（collectionId=null → 编辑器走创建分支） -->
    <SmartQueryEditorModal
      :visible="editorVisible"
      :collection-id="null"
      :initial-query="null"
      initial-name=""
      @update:visible="editorVisible = $event"
      @saved="handleEditorSaved"
    />

    <!-- 手动合集新建/重命名 -->
    <a-modal
      :visible="renameModalOpen"
      :title="renameTargetId === null ? '新建手动合集' : '编辑合集信息'"
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
      <p style="color: #999; font-size: 12px">
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

/* 左侧列表 */
.collection-list {
  width: 260px;
  min-width: 260px;
  min-height: 420px;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 10px;
  position: sticky;
  top: 16px;
  display: flex;
  flex-direction: column;
}
.list-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: nowrap;
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 600;
}
.collection-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  border: 1px solid transparent;
  margin-bottom: 2px;
}
.collection-item:hover {
  background: #f5f7fa;
}
.collection-item.active {
  background: #eef4ff;
  border-color: #94bfff;
}
.collection-item.drag-over {
  border-top: 2px solid #2080f0;
}
.collection-kind {
  font-size: 14px;
}
.collection-info {
  flex: 1;
  min-width: 0;
}
.collection-name {
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.collection-meta {
  font-size: 11px;
  color: #86909c;
}
.drag-hint {
  font-size: 11px;
  color: #c9cdd4;
  text-align: center;
  padding-top: 8px;
}

/* 列表区撑满剩余高度，让「拖动调整」提示稳定贴底 */
.collection-list :deep(.arco-spin) {
  flex: 1;
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
  background: #fff;
  border: 1px dashed #e5e7eb;
  border-radius: 8px;
}
.content-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.content-title {
  display: flex;
  align-items: center;
  gap: 8px;
}
.content-title h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}
.content-total {
  font-size: 13px;
  color: #999;
}
.content-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.content-toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
}
.remove-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  margin-bottom: 8px;
  background: #fff7e6;
  border: 1px solid #ffd591;
  border-radius: 8px;
  font-size: 13px;
}

/* 排序模式 */
.ordering-tip {
  font-size: 12px;
  color: #86909c;
  margin: 0 0 8px;
}
.ordering-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 12px;
  background: #f8f9fa;
  border-radius: 8px;
}
.ordering-card {
  width: 96px;
  height: 130px;
  border-radius: 6px;
  overflow: hidden;
  cursor: grab;
  border: 2px solid transparent;
  background: #f2f3f5;
}
.ordering-card:active {
  cursor: grabbing;
}
.ordering-card:hover {
  border-color: #94bfff;
}
.ordering-card img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  pointer-events: none;
}
.pagination-wrapper {
  display: flex;
  justify-content: center;
  padding: 24px 0;
}
</style>
