<script setup lang="ts">
/** 标签工具栏：搜索、类别/来源筛选、排序、批量操作、重复扫描、删除未使用。 */

import { CATEGORY_LABELS } from '@/api/tags'

const searchQuery = defineModel<string>('searchQuery', { required: true })
const filterCategory = defineModel<string | null>('filterCategory', { required: true })
const filterSource = defineModel<string | null>('filterSource', { required: true })
const sortMode = defineModel<'usage' | 'name' | 'custom'>('sortMode', { required: true })
const duplicateThreshold = defineModel<number>('duplicateThreshold', { required: true })

defineProps<{
  selectedCount: number
  unusedCount: number
  scanning: boolean
}>()

const emit = defineEmits<{
  'batch-delete': []
  'batch-merge': []
  'batch-category': []
  'batch-rename': []
  'deselect-all': []
  'find-duplicates': []
  'delete-unused': []
}>()

// Arco Select 的 modelValue 类型不含 null：用 undefined 承接空值，change 时转回 null 哨兵
function onCategoryChange(v: unknown) {
  filterCategory.value = (v as string | undefined) ?? null
}

function onSourceChange(v: unknown) {
  filterSource.value = (v as string | undefined) ?? null
}
</script>

<template>
  <a-space wrap style="margin-bottom:16px" :size="12">
    <a-input
      v-model="searchQuery"
      placeholder="搜索标签..."
      allow-clear
      style="width:200px"
    />
    <a-select
      :model-value="filterCategory ?? undefined"
      :options="Object.entries(CATEGORY_LABELS).map(([k,v])=>({label:v,value:k}))"
      style="width:120px"
      size="small"
      placeholder="类别"
      allow-clear
      @change="onCategoryChange"
    />
    <a-select
      :model-value="filterSource ?? undefined"
      :options="[
        { label: '预设', value: 'seed' },
        { label: 'AI生成', value: 'ai_generated' },
        { label: '手动', value: 'manual' },
      ]"
      style="width:110px"
      size="small"
      placeholder="来源"
      allow-clear
      @change="onSourceChange"
    />
    <a-radio-group v-model="sortMode" type="button" size="small">
      <a-radio value="usage">使用次数</a-radio>
      <a-radio value="name">名称</a-radio>
      <a-radio value="custom">自定义</a-radio>
    </a-radio-group>

    <a-divider direction="vertical" />

    <a-popconfirm
      v-if="selectedCount > 0"
      :content="`确认删除选中的 ${selectedCount} 个标签？此操作不可恢复`"
      @ok="emit('batch-delete')"
    >
      <a-button
        size="small"
        type="secondary"
        status="danger"
      >
        删除选中 ({{ selectedCount }})
      </a-button>
    </a-popconfirm>
    <a-button
      v-if="selectedCount >= 2"
      size="small"
      type="secondary"
      status="warning"
      @click="emit('batch-merge')"
    >
      合并选中
    </a-button>
    <a-button
      v-if="selectedCount > 0"
      size="small"
      type="secondary"
      @click="emit('batch-category')"
    >
      改类别
    </a-button>
    <a-button
      v-if="selectedCount > 0"
      size="small"
      type="secondary"
      @click="emit('batch-rename')"
    >
      重命名
    </a-button>
    <a-button v-if="selectedCount > 0" size="small" @click="emit('deselect-all')">
      取消选中
    </a-button>

    <a-divider direction="vertical" />

    <a-select
      v-model="duplicateThreshold"
      :options="[
        { label: '≥60%', value: 0.6 },
        { label: '≥70%', value: 0.7 },
        { label: '≥75%', value: 0.75 },
        { label: '≥80%', value: 0.8 },
        { label: '≥90%', value: 0.9 },
      ]"
      size="mini"
      style="width:80px"
      title="相似度阈值"
    />
    <a-button size="small" @click="emit('find-duplicates')" :loading="scanning">
      发现重复
    </a-button>
    <a-popconfirm
      content="确定删除所有未使用的标签？"
      @ok="emit('delete-unused')"
    >
      <a-button size="small" type="secondary" status="warning" :disabled="unusedCount === 0">
        删除未使用 {{ unusedCount > 0 ? `(${unusedCount})` : '' }}
      </a-button>
    </a-popconfirm>
  </a-space>
</template>
