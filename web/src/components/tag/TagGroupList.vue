<script setup lang="ts">
/** 标签分组折叠列表：分组勾选、置顶、来源/次数展示、编辑/别名/合并/删除、拖拽改类别与自定义排序。 */

import { ref } from 'vue'
import { CATEGORY_LABELS, SOURCE_LABELS, type TagCategoryGroup, type TagItem } from '@/api/tags'

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

function onDragStart(tag: TagItem) { dragTag.value = tag }
function onDragOver(category: string, e: DragEvent) {
  e.preventDefault()
  dragOverCategory.value = category
}
function onDragLeave() { dragOverCategory.value = null }
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
</script>

<template>
  <n-collapse>
    <n-collapse-item
      v-for="group in groups"
      :key="group.category"
    >
      <template #header>
        <n-space align="center">
          <n-checkbox
            @click.stop
            @update:checked="(v: boolean) => v ? emit('select-all', group) : emit('deselect-all')"
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
        @drop="onDropCategory(group.category)"
      >
        <n-list hoverable clickable>
          <n-list-item
            v-for="tag in group.tags"
            :key="tag.id"
            :draggable="sortMode === 'custom' && !hasActiveFilter"
            @dragstart="onDragStart(tag)"
            @dragover="onTagDragOver"
            @drop="onTagDrop(tag)"
            style="cursor:grab"
          >
            <template #prefix>
              <n-space align="center" :size="8">
                <n-checkbox
                  :checked="selectedIds.has(tag.id)"
                  @update:checked="emit('toggle-select', tag.id)"
                  @click.stop
                />
                <n-button
                  size="tiny"
                  text
                  :type="tag.pinned ? 'warning' : 'tertiary'"
                  @click.stop="emit('toggle-pin', tag)"
                  :title="tag.pinned ? '取消置顶' : '置顶到最前'"
                >📌</n-button>
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
              @click="emit('select-tag', tag)"
              :title="tag.description ? `点击查看素材 — ${tag.description}` : '点击查看使用该标签的素材'"
            >{{ tag.name }}</span>

            <template #suffix>
              <n-space :size="4">
                <n-button size="tiny" text type="info" @click="emit('edit', tag)">编辑</n-button>
                <n-button size="tiny" text type="info" @click="emit('alias', tag)">别名</n-button>
                <n-button size="tiny" text type="info"
                  @click="emit('merge', tag)"
                >合并</n-button>
                <n-popconfirm @positive-click="emit('delete', tag.id, tag.name)">
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
</template>
