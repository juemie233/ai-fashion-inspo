<script setup lang="ts">
/** 标签管理页：浏览/编辑/合并/批量操作/统计/导入导出。 */

import { ref, onMounted } from 'vue'
import { useMessage } from 'naive-ui'
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
import TagBatchCategoryModal from '@/components/tag/TagBatchCategoryModal.vue'
import TagBatchRenameModal from '@/components/tag/TagBatchRenameModal.vue'
import TagAliasModal from '@/components/tag/TagAliasModal.vue'
import { useSplitResize } from '@/composables/useSplitResize'
import { useTagManage } from '@/composables/useTagManage'
import { exportTags, type TagItem } from '@/api/tags'

const message = useMessage()

// ===== 左右分栏可拖拽间隔线 =====
const { containerRef, leftWidth, isDragging, startDrag } = useSplitResize({ initial: 50, min: 20, max: 80 })

// ===== 数据与操作（加载 / 筛选排序 / 选中 / 删除合并 / 重复扫描等） =====
const {
  groups, loading, stats, searchQuery, filterCategory, filterSource, sortMode,
  selectedIds, selectedTag, duplicatePairs, scanningDuplicates, showDuplicatesPanel,
  filteredGroups, hasActiveFilter,
  loadAll, selectTag, onGridChanged, toggleSelect, selectAllInGroup, deselectAll,
  handleDelete, handleBatchDelete, handleDeleteUnused, scanDuplicates,
  quickMerge, quickSetAlias, togglePin, onDrop, onTagDrop,
} = useTagManage()

// 页面挂载时加载标签数据
onMounted(() => {
  loadAll()
})

// ===== 弹窗开关 =====
const showCreateForm = ref(false)
const showEditModal = ref(false)
const editTag = ref<TagItem | null>(null)
const showMergeDialog = ref(false)
const mergeSource = ref<{ id: number; name: string } | null>(null)
const showBatchMergeDialog = ref(false)
const showImportModal = ref(false)
const showBatchCategoryDialog = ref(false)
const showBatchRenameDialog = ref(false)
const showAliasModal = ref(false)
const aliasTag = ref<TagItem | null>(null)
const showAnalytics = ref(false)

// ===== 打开弹窗 =====
function openEdit(tag: TagItem) {
  editTag.value = tag
  showEditModal.value = true
}

function openMerge(tag: TagItem) {
  mergeSource.value = { id: tag.id, name: tag.name }
  showMergeDialog.value = true
}

function openBatchMerge() {
  if (selectedIds.value.size < 2) {
    message.warning('请至少选中 2 个标签')
    return
  }
  showBatchMergeDialog.value = true
}

function openAliasManager(tag: TagItem) {
  aliasTag.value = tag
  showAliasModal.value = true
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
    a.href = url; a.download = 'tags-export.json'; a.click()
    URL.revokeObjectURL(url)
    message.success('已导出')
  } catch { message.error('导出失败') }
}
</script>

<template>
  <div class="tag-page">
    <div class="page-header">
      <h2>标签管理</h2>
      <n-space>
        <n-button @click="showCreateForm = !showCreateForm" type="primary">
          {{ showCreateForm ? '取消' : '新标签' }}
        </n-button>
        <n-button @click="showAnalytics = true" secondary>标签分析</n-button>
        <n-button @click="handleExport" secondary>导出</n-button>
        <n-button @click="showImportModal = true" secondary>导入</n-button>
      </n-space>
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
      :selected-count="selectedIds.size"
      :unused-count="stats?.unused || 0"
      :scanning="scanningDuplicates"
      @batch-delete="handleBatchDelete"
      @batch-merge="openBatchMerge"
      @batch-category="showBatchCategoryDialog = true"
      @batch-rename="showBatchRenameDialog = true"
      @deselect-all="deselectAll"
      @find-duplicates="scanDuplicates"
      @delete-unused="handleDeleteUnused"
    />

    <!-- ===== 新建标签表单 ===== -->
    <TagCreateForm v-model:show="showCreateForm" @created="loadAll" />

    <!-- ===== 重复标签面板 ===== -->
    <TagDuplicatesPanel
      v-if="showDuplicatesPanel"
      :pairs="duplicatePairs"
      @close="showDuplicatesPanel = false"
      @merge="quickMerge"
      @set-alias="quickSetAlias"
    />

    <!-- ===== 标签分组列表 ===== -->
    <n-spin :show="loading">
      <div v-if="filteredGroups.length === 0 && !loading" style="text-align:center;padding:40px;color:#999">
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
    </n-spin>
    </div><!-- /left-panel -->

    <!-- 可拖拽间隔线 -->
    <div class="divider" :class="{ dragging: isDragging }" @mousedown="startDrag" />

    <!-- ===== 右面板：选中标签的素材网格 ===== -->
    <div class="right-panel">
      <TagInspirationGrid :tag="selectedTag" @changed="onGridChanged" />
    </div><!-- /right-panel -->

    </div><!-- /split-layout -->

    <!-- ===== 编辑弹窗 ===== -->
    <TagEditModal v-model:show="showEditModal" :tag="editTag" @saved="loadAll" />

    <!-- ===== 合并弹窗 ===== -->
    <TagMergeModal v-model:show="showMergeDialog" :source="mergeSource" :groups="groups" @merged="loadAll" />

    <!-- ===== 批量合并弹窗 ===== -->
    <TagBatchMergeModal v-model:show="showBatchMergeDialog" :selected-ids="selectedIds" :groups="groups" @done="onBatchDone" />

    <!-- ===== 导出/导入弹窗 ===== -->
    <TagImportModal v-model:show="showImportModal" @imported="loadAll" />

    <!-- ===== 批量改类别 ===== -->
    <TagBatchCategoryModal v-model:show="showBatchCategoryDialog" :selected-ids="selectedIds" @done="onBatchDone" />

    <!-- ===== 批量重命名 ===== -->
    <TagBatchRenameModal v-model:show="showBatchRenameDialog" :selected-ids="selectedIds" @done="onBatchDone" />

    <!-- ===== 别名管理 ===== -->
    <TagAliasModal v-model:show="showAliasModal" :tag="aliasTag" />

    <!-- ===== 标签分析 ===== -->
    <TagAnalyticsModal v-model:show="showAnalytics" />

  </div>
</template>

<style scoped>
.tag-page { max-width: 100%; margin: 0 auto; height: calc(100vh - 120px); display: flex; flex-direction: column; }
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; flex-shrink: 0; }
.page-header h2 { margin: 0; }

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
  .split-layout { flex-direction: column; }
  .left-panel, .right-panel { flex: none; max-height: 50vh; }
  .left-panel { width: 100% !important; }
  .right-panel { width: 100%; border-top: 1px solid #e5e7eb; padding-left: 0; padding-top: 12px; }
  .divider { display: none; }
}
</style>
