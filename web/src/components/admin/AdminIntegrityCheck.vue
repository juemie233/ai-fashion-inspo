<script setup lang="ts">
/** 数据完整性检查卡片：缺失文件 / 孤立文件检测与清理。 */

import type { MissingFile, OrphanFile } from '@/types/admin'
import type { TableColumnData } from '@arco-design/web-vue'
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

/** 缺失文件表格列定义（Arco render 的 record 转 MissingFile） */
const missingColumns: TableColumnData[] = [
  { title: '预期文件路径', dataIndex: 'file_path', ellipsis: true, tooltip: true },
  {
    title: '关联素材数',
    dataIndex: 'inspiration_ids',
    width: 100,
    render: ({ record }) => (record as MissingFile).inspiration_ids.length,
  },
]

/** 孤立文件表格列定义（Arco render 的 record 转 OrphanFile） */
const orphanColumns: TableColumnData[] = [
  { title: '文件路径', dataIndex: 'file_path', ellipsis: true, tooltip: true },
  {
    title: '大小',
    dataIndex: 'size_bytes',
    width: 100,
    render: ({ record }) => formatSize((record as OrphanFile).size_bytes),
  },
]
</script>

<template>
  <a-card title="数据完整性检查" size="small" style="margin-bottom: 24px">
    <template #extra>
      <a-space>
        <a-button size="small" :loading="checking" @click="emit('recheck')">
          重新检查
        </a-button>
        <a-popconfirm
          v-if="orphanFiles.length > 0"
          :content="`确定删除所有 ${orphanFiles.length} 个孤立文件？释放约 ${fmtSize(orphanBytes)}。此操作不可撤销。`"
          @ok="emit('cleanOrphans')"
        >
          <a-button size="small" type="outline" status="danger">
            清理孤立文件 ({{ orphanFiles.length }})
          </a-button>
        </a-popconfirm>
      </a-space>
    </template>

    <!-- 缺失文件 -->
    <div v-if="missingFiles.length > 0" style="margin-bottom: 16px">
      <h4 style="color: #d03050; margin: 0 0 8px">
        ❌ 缺失文件 ({{ missingFiles.length }}) — 数据库有记录但文件不存在
      </h4>
      <a-table
        :columns="missingColumns"
        :data="missingFiles.slice(0, 50)"
        :bordered="false"
        size="small"
        :max-height="300"
        :pagination="false"
      />
    </div>

    <!-- 孤立文件 -->
    <div v-if="orphanFiles.length > 0">
      <h4 style="color: #f0a020; margin: 0 0 8px">
        ⚠️ 孤立文件 ({{ orphanFiles.length }}) — 磁盘有文件但数据库无记录 · 共 {{ fmtSize(orphanBytes) }}
      </h4>
      <a-table
        :columns="orphanColumns"
        :data="orphanFiles.slice(0, 50)"
        :bordered="false"
        size="small"
        :max-height="300"
        :pagination="false"
      />
    </div>

    <a-empty
      v-if="missingFiles.length === 0 && orphanFiles.length === 0 && !checking"
      description="✅ 数据完整，未发现缺失或孤立文件"
    />
  </a-card>
</template>
