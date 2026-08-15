<script setup lang="ts">
/** 数据完整性检查卡片：缺失文件 / 孤立文件检测与清理。 */

import type { MissingFile, OrphanFile } from '@/types/admin'
import { fmtSize, formatSize } from '@/utils/format'

defineProps<{
  missingFiles: MissingFile[]
  orphanFiles: OrphanFile[]
  orphanBytes: number
  checking: boolean
}>()

const emit = defineEmits<{
  (e: 'recheck'): void
  (e: 'cleanOrphans'): void
}>()

const orphanColumns = [
  { title: '文件路径', key: 'file_path', ellipsis: { tooltip: true } },
  { title: '大小', key: 'size_bytes', width: 100, render: (row: OrphanFile) => formatSize(row.size_bytes) },
]
</script>

<template>
  <n-card title="数据完整性检查" size="small" style="margin-bottom: 24px">
    <template #header-extra>
      <n-space>
        <n-button size="small" :loading="checking" @click="emit('recheck')">
          重新检查
        </n-button>
        <n-popconfirm @positive-click="emit('cleanOrphans')" v-if="orphanFiles.length > 0">
          <template #trigger>
            <n-button size="small" type="error" ghost>
              清理孤立文件 ({{ orphanFiles.length }})
            </n-button>
          </template>
          确定删除所有 {{ orphanFiles.length }} 个孤立文件？释放约 {{ fmtSize(orphanBytes) }}。此操作不可撤销。
        </n-popconfirm>
      </n-space>
    </template>

    <!-- 缺失文件 -->
    <div v-if="missingFiles.length > 0" style="margin-bottom: 16px">
      <h4 style="color: #d03050; margin: 0 0 8px">
        ❌ 缺失文件 ({{ missingFiles.length }}) — 数据库有记录但文件不存在
      </h4>
      <n-data-table
        :columns="[
          { title: '预期文件路径', key: 'file_path', ellipsis: { tooltip: true } },
          { title: '关联素材数', key: 'inspiration_ids', width: 100, render: (row: MissingFile) => row.inspiration_ids.length },
        ]"
        :data="missingFiles.slice(0, 50)"
        :bordered="false"
        size="small"
        :max-height="300"
      />
    </div>

    <!-- 孤立文件 -->
    <div v-if="orphanFiles.length > 0">
      <h4 style="color: #f0a020; margin: 0 0 8px">
        ⚠️ 孤立文件 ({{ orphanFiles.length }}) — 磁盘有文件但数据库无记录 · 共 {{ fmtSize(orphanBytes) }}
      </h4>
      <n-data-table
        :columns="orphanColumns"
        :data="orphanFiles.slice(0, 50)"
        :bordered="false"
        size="small"
        :max-height="300"
      />
    </div>

    <n-empty
      v-if="missingFiles.length === 0 && orphanFiles.length === 0 && !checking"
      description="✅ 数据完整，未发现缺失或孤立文件"
      size="small"
    />
  </n-card>
</template>
