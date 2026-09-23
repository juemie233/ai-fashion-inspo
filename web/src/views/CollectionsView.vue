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
/** 当前二级收藏夹所属一级的名字（一级自己的内容区不显示面包屑） */
const parentName = computed(() => {
  const parentId = current.value?.parent_id
  if (parentId == null) return null
  return collections.value.find((c) => c.id === parentId)?.name ?? null
})
/** 当前合集的二级收藏夹（非空 = 这是分类节点：内容区展示收藏夹卡片） */
const currentChildren = computed(() => {
  const id = current.value?.id
  return id == null ? [] : childrenOf(id)
})

// 两级结构：一级（parent_id 为空）渲染成组，二级跟在各一级下面。
// 后端已保证「一级后面紧跟它的二级」的顺序，这里只做分组。
const rootCollections = computed(() => collections.value.filter((c) => c.parent_id === null))
const childrenOf = (id: number) => collections.value.filter((c) => c.parent_id === id)
/** 折叠的一级节点（默认展开：抖音收藏夹要一眼看得见） */
const collapsed = ref<Set<number>>(new Set())

function toggleCollapse(id: number) {
  const next = new Set(collapsed.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  collapsed.value = next
}

/** 列表项副标题：一级显示子夹数（+ 素材数），二级显示素材数 */
function itemMeta(c: CollectionOut): string {
  if (c.kind === 'smart') return '智能合集'
  const own = c.item_count ?? 0
  const children = childrenOf(c.id).length
  // 分类节点自己没有成员时只报收藏夹数：写「0 个素材」会被读成「这个夹是空的」
  if (children > 0) return own > 0 ? `${children} 个收藏夹 · ${own} 个素材` : `${children} 个收藏夹`
  return `${own} 个素材`
}

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
// 新建时的父收藏夹：非空表示建二级（挂在选中的一级之下）
const renameParentId = ref<number | null>(null)
const renameForm = reactive({ name: '', description: '' })

function openCreateManual() {
  renameTargetId.value = null
  renameParentId.value = null
  renameForm.name = ''
  renameForm.description = ''
  renameModalOpen.value = true
}

/** 在某个一级合集下新建二级收藏夹 */
function openCreateChild(parentId: number) {
  renameTargetId.value = null
  renameParentId.value = parentId
  renameForm.name = ''
  renameForm.description = ''
  renameModalOpen.value = true
}

function openRename() {
  const c = current.value
  if (!c) return
  if (c.auto_source) {
    Message.warning('该收藏夹由抖音同步自动维护，名称不可修改')
    return
  }
  renameTargetId.value = c.id
  renameParentId.value = c.parent_id
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
      await create({
        name,
        description: renameForm.description.trim() || null,
        parent_id: renameParentId.value,
      })
      Message.success(renameParentId.value === null ? '已创建合集' : '已创建二级收藏夹')
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
        <!-- 两级：每个一级渲染成组，二级紧随其下（缩进 + 折角） -->
        <template v-for="c in rootCollections" :key="c.id">
          <div
            class="collection-item"
            :class="{ active: c.id === currentId, 'drag-over': dragOverId === c.id }"
            draggable="true"
            @dragstart="onCollectionDragStart(c.id)"
            @dragover.prevent="dragOverId = c.id"
            @dragleave="dragOverId = null"
            @drop.prevent="onCollectionDrop(c.id)"
            @click="currentId = c.id"
          >
            <span
              v-if="c.child_count > 0"
              class="collapse-toggle"
              :title="collapsed.has(c.id) ? '展开二级收藏夹' : '收起二级收藏夹'"
              @click.stop="toggleCollapse(c.id)"
            >
              {{ collapsed.has(c.id) ? '▸' : '▾' }}
            </span>
            <span v-else class="collapse-toggle placeholder"></span>
            <span class="collection-kind">{{ c.kind === 'smart' ? '⚡' : '📁' }}</span>
            <div class="collection-info">
              <div class="collection-name" :title="c.name">{{ c.name }}</div>
              <div class="collection-meta">{{ itemMeta(c) }}</div>
            </div>
          </div>

          <template v-if="!collapsed.has(c.id)">
            <div
              v-for="child in childrenOf(c.id)"
              :key="child.id"
              class="collection-item child"
              :class="{ active: child.id === currentId }"
              @click="currentId = child.id"
            >
              <span class="collection-kind">└</span>
              <div class="collection-info">
                <div class="collection-name" :title="child.name">{{ child.name }}</div>
                <div class="collection-meta">{{ itemMeta(child) }}</div>
              </div>
            </div>
            <div v-if="c.kind !== 'smart'" class="child-create" @click="openCreateChild(c.id)">
              ＋ 在此新建二级收藏夹
            </div>
          </template>
        </template>
        <a-empty
          v-if="collections.length === 0 && !listLoading"
          description="暂无合集，点击上方按钮新建"
        />
      </a-spin>
      <div class="drag-hint">拖动一级合集调整顺序；二级收藏夹由抖音同步维护</div>
    </aside>

    <!-- 右侧内容区 -->
    <section class="collection-content">
      <template v-if="current">
        <div class="content-header">
          <div class="content-title">
            <h2>{{ current.name }}</h2>
            <span v-if="parentName" class="content-parent">{{ parentName }} /</span>
            <a-tag v-if="current.kind === 'smart'" color="arcoblue" size="small">⚡ 智能合集</a-tag>
            <a-tag v-if="current.auto_source" color="gray" size="small">抖音同步</a-tag>
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
              <a-tooltip
                :disabled="!current.auto_source"
                content="由抖音同步自动维护，名称以抖音侧的收藏夹名为准"
              >
                <a-button size="small" :disabled="!!current.auto_source" @click="openRename"
                  >重命名</a-button
                >
              </a-tooltip>
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
                <span v-else class="folder-cover-empty">📂</span>
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
          <div v-if="current.kind === 'manual' && !currentChildren.length" class="content-toolbar">
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

          <template v-if="!currentChildren.length || items.length">
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
/* 二级收藏夹：缩进 + 更淡的字色，视觉上从属于上面那个一级 */
.collection-item.child {
  padding-left: 26px;
}
.collection-item.child .collection-name {
  font-size: 12.5px;
  color: #4e5969;
}
.collapse-toggle {
  width: 12px;
  font-size: 10px;
  color: #86909c;
  flex: none;
  text-align: center;
}
.collapse-toggle.placeholder {
  visibility: hidden;
}
.child-create {
  padding: 4px 10px 4px 26px;
  font-size: 11px;
  color: #86909c;
  cursor: pointer;
  border-radius: 6px;
}
.child-create:hover {
  color: #2080f0;
  background: #f5f7fa;
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
/* 二级收藏夹的父级面包屑（一级名下没有这段） */
.content-parent {
  font-size: 13px;
  color: #86909c;
}

/* 分类节点（一级）的收藏夹卡片墙：像抖音的收藏夹列表，而不是混合素材墙 */
.folder-list {
  margin-bottom: 16px;
}
.folder-tip {
  margin: 0 0 10px;
  font-size: 12px;
  color: #86909c;
}
.folder-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 12px;
}
.folder-card {
  border: 1px solid #e5e6eb;
  border-radius: 8px;
  overflow: hidden;
  cursor: pointer;
  background: #fff;
  transition: all 0.15s;
}
.folder-card:hover {
  border-color: #94bfff;
  box-shadow: 0 2px 8px rgb(0 0 0 / 8%);
}
.folder-cover {
  height: 96px;
  background: #f7f8fa;
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
  color: #86909c;
}
.folder-unclassified {
  margin-top: 10px;
  font-size: 12px;
  color: #ff7d00;
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
