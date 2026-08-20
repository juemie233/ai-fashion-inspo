<script setup lang="ts">
/** 操作审计日志：展示破坏性批量操作的留痕（时间/动作/数量/释放空间）。 */

import { h, onMounted, ref } from 'vue'
import { Message, type TableColumnData } from '@arco-design/web-vue'
import { fetchAuditLogs, type AuditLogItem } from '@/api/admin'
import { formatDate, formatSize } from '@/utils/format'

const items = ref<AuditLogItem[]>([])
const loading = ref(false)

/** 操作类型中文映射 */
const ACTION_LABELS: Record<string, string> = {
  trash: '移入垃圾桶',
  restore: '恢复素材',
  batch_delete: '批量删除',
  delete_rejected: '已拒绝素材移入垃圾桶',
  cleanup_orphans: '清理孤立文件',
  empty_trash: '清空垃圾桶',
  batch_trash: '批量移入垃圾桶',
}

function actionLabel(action: string): string {
  return ACTION_LABELS[action] || action
}

/** 审计日志表格列定义（Arco render 的 record 转 AuditLogItem） */
const logColumns: TableColumnData[] = [
  {
    title: '时间',
    dataIndex: 'created_at',
    width: 190,
    render: ({ record }) =>
      h('span', { style: 'white-space: nowrap' }, formatDate((record as AuditLogItem).created_at)),
  },
  {
    title: '操作',
    dataIndex: 'action',
    width: 150,
    render: ({ record }) => actionLabel((record as AuditLogItem).action),
  },
  { title: '数量', dataIndex: 'count', width: 70, align: 'right' },
  {
    title: '释放空间',
    dataIndex: 'freed_bytes',
    width: 110,
    align: 'right',
    render: ({ record }) =>
      (record as AuditLogItem).freed_bytes > 0 ? formatSize((record as AuditLogItem).freed_bytes) : '-',
  },
  {
    title: '说明',
    dataIndex: 'detail',
    render: ({ record }) => (record as AuditLogItem).detail || '-',
  },
]

async function load() {
  loading.value = true
  try {
    items.value = await fetchAuditLogs(50)
  } catch {
    Message.error('加载审计日志失败')
    items.value = []
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <a-card title="操作审计日志" size="small">
    <template #extra>
      <a-button size="mini" type="text" @click="load">刷新</a-button>
    </template>
    <a-spin :loading="loading" style="display: block">
      <div class="audit-table-wrap">
        <a-table
          v-if="items.length > 0"
          :columns="logColumns"
          :data="items"
          size="small"
          :bordered="false"
          :pagination="false"
        />
        <div v-else-if="!loading" class="audit-empty">暂无破坏性操作记录</div>
      </div>
    </a-spin>
  </a-card>
</template>

<style scoped>
/* 表格整体居中：限制最大宽度并水平居中，避免内容贴左 */
.audit-table-wrap {
  max-width: 760px;
  margin: 0 auto;
}
.audit-empty {
  color: #999;
  font-size: 13px;
  text-align: center;
  padding: 24px 0;
}
</style>
