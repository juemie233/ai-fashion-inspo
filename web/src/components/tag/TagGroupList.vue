<script setup lang="ts">
/** 标签分组折叠列表：分组勾选、置顶、来源/次数展示、编辑/别名/合并/删除、拖拽改类别与自定义排序。
 * 大分组（数千标签）展开时分批渐进渲染，避免一次性挂载导致首帧卡死。 */

import { ref, reactive, watch, onUnmounted } from 'vue'
import { type TagCategoryGroup, type TagItem } from '@/api/tags'
import { CATEGORY_LABELS, SOURCE_LABELS } from '@/constants/tag'

const props = defineProps<{
  groups: TagCategoryGroup[]
  selectedIds: Set<number>
  sortMode: 'usage' | 'name' | 'custom'
  hasActiveFilter: boolean
}>()

const emit = defineEmits<{
  'toggle-select': [id: number]
  'select-all': [group: TagCategoryGroup]
  'deselect-all': []
  'toggle-pin': [tag: TagItem]
  'select-tag': [tag: TagItem]
  edit: [tag: TagItem]
  alias: [tag: TagItem]
  merge: [tag: TagItem]
  delete: [tagId: number, tagName: string]
  'drop-category': [tag: TagItem, category: string]
  'tag-drop': [target: TagItem, dragged: TagItem]
}>()

// ===== 拖拽状态（改类别 / 自定义排序共用） =====
const dragTag = ref<TagItem | null>(null)
const dragOverCategory = ref<string | null>(null)

function onDragStart(tag: TagItem) {
  dragTag.value = tag
}
function onDragOver(category: string, e: DragEvent) {
  e.preventDefault()
  dragOverCategory.value = category
}
function onDragLeave() {
  dragOverCategory.value = null
}
function onDropCategory(category: string) {
  dragOverCategory.value = null
  const tag = dragTag.value
  dragTag.value = null
  if (tag) emit('drop-category', tag, category)
}

function onTagDragOver(e: DragEvent) {
  if (props.sortMode === 'custom' && !props.hasActiveFilter) e.preventDefault()
}

function onTagDrop(target: TagItem) {
  const tag = dragTag.value
  dragTag.value = null
  if (tag) emit('tag-drop', target, tag)
}

// 来源颜色
function sourceColor(s: string) {
  return s === 'ai_generated' ? '#8b5cf6' : s === 'manual' ? '#3b82f6' : '#9ca3af'
}

// ===== 两段式删除确认（替代每行一个 popconfirm，大分组下显著降低组件开销） =====
/** 当前处于「确认删除」态的标签 id */
const deletingId = ref<number | null>(null)
let deletingTimer: number | null = null

function onDeleteClick(tag: TagItem) {
  if (deletingId.value === tag.id) {
    clearDeletingState()
    emit('delete', tag.id, tag.name)
    return
  }
  deletingId.value = tag.id
  if (deletingTimer !== null) window.clearTimeout(deletingTimer)
  deletingTimer = window.setTimeout(clearDeletingState, 3000)
}

function clearDeletingState() {
  deletingId.value = null
  if (deletingTimer !== null) {
    window.clearTimeout(deletingTimer)
    deletingTimer = null
  }
}

// ===== 大分组分批渐进渲染 =====
/** 展开中的分组名（受控，同时驱动折叠面板） */
const expandedNames = ref<string[]>([])
/** 每个分组当前已渲染的标签条数 */
const renderedCounts = reactive<Record<string, number>>({})
/** 分批渲染定时器（按分组保存，便于取消） */
const chunkTimers = new Map<string, number>()
/** 首帧渲染条数：让首次点击立刻有内容可看 */
const FIRST_CHUNK = 100
/** 后续每帧追加条数 */
const CHUNK_SIZE = 200

/** 分组当前应渲染的标签切片 */
function visibleTags(group: TagCategoryGroup): TagItem[] {
  const count = renderedCounts[group.category]
  return count === undefined ? group.tags : group.tags.slice(0, count)
}

function stopProgressiveRender(category: string) {
  const timer = chunkTimers.get(category)
  if (timer !== undefined) {
    window.clearTimeout(timer)
    chunkTimers.delete(category)
  }
}

/** 从当前进度继续分批渲染，直至全部渲染完 */
function startProgressiveRender(group: TagCategoryGroup) {
  const total = group.tags.length
  const current = renderedCounts[group.category] ?? 0
  if (current >= total) return // 已全部渲染
  stopProgressiveRender(group.category)
  if (current === 0) renderedCounts[group.category] = Math.min(FIRST_CHUNK, total)
  const step = () => {
    const done = renderedCounts[group.category] ?? 0
    if (done >= total) {
      chunkTimers.delete(group.category)
      return
    }
    renderedCounts[group.category] = Math.min(total, done + CHUNK_SIZE)
    chunkTimers.set(group.category, window.setTimeout(step, 16))
  }
  chunkTimers.set(group.category, window.setTimeout(step, 16))
}

// 展开分组时开始渐进渲染
watch(expandedNames, (names, oldNames) => {
  for (const name of names) {
    if (oldNames?.includes(name)) continue
    const group = props.groups.find((g) => g.category === name)
    if (group) startProgressiveRender(group)
  }
})

// 数据源变化（筛选/搜索/重载）导致标签数量变化时，对展开中的分组从头渐进渲染；
// 仅排序变化（长度不变）不重置进度，避免置顶/删除刷新后列表闪缩
const lastLengths = new Map<string, number>()
watch(
  () => props.groups.map((g) => g.tags),
  () => {
    for (const group of props.groups) {
      const prev = lastLengths.get(group.category)
      lastLengths.set(group.category, group.tags.length)
      if (
        prev !== undefined &&
        prev !== group.tags.length &&
        expandedNames.value.includes(group.category)
      ) {
        renderedCounts[group.category] = 0
        startProgressiveRender(group)
      }
    }
  },
)

onUnmounted(() => {
  for (const timer of chunkTimers.values()) window.clearTimeout(timer)
  chunkTimers.clear()
  if (deletingTimer !== null) window.clearTimeout(deletingTimer)
})
</script>

<template>
  <!-- a-collapse 默认销毁行为 destroy-on-hide=false：首次展开后内容保持挂载，再次展开/收起零渲染开销 -->
  <a-collapse v-model:active-key="expandedNames">
    <a-collapse-item v-for="group in groups" :key="group.category">
      <template #header>
        <a-space align="center">
          <a-checkbox
            @click.stop
            @change="
              (v: unknown) => (v === true ? emit('select-all', group) : emit('deselect-all'))
            "
            :model-value="group.tags.every((t) => selectedIds.has(t.id))"
            :indeterminate="
              group.tags.some((t) => selectedIds.has(t.id)) &&
              !group.tags.every((t) => selectedIds.has(t.id))
            "
          />
          <span>{{ CATEGORY_LABELS[group.category] || group.category }}</span>
          <a-tag size="small">{{ group.tags.length }}</a-tag>
        </a-space>
      </template>

      <div
        :style="{
          background: dragOverCategory === group.category ? '#3b82f620' : undefined,
          border:
            dragOverCategory === group.category ? '2px dashed #3b82f6' : '2px solid transparent',
          borderRadius: '8px',
          transition: 'all 0.2s',
          minHeight: '40px',
        }"
        @dragover="onDragOver(group.category, $event)"
        @dragleave="onDragLeave"
        @drop="onDropCategory(group.category)"
      >
        <!-- 轻量行：数千条标签时组件化行（list-item/popconfirm 等）挂载成本过高，
             改用原生元素 + CSS 实现同等交互，展开与滚动都快一个数量级 -->
        <div class="tag-list">
          <div
            v-for="tag in visibleTags(group)"
            :key="tag.id"
            v-memo="[
              selectedIds.has(tag.id),
              deletingId === tag.id,
              sortMode,
              hasActiveFilter,
              tag,
            ]"
            class="tag-row"
            :class="{ 'row-selected': selectedIds.has(tag.id) }"
            :draggable="sortMode === 'custom' && !hasActiveFilter"
            @dragstart="onDragStart(tag)"
            @dragover="onTagDragOver"
            @drop="onTagDrop(tag)"
          >
            <input
              type="checkbox"
              class="row-check"
              :checked="selectedIds.has(tag.id)"
              @click.stop
              @change="emit('toggle-select', tag.id)"
            />
            <button
              class="row-pin"
              :class="{ pinned: tag.pinned }"
              :title="tag.pinned ? '取消置顶' : '置顶到最前'"
              @click.stop="emit('toggle-pin', tag)"
            >
              📌
            </button>
            <span class="row-badge row-source" :style="{ background: sourceColor(tag.source) }">
              {{ SOURCE_LABELS[tag.source] || tag.source }}
            </span>
            <span class="row-badge row-usage">{{ tag.usage_count }} 次</span>
            <span
              class="row-name"
              :title="
                tag.description ? `点击查看素材 — ${tag.description}` : '点击查看使用该标签的素材'
              "
              @click="emit('select-tag', tag)"
              >{{ tag.name }}</span
            >
            <span class="row-actions">
              <button class="row-act" @click="emit('edit', tag)">编辑</button>
              <button class="row-act" @click="emit('alias', tag)">别名</button>
              <button class="row-act" @click="emit('merge', tag)">合并</button>
              <button
                class="row-act danger"
                :class="{ confirming: deletingId === tag.id }"
                @click="onDeleteClick(tag)"
              >
                {{ deletingId === tag.id ? '确认删除?' : '删除' }}
              </button>
            </span>
          </div>
        </div>
      </div>
    </a-collapse-item>
  </a-collapse>
</template>

<style scoped>
/* 轻量标签行：外观对齐原 n-list-item + 小号组件，但每行只有十几个原生节点 */
.tag-list {
  display: flex;
  flex-direction: column;
}

.tag-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 36px;
  padding: 4px 10px;
  border-bottom: 1px solid #f0f0f0;
  font-size: 13px;
  cursor: default;
}

.tag-row:hover {
  background: rgba(0, 0, 0, 0.03);
}

.tag-row.row-selected {
  background: rgba(59, 130, 246, 0.08);
}

/* 自定义排序模式下可拖拽，恢复抓手光标提示 */
.tag-row[draggable='true'] {
  cursor: grab;
}

.row-check {
  width: 18px;
  height: 18px;
  margin: 0;
  accent-color: #3b82f6;
  cursor: pointer;
  flex-shrink: 0;
}

.row-pin {
  border: none;
  background: transparent;
  padding: 0;
  font-size: 13px;
  cursor: pointer;
  opacity: 0.35;
  flex-shrink: 0;
}

.row-pin:hover {
  opacity: 0.8;
}

.row-pin.pinned {
  opacity: 1;
}

.row-badge {
  flex-shrink: 0;
  font-size: 11px;
  line-height: 1;
  padding: 3px 8px;
  border-radius: 10px;
  white-space: nowrap;
}

.row-source {
  color: #fff;
}

.row-usage {
  background: rgba(0, 0, 0, 0.06);
  color: #555;
}

.row-name {
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.row-name:hover {
  color: #3b82f6;
}

.row-actions {
  margin-left: auto;
  display: flex;
  gap: 4px;
  flex-shrink: 0;
  opacity: 0;
  transition: opacity 0.15s;
}

.tag-row:hover .row-actions {
  opacity: 1;
}

.row-act {
  border: none;
  background: transparent;
  padding: 2px 6px;
  font-size: 12px;
  color: #2080f0;
  cursor: pointer;
  border-radius: 4px;
}

.row-act:hover {
  background: rgba(32, 128, 240, 0.1);
}

.row-act.danger {
  color: #d03050;
}

.row-act.danger:hover {
  background: rgba(208, 48, 80, 0.1);
}

.row-act.confirming {
  color: #fff;
  background: #d03050;
}
</style>
