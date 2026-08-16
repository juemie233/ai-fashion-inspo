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
</script>

<template>
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
      <n-radio-button value="custom">自定义</n-radio-button>
    </n-radio-group>

    <n-divider vertical />

    <n-popconfirm
      v-if="selectedCount > 0"
      @positive-click="emit('batch-delete')"
    >
      <template #trigger>
        <n-button
          size="small"
          type="error"
          secondary
        >
          删除选中 ({{ selectedCount }})
        </n-button>
      </template>
      确认删除选中的 {{ selectedCount }} 个标签？此操作不可恢复
    </n-popconfirm>
    <n-button
      v-if="selectedCount >= 2"
      size="small"
      type="warning"
      secondary
      @click="emit('batch-merge')"
    >
      合并选中
    </n-button>
    <n-button
      v-if="selectedCount > 0"
      size="small"
      secondary
      @click="emit('batch-category')"
    >
      改类别
    </n-button>
    <n-button
      v-if="selectedCount > 0"
      size="small"
      secondary
      @click="emit('batch-rename')"
    >
      重命名
    </n-button>
    <n-button v-if="selectedCount > 0" size="small" @click="emit('deselect-all')">
      取消选中
    </n-button>

    <n-divider vertical />

    <n-select
      v-model:value="duplicateThreshold"
      :options="[
        { label: '≥60%', value: 0.6 },
        { label: '≥70%', value: 0.7 },
        { label: '≥75%', value: 0.75 },
        { label: '≥80%', value: 0.8 },
        { label: '≥90%', value: 0.9 },
      ]"
      size="tiny"
      style="width:80px"
      title="相似度阈值"
    />
    <n-button size="small" @click="emit('find-duplicates')" :loading="scanning">
      发现重复
    </n-button>
    <n-popconfirm @positive-click="emit('delete-unused')">
      <template #trigger>
        <n-button size="small" type="warning" secondary :disabled="unusedCount === 0">
          删除未使用 {{ unusedCount > 0 ? `(${unusedCount})` : '' }}
        </n-button>
      </template>
      确定删除所有未使用的标签？
    </n-popconfirm>
  </n-space>
</template>
