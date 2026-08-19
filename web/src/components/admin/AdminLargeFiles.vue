<script setup lang="ts">
/** 占用空间最大的文件表格。 */

import { h } from 'vue'
import { Tag, type TableColumnData } from '@arco-design/web-vue'
import type { LargeFile } from '@/types/admin'
import { sourceLabel } from '@/utils/sourceLabel'
import { formatSize } from '@/utils/format'

defineProps<{ files: LargeFile[] }>()

/** 大文件表格列定义（Arco render 的 record 转 LargeFile） */
const fileColumns: TableColumnData[] = [
  { title: '文件路径', dataIndex: 'file_path', width: 320, ellipsis: true, tooltip: true },
  {
    title: '来源',
    dataIndex: 'source_type',
    width: 80,
    render: ({ record }) => sourceLabel((record as LargeFile).source_type),
  },
  {
    title: '大小',
    dataIndex: 'size_bytes',
    width: 90,
    render: ({ record }) => formatSize((record as LargeFile).size_bytes),
  },
  {
    title: '状态',
    dataIndex: 'exists',
    width: 70,
    render: ({ record }) =>
      (record as LargeFile).exists
        ? h(Tag, { color: 'green', size: 'small' }, '正常')
        : h(Tag, { color: 'red', size: 'small' }, '缺失'),
  },
]
</script>

<template>
  <a-card title="占用空间最大的文件 (Top 20)" size="small" style="margin-bottom: 24px">
    <a-table
      :columns="fileColumns"
      :data="files"
      :bordered="false"
      size="small"
      :max-height="400"
      :pagination="false"
    />
  </a-card>
</template>
