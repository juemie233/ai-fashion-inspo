<script setup lang="ts">
/** 占用空间最大的文件表格。 */

import { h } from 'vue'
import { NTag } from 'naive-ui'
import type { LargeFile } from '@/types/admin'
import { sourceLabel } from '@/utils/sourceLabel'
import { formatSize } from '@/utils/format'

defineProps<{ files: LargeFile[] }>()

const fileColumns = [
  { title: '文件路径', key: 'file_path', width: 320, ellipsis: { tooltip: true } },
  {
    title: '来源', key: 'source_type', width: 80,
    render: (row: LargeFile) => sourceLabel(row.source_type),
  },
  {
    title: '大小', key: 'size_bytes', width: 90,
    render: (row: LargeFile) => formatSize(row.size_bytes),
  },
  {
    title: '状态', key: 'exists', width: 70,
    render: (row: LargeFile) =>
      row.exists
        ? h(NTag, { type: 'success', size: 'tiny' }, '正常')
        : h(NTag, { type: 'error', size: 'tiny' }, '缺失'),
  },
]
</script>

<template>
  <n-card title="占用空间最大的文件 (Top 20)" size="small" style="margin-bottom: 24px">
    <n-data-table
      :columns="fileColumns"
      :data="files"
      :bordered="false"
      size="small"
      :max-height="400"
    />
  </n-card>
</template>
