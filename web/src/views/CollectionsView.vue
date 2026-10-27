<script setup lang="ts">
/** 收藏合集页：左侧合集列表（拖拽排序）+ 右侧合集内容瀑布流。
 *
 * 手动合集：瀑布流 + 批量多选移出 + 「编辑排序」模式（缩略图条带拖拽编排展示顺序）；
 * 智能合集：⚡ 徽标 + 条件摘要，无加入/移出/排序（内容由条件动态决定），
 * 支持「编辑条件」「转手动（固化）」。
 */

import { computed, onMounted, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import MasonryGrid from '@/components/inspiration/MasonryGrid.vue'
import CollectionContentHeader from '@/components/collection/CollectionContentHeader.vue'
import CollectionOrderingStrip from '@/components/collection/CollectionOrderingStrip.vue'
import CollectionSidebar from '@/components/collection/CollectionSidebar.vue'
import SmartQueryEditorModal from '@/components/collection/SmartQueryEditorModal.vue'
import {
  fetchCollectionInspirations,
  fetchCollections,
  removeFromCollection,
  type CollectionOut,
} from '@/api/collections'
import { getApiErrorMessage } from '@/utils/apiError'
import { SOURCE_TYPE_LABELS } from '@/utils/sourceLabel'
import { getFileUrl } from '@/api/inspirations'
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

// ── 拖拽排序：合集列表（实现见 composables/useCollectionListDnd.ts）──

const { dragOverId, onCollectionDragStart, onCollectionDrop } = useCollectionListDnd({
  collections,
  loadCollections,
})

// ── 拖拽排序：合集内素材（实现见 composables/useCollectionItemOrdering.ts）──

const { ordering, orderedItems, onItemDragStart, enterOrdering, onItemDrop, saveItemOrder } =
  useCollectionItemOrdering({ current, total, loadContent })

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
    <!-- 左侧合集列表（两级树 + 拖拽排序 + 新建入口） -->
    <CollectionSidebar
      v-model:current-id="currentId"
      v-model:drag-over-id="dragOverId"
      :collections="collections"
      :children-by-parent="childrenByParent"
      :loading="listLoading"
      @drag-start="onCollectionDragStart"
      @drop="onCollectionDrop"
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
          @edit-smart="openEditSmart"
          @solidify="confirmSolidify"
          @enter-ordering="enterOrdering"
          @save-order="saveItemOrder"
          @cancel-ordering="ordering = false"
          @rename="openRename"
          @delete="confirmDelete"
        />

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
        <CollectionOrderingStrip
          v-if="ordering"
          :items="orderedItems"
          @drag-start="onItemDragStart"
          @drop="onItemDrop"
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

.pagination-wrapper {
  display: flex;
  justify-content: center;
  padding: 24px 0;
}
</style>
