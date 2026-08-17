<script setup lang="ts">
/** 操作审计日志：展示破坏性批量操作的留痕（时间/动作/数量/释放空间）。 */

import { onMounted, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { fetchAuditLogs, type AuditLogItem } from '@/api/admin'
import { formatSize } from '@/utils/format'

const message = useMessage()
const items = ref<AuditLogItem[]>([])
const loading = ref(false)

/** 操作类型中文映射 */
const ACTION_LABELS: Record<string, string> = {
  batch_delete: '批量删除',
  delete_rejected: '已拒绝素材移入垃圾桶',
  cleanup_orphans: '清理孤立文件',
  empty_trash: '清空垃圾桶',
  batch_trash: '批量移入垃圾桶',
}

function actionLabel(action: string): string {
  return ACTION_LABELS[action] || action
}

function formatTime(iso: string | null): string {
  if (!iso) return '-'
  return new Date(iso).toLocaleString('zh-CN')
}

async function load() {
  loading.value = true
  try {
    items.value = await fetchAuditLogs(50)
  } catch {
    message.error('加载审计日志失败')
    items.value = []
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <n-card title="操作审计日志" size="small">
    <template #header-extra>
      <n-button size="tiny" quaternary @click="load">刷新</n-button>
    </template>
    <n-spin :show="loading">
      <n-table v-if="items.length > 0" size="small" :bordered="false">
        <thead>
          <tr>
            <th>时间</th>
            <th>操作</th>
            <th style="text-align: right">数量</th>
            <th style="text-align: right">释放空间</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="it in items" :key="it.id">
            <td>{{ formatTime(it.created_at) }}</td>
            <td>{{ actionLabel(it.action) }}</td>
            <td style="text-align: right">{{ it.count }}</td>
            <td style="text-align: right">{{ it.freed_bytes > 0 ? formatSize(it.freed_bytes) : '-' }}</td>
            <td>{{ it.detail || '-' }}</td>
          </tr>
        </tbody>
      </n-table>
      <div v-else-if="!loading" class="audit-empty">暂无破坏性操作记录</div>
    </n-spin>
  </n-card>
</template>

<style scoped>
.audit-empty {
  color: #999;
  font-size: 13px;
  text-align: center;
  padding: 24px 0;
}
</style>
