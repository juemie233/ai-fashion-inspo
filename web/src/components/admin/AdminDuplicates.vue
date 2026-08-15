<script setup lang="ts">
/** 重复文件检测卡片：检测重复与去重删除。 */

import type { DuplicateGroup, DedupResult } from '@/types/admin'
import { fmtSize, formatSize } from '@/utils/format'

defineProps<{
  duplicates: DuplicateGroup[]
  dupCount: number
  dupBytes: number
  checking: boolean
  deduplicating: boolean
  dedupResult: DedupResult | null
}>()

const emit = defineEmits<{
  (e: 'scan'): void
  (e: 'deduplicate'): void
}>()

const dupColumns = [
  { title: '文件路径', key: 'file_path', ellipsis: { tooltip: true } },
  {
    title: '大小', key: 'size_bytes', width: 100,
    render: (row: { file_path: string; size_bytes: number }) => formatSize(row.size_bytes),
  },
]
</script>

<template>
  <n-card title="重复文件检测" size="small" style="margin-bottom: 24px">
    <template #header-extra>
      <n-space>
        <n-button size="small" :loading="checking" @click="emit('scan')">
          检测重复
        </n-button>
        <n-popconfirm
          v-if="duplicates.length > 0"
          @positive-click="emit('deduplicate')"
        >
          <template #trigger>
            <n-button
              size="small"
              type="error"
              ghost
              :loading="deduplicating"
              :disabled="duplicates.length === 0"
            >
              删除重复文件 ({{ dupCount }})
            </n-button>
          </template>
          确定删除所有 {{ dupCount }} 个重复文件？<br/>
          每组将保留评分最高的 1 个（优先有标签/收藏/AI已分析的素材）。<br/>
          将释放约 {{ fmtSize(dupBytes) }} 空间。<br/>
          <b style="color: #d03050">此操作物理删除文件，不可撤销！</b>
        </n-popconfirm>
      </n-space>
    </template>

    <!-- 去重结果 -->
    <n-alert
      v-if="dedupResult && dedupResult.files_deleted > 0"
      type="success"
      style="margin-bottom: 12px"
    >
      已处理 {{ dedupResult.groups_processed }} 组，删除 {{ dedupResult.files_deleted }} 个文件，
      释放 {{ fmtSize(dedupResult.freed_bytes) }} 空间
    </n-alert>
    <n-alert
      v-if="dedupResult && dedupResult.files_deleted === 0"
      type="info"
      style="margin-bottom: 12px"
    >
      未发现可清理的重复文件
    </n-alert>

    <div v-if="duplicates.length > 0">
      <p style="color: #f0a020; margin-bottom: 12px">
        ⚠️ 发现 {{ duplicates.length }} 组重复文件，共 {{ dupCount }} 个冗余副本，浪费 {{ fmtSize(dupBytes) }} 空间
      </p>
      <div v-for="(group, gi) in duplicates.slice(0, 20)" :key="group.hash" style="margin-bottom: 16px">
        <n-tag type="info" size="tiny" style="margin-bottom: 6px">
          {{ group.files.length }} 个相同文件 ({{ formatSize(group.files[0].size_bytes) }} × {{ group.files.length }})
        </n-tag>
        <n-data-table
          :columns="dupColumns"
          :data="group.files"
          :bordered="false"
          size="small"
        />
      </div>
    </div>

    <n-empty v-else-if="!checking" description="✅ 未发现完全重复的文件" size="small" />
  </n-card>
</template>
