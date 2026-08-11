<script setup lang="ts">
/** 标签管理页：浏览/编辑/合并/批量操作/统计/导入导出。 */

import { ref, onMounted, h, computed, watch } from 'vue'
import { useMessage, useDialog } from 'naive-ui'
import { getFileUrl } from '@/api/inspirations'
import {
  fetchTagsGrouped, createTag, updateTag, mergeTags, getSimilarSuggestions,
  batchDeleteTags, deleteUnusedTags, fetchTagStats, findDuplicates,
  fetchTagInspirations, exportTags, importTags,
  type TagCategoryGroup, type TagItem, type TagStats, type DuplicatePair,
  type TagInspiration,
  CATEGORY_LABELS, SOURCE_LABELS,
} from '@/api/tags'

const message = useMessage()
const dialog = useDialog()

const groups = ref<TagCategoryGroup[]>([])
const loading = ref(false)
const searchQuery = ref('')
const filterCategory = ref<string | null>(null)
const sortMode = ref<'usage' | 'name' | 'recent'>('usage')

// ===== 统计 =====
const stats = ref<TagStats | null>(null)

// ===== 选中（批量操作） =====
const selectedIds = ref<Set<number>>(new Set())

// ===== 编辑弹窗 =====
const showEditModal = ref(false)
const editTag = ref<TagItem | null>(null)
const editName = ref('')
const editCategory = ref('')

// ===== 创建表单 =====
const showCreateForm = ref(false)
const newTagName = ref('')
const newTagCategory = ref('free')
const createSuggestions = ref<Array<{ id: number; name: string; category: string }>>([])
let suggestionDebounce: ReturnType<typeof setTimeout> | null = null

// ===== 合并弹窗 =====
const showMergeDialog = ref(false)
const mergeSource = ref<{ id: number; name: string } | null>(null)
const mergeTarget = ref<number | null>(null)

// ===== 合并所有选中 =====
const showBatchMergeDialog = ref(false)
const batchMergeTarget = ref<number | null>(null)

// ===== 重复扫描 =====
const scanningDuplicates = ref(false)
const duplicatePairs = ref<DuplicatePair[]>([])
const showDuplicatesPanel = ref(false)

// ===== 标签详情侧边栏 =====
const showDetailDrawer = ref(false)
const detailTag = ref<TagItem | null>(null)
const detailInspirations = ref<TagInspiration[]>([])
const detailTotal = ref(0)
const detailLoading = ref(false)

// ===== 导入/导出 =====
const showImportModal = ref(false)
const importJsonText = ref('')

// ===== 拖拽改类别 =====
const dragTag = ref<TagItem | null>(null)
const dragOverCategory = ref<string | null>(null)

// ===== 来源筛选 =====
const filterSource = ref<string | null>(null)

onMounted(async () => { await loadAll() })

async function loadAll() {
  loading.value = true
  try {
    groups.value = await fetchTagsGrouped()
    stats.value = await fetchTagStats()
  } catch { message.error('加载失败') } finally { loading.value = false }
}

// ===== 过滤与排序 =====
const filteredGroups = computed(() => {
  let result = groups.value.map(g => ({ ...g, tags: [...g.tags] }))

  // 类别筛选
  if (filterCategory.value) {
    result = result.filter(g => g.category === filterCategory.value)
  }
  // 来源筛选
  if (filterSource.value) {
    result = result.map(g => ({
      ...g,
      tags: g.tags.filter(t => t.source === filterSource.value),
    }))
  }
  // 搜索过滤
  if (searchQuery.value.trim()) {
    const q = searchQuery.value.trim().toLowerCase()
    result = result.map(g => ({
      ...g,
      tags: g.tags.filter(t => t.name.toLowerCase().includes(q)),
    }))
  }
  // 排序
  for (const g of result) {
    if (sortMode.value === 'usage') {
      g.tags.sort((a, b) => b.usage_count - a.usage_count)
    } else if (sortMode.value === 'name') {
      g.tags.sort((a, b) => a.name.localeCompare(b.name, 'zh'))
    }
  }
  // 移除空组
  return result.filter(g => g.tags.length > 0)
})

// ===== 创建标签（带去重建议） =====
function onNewTagNameInput() {
  if (suggestionDebounce) clearTimeout(suggestionDebounce)
  const name = newTagName.value.trim()
  if (!name || name.length < 2) {
    createSuggestions.value = []
    return
  }
  suggestionDebounce = setTimeout(async () => {
    try { createSuggestions.value = await getSimilarSuggestions(name) }
    catch { createSuggestions.value = [] }
  }, 300)
}

async function handleCreate() {
  if (!newTagName.value.trim()) return
  try {
    await createTag(newTagName.value.trim(), newTagCategory.value)
    message.success('标签已创建')
    showCreateForm.value = false
    newTagName.value = ''
    createSuggestions.value = []
    await loadAll()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '创建失败')
  }
}

// ===== 编辑标签 =====
function openEdit(tag: TagItem) {
  editTag.value = tag
  editName.value = tag.name
  editCategory.value = tag.category
  showEditModal.value = true
}

async function handleEdit() {
  if (!editTag.value || !editName.value.trim()) return
  try {
    await updateTag(editTag.value.id, {
      name: editName.value.trim() !== editTag.value.name ? editName.value.trim() : undefined,
      category: editCategory.value !== editTag.value.category ? editCategory.value : undefined,
    })
    message.success('标签已更新')
    showEditModal.value = false
    await loadAll()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '更新失败')
  }
}

// ===== 删除（单个 / 批量） =====
async function handleDelete(tagId: number, tagName: string) {
  try {
    await batchDeleteTags([tagId])
    message.success(`已删除 "${tagName}"`)
    selectedIds.value.delete(tagId)
    await loadAll()
  } catch { message.error('删除失败') }
}

async function handleBatchDelete() {
  if (selectedIds.value.size === 0) return
  const ids = Array.from(selectedIds.value)
  try {
    await batchDeleteTags(ids)
    message.success(`已删除 ${ids.length} 个标签`)
    selectedIds.value = new Set()
    await loadAll()
  } catch { message.error('批量删除失败') }
}

async function handleDeleteUnused() {
  try {
    const { data } = await deleteUnusedTags()
    message.success(data.message)
    await loadAll()
  } catch { message.error('删除失败') }
}

// ===== 合并 =====
async function handleMerge() {
  if (!mergeSource.value || !mergeTarget.value) return
  try {
    await mergeTags(mergeSource.value.id, mergeTarget.value)
    message.success('标签已合并')
    showMergeDialog.value = false
    mergeSource.value = null; mergeTarget.value = null
    await loadAll()
  } catch (e: any) { message.error(e.response?.data?.detail || '合并失败') }
}

function mergeTargetOptions() {
  if (!mergeSource.value) return []
  const opts: Array<{ label: string; value: number }> = []
  for (const group of groups.value) {
    for (const tag of group.tags) {
      if (tag.id !== mergeSource.value.id) {
        opts.push({ label: `${tag.name} (${CATEGORY_LABELS[tag.category] || tag.category})`, value: tag.id })
      }
    }
  }
  return opts
}

// ===== 批量合并选中 =====
function openBatchMerge() {
  if (selectedIds.value.size < 2) {
    message.warning('请至少选中 2 个标签')
    return
  }
  batchMergeTarget.value = null
  showBatchMergeDialog.value = true
}

function batchMergeTargetOptions() {
  const opts: Array<{ label: string; value: number }> = []
  for (const group of groups.value) {
    for (const tag of group.tags) {
      opts.push({ label: `${tag.name} (${CATEGORY_LABELS[tag.category] || tag.category})`, value: tag.id })
    }
  }
  return opts
}

async function handleBatchMerge() {
  if (!batchMergeTarget.value || selectedIds.value.size < 2) return
  const sourceIds = Array.from(selectedIds.value).filter(id => id !== batchMergeTarget.value)
  if (sourceIds.length === 0) {
    message.warning('目标标签不能在被选中的标签中')
    return
  }
  try {
    for (const sid of sourceIds) {
      await mergeTags(sid, batchMergeTarget.value)
    }
    message.success(`已将 ${sourceIds.length} 个标签合并`)
    showBatchMergeDialog.value = false
    selectedIds.value = new Set()
    await loadAll()
  } catch (e: any) { message.error('批量合并失败') }
}

// ===== 重复扫描 =====
async function handleFindDuplicates() {
  scanningDuplicates.value = true
  try {
    const data = await findDuplicates(0.75)
    duplicatePairs.value = data.duplicates
    showDuplicatesPanel.value = true
    if (data.total === 0) message.success('未发现重复标签')
    else message.info(`发现 ${data.total} 对相似标签`)
  } catch { message.error('扫描失败') }
  finally { scanningDuplicates.value = false }
}

async function quickMerge(a: number, b: number) {
  try {
    await mergeTags(a, b)
    message.success('已快速合并')
    // 从列表中移除
    duplicatePairs.value = duplicatePairs.value.filter(
      p => !(p.tag_a.id === a && p.tag_b.id === b)
    )
    await loadAll()
  } catch { message.error('合并失败') }
}

// ===== 标签详情 =====
async function openDetail(tag: TagItem) {
  detailTag.value = tag
  showDetailDrawer.value = true
  detailLoading.value = true
  try {
    const data = await fetchTagInspirations(tag.id, 1, 30)
    detailInspirations.value = data.items
    detailTotal.value = data.total
  } catch { message.error('加载详情失败') }
  finally { detailLoading.value = false }
}

// ===== 导入/导出 =====
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

async function handleImport() {
  if (!importJsonText.value.trim()) return
  try {
    const tags = JSON.parse(importJsonText.value)
    if (!Array.isArray(tags)) throw new Error('格式错误')
    const { data } = await importTags(tags)
    message.success(data.message)
    showImportModal.value = false
    importJsonText.value = ''
    await loadAll()
  } catch (e: any) {
    message.error('导入失败：' + (e.message || 'JSON 格式错误'))
  }
}

// ===== 选中逻辑 =====
function toggleSelect(id: number) {
  if (selectedIds.value.has(id)) selectedIds.value.delete(id)
  else selectedIds.value.add(id)
  selectedIds.value = new Set(selectedIds.value)
}
function selectAllInGroup(group: TagCategoryGroup) {
  for (const t of group.tags) selectedIds.value.add(t.id)
  selectedIds.value = new Set(selectedIds.value)
}
function deselectAll() { selectedIds.value = new Set() }

// ===== 拖拽改类别 =====
function onDragStart(tag: TagItem) { dragTag.value = tag }
function onDragOver(category: string, e: DragEvent) {
  e.preventDefault()
  dragOverCategory.value = category
}
function onDragLeave() { dragOverCategory.value = null }
async function onDrop(category: string) {
  dragOverCategory.value = null
  if (!dragTag.value || dragTag.value.category === category) return
  try {
    await updateTag(dragTag.value.id, { category })
    message.success(`已将 "${dragTag.value.name}" 移至 ${CATEGORY_LABELS[category] || category}`)
    await loadAll()
  } catch { message.error('移动失败') }
  dragTag.value = null
}

// 来源颜色
function sourceColor(s: string) {
  return s === 'ai_generated' ? '#8b5cf6' : s === 'manual' ? '#3b82f6' : '#9ca3af'
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
        <n-button @click="handleExport" secondary>导出</n-button>
        <n-button @click="showImportModal = true" secondary>导入</n-button>
      </n-space>
    </div>

    <!-- ===== 统计卡片 ===== -->
    <n-grid v-if="stats" :cols="5" :x-gap="12" style="margin-bottom:16px">
      <n-gi><n-card size="small"><n-statistic label="总标签" :value="stats.total" /></n-card></n-gi>
      <n-gi><n-card size="small"><n-statistic label="未使用" :value="stats.unused" /></n-card></n-gi>
      <n-gi><n-card size="small"><n-statistic label="关联数" :value="stats.total_links" /></n-card></n-gi>
      <n-gi><n-card size="small"><n-statistic label="AI生成" :value="stats.by_source?.ai_generated || 0" /></n-card></n-gi>
      <n-gi><n-card size="small"><n-statistic label="手动" :value="stats.by_source?.manual || 0" /></n-card></n-gi>
    </n-grid>

    <!-- ===== 工具栏：搜索 + 筛选 + 排序 + 批量操作 ===== -->
    <n-space align="center" style="margin-bottom:16px" :size="12">
      <n-input
        v-model:value="searchQuery"
        placeholder="搜索标签..."
        clearable
        style="width:200px"
      />
      <n-select
        v-model:value="filterCategory"
        :options="[
          { label: '全部类别', value: null },
          ...Object.entries(CATEGORY_LABELS).map(([k,v])=>({label:v,value:k})),
        ]"
        style="width:120px"
        size="small"
        placeholder="类别"
        clearable
      />
      <n-select
        v-model:value="filterSource"
        :options="[
          { label: '全部来源', value: null },
          { label: '预设', value: 'seed' },
          { label: 'AI生成', value: 'ai_generated' },
          { label: '手动', value: 'manual' },
        ]"
        style="width:110px"
        size="small"
        placeholder="来源"
        clearable
      />
      <n-radio-group v-model:value="sortMode" size="small">
        <n-radio-button value="usage">使用次数</n-radio-button>
        <n-radio-button value="name">名称</n-radio-button>
      </n-radio-group>

      <n-divider vertical />

      <n-button
        v-if="selectedIds.size > 0"
        size="small"
        type="error"
        secondary
        @click="handleBatchDelete"
      >
        删除选中 ({{ selectedIds.size }})
      </n-button>
      <n-button
        v-if="selectedIds.size >= 2"
        size="small"
        type="warning"
        secondary
        @click="openBatchMerge"
      >
        合并选中
      </n-button>
      <n-button v-if="selectedIds.size > 0" size="small" @click="deselectAll">
        取消选中
      </n-button>

      <n-divider vertical />

      <n-button size="small" @click="handleFindDuplicates" :loading="scanningDuplicates">
        发现重复
      </n-button>
      <n-popconfirm @positive-click="handleDeleteUnused">
        <template #trigger>
          <n-button size="small" type="warning" secondary :disabled="(stats?.unused || 0) === 0">
            删除未使用 {{ stats && stats.unused > 0 ? `(${stats.unused})` : '' }}
          </n-button>
        </template>
        确定删除所有未使用的标签？
      </n-popconfirm>
    </n-space>

    <!-- ===== 新建标签表单 ===== -->
    <n-card v-if="showCreateForm" title="创建新标签" size="small" style="margin-bottom:16px">
      <n-space align="flex-end">
        <n-form-item label="标签名">
          <n-input
            v-model:value="newTagName"
            placeholder="例如: 森系"
            @input="onNewTagNameInput"
          />
        </n-form-item>
        <n-form-item label="类别">
          <n-select
            v-model:value="newTagCategory"
            :options="Object.entries(CATEGORY_LABELS).map(([k, v]) => ({ label: v, value: k }))"
            style="width:140px"
          />
        </n-form-item>
        <n-button type="primary" @click="handleCreate">创建</n-button>
      </n-space>
      <!-- 去重建议 -->
      <div v-if="createSuggestions.length > 0" style="margin-top:8px">
        <span style="font-size:12px;color:#f0a020">⚠ 已有相似标签：</span>
        <n-space :size="4" style="margin-top:4px">
          <n-tag
            v-for="s in createSuggestions"
            :key="s.id"
            size="small"
            type="warning"
          >
            {{ s.name }} ({{ CATEGORY_LABELS[s.category] || s.category }})
          </n-tag>
        </n-space>
      </div>
    </n-card>

    <!-- ===== 重复标签面板 ===== -->
    <n-card v-if="showDuplicatesPanel && duplicatePairs.length > 0" title="相似标签" size="small" style="margin-bottom:16px;border-color:#f0a020">
      <template #header-extra>
        <n-button size="small" text @click="showDuplicatesPanel = false">关闭</n-button>
      </template>
      <n-list>
        <n-list-item v-for="pair in duplicatePairs.slice(0, 20)" :key="`${pair.tag_a.id}-${pair.tag_b.id}`">
          <n-space align="center">
            <n-tag size="small">{{ pair.tag_a.name }}</n-tag>
            <span style="font-size:12px;color:#999">相似度 {{ (pair.similarity * 100).toFixed(0) }}%</span>
            <n-tag size="small">{{ pair.tag_b.name }}</n-tag>
            <n-button size="tiny" type="warning" @click="quickMerge(pair.tag_a.id, pair.tag_b.id)">
              合并 → {{ pair.tag_a.name }}
            </n-button>
            <n-button size="tiny" type="warning" @click="quickMerge(pair.tag_b.id, pair.tag_a.id)">
              合并 → {{ pair.tag_b.name }}
            </n-button>
          </n-space>
        </n-list-item>
      </n-list>
    </n-card>

    <!-- ===== 标签分组列表 ===== -->
    <n-spin :show="loading">
      <div v-if="filteredGroups.length === 0 && !loading" style="text-align:center;padding:40px;color:#999">
        没有匹配的标签
      </div>

      <n-collapse v-else>
        <n-collapse-item
          v-for="group in filteredGroups"
          :key="group.category"
        >
          <template #header>
            <n-space align="center">
              <n-checkbox
                @click.stop
                @update:checked="(v: boolean) => v ? selectAllInGroup(group) : deselectAll()"
                :checked="group.tags.every(t => selectedIds.has(t.id))"
                :indeterminate="group.tags.some(t => selectedIds.has(t.id)) && !group.tags.every(t => selectedIds.has(t.id))"
              />
              <span>{{ CATEGORY_LABELS[group.category] || group.category }}</span>
              <n-tag size="small" :bordered="false">{{ group.tags.length }}</n-tag>
            </n-space>
          </template>

          <div
            :style="{
              background: dragOverCategory === group.category ? '#3b82f620' : undefined,
              border: dragOverCategory === group.category ? '2px dashed #3b82f6' : '2px solid transparent',
              borderRadius: '8px',
              transition: 'all 0.2s',
              minHeight: '40px',
            }"
            @dragover="onDragOver(group.category, $event)"
            @dragleave="onDragLeave"
            @drop="onDrop(group.category)"
          >
            <n-list hoverable clickable>
              <n-list-item
                v-for="tag in group.tags"
                :key="tag.id"
                draggable="true"
                @dragstart="onDragStart(tag)"
                style="cursor:grab"
              >
                <template #prefix>
                  <n-space align="center" :size="8">
                    <n-checkbox
                      :checked="selectedIds.has(tag.id)"
                      @update:checked="toggleSelect(tag.id)"
                      @click.stop
                    />
                    <n-tag size="small" :bordered="false" :color="{ color: sourceColor(tag.source), textColor: '#fff' }">
                      {{ SOURCE_LABELS[tag.source] || tag.source }}
                    </n-tag>
                    <n-tag size="small" :bordered="false">
                      {{ tag.usage_count }} 次
                    </n-tag>
                  </n-space>
                </template>

                <span
                  style="cursor:pointer"
                  @click="openDetail(tag)"
                  :title="'点击查看使用该标签的素材'"
                >{{ tag.name }}</span>

                <template #suffix>
                  <n-space :size="4">
                    <n-button size="tiny" text type="info" @click="openEdit(tag)">编辑</n-button>
                    <n-button size="tiny" text type="info"
                      @click="mergeSource = { id: tag.id, name: tag.name }; showMergeDialog = true"
                    >合并</n-button>
                    <n-popconfirm @positive-click="handleDelete(tag.id, tag.name)">
                      <template #trigger>
                        <n-button size="tiny" text type="error">删除</n-button>
                      </template>
                      确定删除标签 "{{ tag.name }}"？
                    </n-popconfirm>
                  </n-space>
                </template>
              </n-list-item>
            </n-list>
          </div>
        </n-collapse-item>
      </n-collapse>
    </n-spin>

    <!-- ===== 编辑弹窗 ===== -->
    <n-modal v-model:show="showEditModal" title="编辑标签" preset="card" style="width:420px">
      <n-form label-placement="left" label-width="60">
        <n-form-item label="名称">
          <n-input v-model:value="editName" />
        </n-form-item>
        <n-form-item label="类别">
          <n-select
            v-model:value="editCategory"
            :options="Object.entries(CATEGORY_LABELS).map(([k, v]) => ({ label: v, value: k }))"
          />
        </n-form-item>
      </n-form>
      <n-space justify="end" style="margin-top:16px">
        <n-button @click="showEditModal = false">取消</n-button>
        <n-button type="primary" @click="handleEdit">保存</n-button>
      </n-space>
    </n-modal>

    <!-- ===== 合并弹窗 ===== -->
    <n-modal v-model:show="showMergeDialog" title="合并标签" preset="card" style="width:500px">
      <p v-if="mergeSource">将 <strong>{{ mergeSource.name }}</strong> 合并到：</p>
      <n-select
        v-model:value="mergeTarget"
        :options="mergeTargetOptions()"
        placeholder="选择目标标签"
        filterable
        style="margin:16px 0"
      />
      <n-space justify="end">
        <n-button @click="showMergeDialog = false">取消</n-button>
        <n-button type="primary" @click="handleMerge" :disabled="!mergeTarget">确认合并</n-button>
      </n-space>
    </n-modal>

    <!-- ===== 批量合并弹窗 ===== -->
    <n-modal v-model:show="showBatchMergeDialog" title="批量合并" preset="card" style="width:500px">
      <p>将选中的 {{ selectedIds.size }} 个标签合并到：</p>
      <n-select
        v-model:value="batchMergeTarget"
        :options="batchMergeTargetOptions()"
        placeholder="选择目标标签"
        filterable
        style="margin:16px 0"
      />
      <n-space justify="end">
        <n-button @click="showBatchMergeDialog = false">取消</n-button>
        <n-button type="primary" @click="handleBatchMerge" :disabled="!batchMergeTarget">确认合并</n-button>
      </n-space>
    </n-modal>

    <!-- ===== 标签详情侧边栏 ===== -->
    <n-drawer v-model:show="showDetailDrawer" :width="420" placement="right">
      <n-drawer-content :title="detailTag ? `「${detailTag.name}」的素材` : '标签详情'" closable>
        <n-spin :show="detailLoading">
          <div v-if="detailInspirations.length === 0 && !detailLoading" style="color:#999;text-align:center;padding:40px">
            暂无使用该标签的素材
          </div>
          <div v-else class="detail-grid">
            <div
              v-for="item in detailInspirations"
              :key="item.inspiration_id"
              class="detail-card"
              :title="`置信度: ${(item.confidence * 100).toFixed(0)}%`"
            >
              <img
                v-if="item.thumbnail_path"
                :src="getFileUrl(item.thumbnail_path)"
                style="width:100%;aspect-ratio:2/3;object-fit:cover;border-radius:4px"
              />
              <div v-else style="width:100%;aspect-ratio:2/3;background:#f5f5f5;border-radius:4px;display:flex;align-items:center;justify-content:center;color:#ccc">
                无预览
              </div>
            </div>
          </div>
          <p v-if="detailTotal > 0" style="font-size:12px;color:#999;text-align:center;margin-top:12px">
            共 {{ detailTotal }} 个素材使用此标签
          </p>
        </n-spin>
      </n-drawer-content>
    </n-drawer>

    <!-- ===== 批量合并弹窗 ===== -->

    <!-- ===== 导出/导入弹窗 ===== -->
    <n-modal v-model:show="showImportModal" title="导入标签" preset="card" style="width:550px">
      <p style="font-size:13px;color:#999;margin-bottom:8px">
        粘贴 JSON 数组，每项含 name 和 category 字段：
      </p>
      <n-input
        v-model:value="importJsonText"
        type="textarea"
        :rows="10"
        placeholder='[{"name": "森系", "category": "style"}, ...]'
      />
      <n-space justify="end" style="margin-top:16px">
        <n-button @click="showImportModal = false">取消</n-button>
        <n-button type="primary" @click="handleImport" :disabled="!importJsonText.trim()">导入</n-button>
      </n-space>
    </n-modal>
  </div>
</template>

<style scoped>
.tag-page { max-width: 1000px; margin: 0 auto; }
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.page-header h2 { margin: 0; }

.detail-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
}
.detail-card { cursor: pointer; border-radius: 4px; overflow: hidden; transition: transform 0.15s; }
.detail-card:hover { transform: scale(1.03); }
</style>
