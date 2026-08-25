<script setup lang="ts">
/** 标签管理页：浏览/编辑/合并/批量操作/统计/导入导出。 */

import { computed, ref, onMounted } from 'vue'
import { Message } from '@arco-design/web-vue'
import TagInspirationGrid from '@/components/tag/TagInspirationGrid.vue'
import TagAnalyticsModal from '@/components/tag/TagAnalyticsModal.vue'
import TagStatsBar from '@/components/tag/TagStatsBar.vue'
import TagToolbar from '@/components/tag/TagToolbar.vue'
import TagCreateForm from '@/components/tag/TagCreateForm.vue'
import TagDuplicatesPanel from '@/components/tag/TagDuplicatesPanel.vue'
import TagGroupList from '@/components/tag/TagGroupList.vue'
import TagEditModal from '@/components/tag/TagEditModal.vue'
import TagMergeModal from '@/components/tag/TagMergeModal.vue'
import TagBatchMergeModal from '@/components/tag/TagBatchMergeModal.vue'
import TagImportModal from '@/components/tag/TagImportModal.vue'
import TagBatchEditModal from '@/components/tag/TagBatchEditModal.vue'
import TagAliasModal from '@/components/tag/TagAliasModal.vue'
import TagDuplicateCompareModal from '@/components/tag/TagDuplicateCompareModal.vue'
import { useSplitResize } from '@/composables/useSplitResize'
import { useTagManage } from '@/composables/useTagManage'
import { exportTags, type TagItem } from '@/api/tags'
import type { TagDuplicatePair } from '@/types/tag'

// ===== 左右分栏可拖拽间隔线 =====
const { containerRef, leftWidth, isDragging, startDrag } = useSplitResize({
  initial: 50,
  min: 20,
  max: 80,
})

// ===== 数据与操作（加载 / 筛选排序 / 选中 / 删除合并 / 重复扫描等） =====
const {
  groups,
  loading,
  stats,
  searchQuery,
  filterCategory,
  filterSource,
  sortMode,
  selectedIds,
  selectedTag,
  duplicatePairs,
  scanningDuplicates,
  showDuplicatesPanel,
  duplicateThreshold,
  filteredGroups,
  hasActiveFilter,
  loadAll,
  selectTag,
  onGridChanged,
  toggleSelect,
  selectAllInGroup,
  deselectAll,
  handleDelete,
  handleBatchDelete,
  handleDeleteUnused,
  scanDuplicates,
  togglePin,
  onDrop,
  onTagDrop,
} = useTagManage()

// ===== 重复标签图片对比弹窗 =====
const compareVisible = ref(false)
const comparePair = ref<TagDuplicatePair | null>(null)

function openCompare(pair: TagDuplicatePair) {
  comparePair.value = pair
  compareVisible.value = true
}

// 页面挂载时加载标签数据
onMounted(() => {
  loadAll()
})

// ===== 弹窗：用单一 activeModal 状态收敛多个布尔开关，payload 携带上下文 =====
type ModalName =
  'create' | 'edit' | 'merge' | 'batchMerge' | 'batchEdit' | 'import' | 'alias' | 'analytics' | null
const activeModal = ref<ModalName>(null)
/** 编辑/合并/别名弹窗的目标标签 */
const editTag = ref<TagItem | null>(null)
const mergeSource = ref<{ id: number; name: string } | null>(null)
const aliasTag = ref<TagItem | null>(null)

// ===== 统一批量编辑弹窗（逐行/查找替换/改类别） =====
const batchEditMode = ref<'inline' | 'replace' | 'category'>('inline')
/** 当前选中的标签快照（从所有分组收集，供批量编辑弹窗逐行展示） */
const selectedTags = computed(() => {
  const ids = selectedIds.value
  const out: TagItem[] = []
  for (const g of groups.value) {
    for (const t of g.tags) {
      if (ids.has(t.id)) out.push(t)
    }
  }
  return out
})

function closeModal() {
  activeModal.value = null
}

function openCreate() {
  activeModal.value = activeModal.value === 'create' ? null : 'create'
}

// ===== 打开弹窗 =====
function openEdit(tag: TagItem) {
  editTag.value = tag
  activeModal.value = 'edit'
}

function openMerge(tag: TagItem) {
  mergeSource.value = { id: tag.id, name: tag.name }
  activeModal.value = 'merge'
}

function openBatchMerge() {
  if (selectedIds.value.size < 2) {
    Message.warning('请至少选中 2 个标签')
    return
  }
  activeModal.value = 'batchMerge'
}

function openBatchEdit(mode: 'inline' | 'replace' | 'category') {
  if (selectedIds.value.size === 0) {
    Message.warning('请先勾选标签')
    return
  }
  batchEditMode.value = mode
  activeModal.value = 'batchEdit'
}

function openImport() {
  activeModal.value = 'import'
}

function openAliasManager(tag: TagItem) {
  aliasTag.value = tag
  activeModal.value = 'alias'
}

function openAnalytics() {
  activeModal.value = 'analytics'
}

/** 批量类操作完成后的统一刷新：清空选中并重载数据 */
function onBatchDone() {
  deselectAll()
  loadAll()
}

// ===== 导出 =====
async function handleExport() {
  try {
    const data = await exportTags()
    const blob = new Blob([JSON.stringify(data.tags, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'tags-export.json'
    a.click()
    URL.revokeObjectURL(url)
    Message.success('已导出')
  } catch {
    Message.error('导出失败')
  }
}
</script>

<template>
  <div class="tag-page">
    <div class="page-header">
      <h2>标签管理</h2>
      <a-space>
        <a-button @click="openCreate" type="primary">
          {{ activeModal === 'create' ? '取消' : '新标签' }}
        </a-button>
        <a-button @click="openAnalytics" type="secondary">标签分析</a-button>
        <a-button @click="handleExport" type="secondary">导出</a-button>
        <a-button @click="openImport" type="secondary">导入</a-button>
      </a-space>
    </div>

    <div ref="containerRef" class="split-layout" :class="{ dragging: isDragging }">
      <!-- ===== 左面板：标签列表 ===== -->
      <div class="left-panel" :style="{ width: leftWidth + '%' }">
        <!-- ===== 统计卡片 ===== -->
        <TagStatsBar :stats="stats" />

        <!-- ===== 工具栏：搜索 + 筛选 + 排序 + 批量操作 ===== -->
        <TagToolbar
          v-model:search-query="searchQuery"
          v-model:filter-category="filterCategory"
          v-model:filter-source="filterSource"
          v-model:sort-mode="sortMode"
          v-model:duplicate-threshold="duplicateThreshold"
          :selected-count="selectedIds.size"
          :unused-count="stats?.unused || 0"
          :scanning="scanningDuplicates"
          @batch-delete="handleBatchDelete"
          @batch-merge="openBatchMerge"
          @batch-category="openBatchEdit('category')"
          @batch-rename="openBatchEdit('replace')"
          @deselect-all="deselectAll"
          @find-duplicates="scanDuplicates"
          @delete-unused="handleDeleteUnused"
        />

        <!-- ===== 新建标签表单 ===== -->
        <TagCreateForm
          v-if="activeModal === 'create'"
          :show="true"
          @update:show="closeModal"
          @created="loadAll"
        />

        <!-- ===== 重复标签面板 ===== -->
        <TagDuplicatesPanel
          v-if="showDuplicatesPanel"
          :pairs="duplicatePairs"
          @close="showDuplicatesPanel = false"
          @compare="openCompare"
        />

        <!-- ===== 标签分组列表 ===== -->
        <a-spin :loading="loading">
          <div
            v-if="filteredGroups.length === 0 && !loading"
            style="text-align: center; padding: 40px; color: #999"
          >
            没有匹配的标签
          </div>

          <TagGroupList
            v-else
            :groups="filteredGroups"
            :selected-ids="selectedIds"
            :sort-mode="sortMode"
            :has-active-filter="hasActiveFilter"
            @toggle-select="toggleSelect"
            @select-all="selectAllInGroup"
            @deselect-all="deselectAll"
            @toggle-pin="togglePin"
            @select-tag="selectTag"
            @edit="openEdit"
            @alias="openAliasManager"
            @merge="openMerge"
            @delete="handleDelete"
            @drop-category="onDrop"
            @tag-drop="onTagDrop"
          />
        </a-spin>
      </div>
      <!-- /left-panel -->

      <!-- 可拖拽间隔线 -->
      <div class="divider" :class="{ dragging: isDragging }" @mousedown="startDrag" />

      <!-- ===== 右面板：选中标签的素材网格 ===== -->
      <div class="right-panel">
        <TagInspirationGrid :tag="selectedTag" @changed="onGridChanged" />
      </div>
      <!-- /right-panel -->
    </div>
    <!-- /split-layout -->

    <!-- ===== 编辑弹窗 ===== -->
    <TagEditModal
      v-if="activeModal === 'edit'"
      :show="true"
      :tag="editTag"
      @update:show="closeModal"
      @saved="loadAll"
    />

    <!-- ===== 合并弹窗 ===== -->
    <TagMergeModal
      v-if="activeModal === 'merge'"
      :show="true"
      :source="mergeSource"
      :groups="groups"
      @update:show="closeModal"
      @merged="loadAll"
    />

    <!-- ===== 批量合并弹窗 ===== -->
    <TagBatchMergeModal
      v-if="activeModal === 'batchMerge'"
      :show="true"
      :selected-ids="selectedIds"
      :groups="groups"
      @update:show="closeModal"
      @done="onBatchDone"
    />

    <!-- ===== 导入弹窗 ===== -->
    <TagImportModal
      v-if="activeModal === 'import'"
      :show="true"
      @update:show="closeModal"
      @imported="loadAll"
    />

    <!-- ===== 批量编辑（逐行/查找替换/改类别，统一弹窗） ===== -->
    <TagBatchEditModal
      v-if="activeModal === 'batchEdit'"
      :visible="true"
      :tags="selectedTags"
      :initial-mode="batchEditMode"
      @update:visible="closeModal"
    />

    <!-- ===== 别名管理 ===== -->
    <TagAliasModal
      v-if="activeModal === 'alias'"
      :show="true"
      :tag="aliasTag"
      @update:show="closeModal"
    />

    <!-- ===== 标签分析 ===== -->
    <TagAnalyticsModal v-if="activeModal === 'analytics'" :show="true" @update:show="closeModal" />

    <!-- ===== 重复标签图片对比（合并/重命名后经事件总线自动刷新） ===== -->
    <TagDuplicateCompareModal v-model:visible="compareVisible" :pair="comparePair" />
  </div>
</template>

<style scoped>
.tag-page {
  max-width: 100%;
  margin: 0 auto;
  height: calc(100vh - 120px);
  display: flex;
  flex-direction: column;
}
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  flex-shrink: 0;
}
.page-header h2 {
  margin: 0;
}

/* 左右分屏（左栏宽度由 useSplitResize 控制） */
.split-layout {
  display: flex;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
.split-layout.dragging {
  user-select: none;
}

.left-panel {
  overflow-y: auto;
  padding-right: 8px;
  flex-shrink: 0;
}

/* 可拖拽间隔线 */
.divider {
  width: 6px;
  flex-shrink: 0;
  cursor: col-resize;
  position: relative;
}
.divider::after {
  content: '';
  position: absolute;
  left: 2px;
  right: 2px;
  top: 0;
  bottom: 0;
  background: #e5e7eb;
  border-radius: 2px;
  transition: background 0.15s;
}
.divider:hover::after,
.divider.dragging::after {
  background: #3b82f6;
}

.right-panel {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  padding-left: 12px;
}

@media (max-width: 900px) {
  .split-layout {
    flex-direction: column;
  }
  .left-panel,
  .right-panel {
    flex: none;
    max-height: 50vh;
  }
  .left-panel {
    width: 100% !important;
  }
  .right-panel {
    width: 100%;
    border-top: 1px solid #e5e7eb;
    padding-left: 0;
    padding-top: 12px;
  }
  .divider {
    display: none;
  }
}
</style>
