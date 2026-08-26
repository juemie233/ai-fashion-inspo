<script setup lang="ts">
/** 数据备份状态卡片：自动补备开关 / 最近成功备份 / 历史记录 / 日志尾部。
 * 任务管理页专用；只读展示，不触发备份。
 */
import { onMounted, ref } from 'vue'
import { fetchBackupStatus, type BackupStatus } from '@/api/admin'
import { formatDate } from '@/utils/format'

const loading = ref(false)
const status = ref<BackupStatus | null>(null)
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    status.value = await fetchBackupStatus()
  } catch {
    error.value = '备份状态加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <a-card class="backup-card" :bordered="false">
    <template #title>
      <span class="backup-title">数据备份</span>
      <a-tag :color="status?.running ? 'arcoblue' : 'gray'" size="small" class="backup-state-tag">
        {{ status?.running ? '备份进行中' : '空闲' }}
      </a-tag>
    </template>
    <template #extra>
      <a-button size="small" :loading="loading" @click="load">刷新</a-button>
    </template>

    <a-spin :loading="loading" style="width: 100%">
      <a-empty v-if="!status && !loading" :description="error || '暂无备份状态'" />
      <template v-else-if="status">
        <a-descriptions :column="3" size="small">
          <a-descriptions-item label="自动补备">
            <a-tag :color="status.enabled ? 'green' : 'gray'" size="small">
              {{ status.enabled ? '启用' : '停用' }}
            </a-tag>
          </a-descriptions-item>
          <a-descriptions-item label="最近成功备份">
            <span style="white-space: nowrap">
              {{ status.latest_success_at ? formatDate(status.latest_success_at) : '暂无' }}
            </span>
          </a-descriptions-item>
          <a-descriptions-item label="目标目录">
            <span class="backup-target">{{
              status.configured ? status.target_path : '未配置'
            }}</span>
          </a-descriptions-item>
        </a-descriptions>

        <div v-if="status.history.length" class="backup-history">
          <div class="backup-section-title">最近备份记录</div>
          <div v-for="item in status.history" :key="item.name" class="backup-history-row">
            <a-tag :color="item.success ? 'green' : 'red'" size="small">
              {{ item.success ? '成功' : '失败' }}
            </a-tag>
            <span class="backup-history-name">{{ item.name }}</span>
            <span style="margin-left: auto; white-space: nowrap">
              {{ item.time ? formatDate(item.time) : '—' }}
            </span>
          </div>
        </div>

        <details v-if="status.log_tail.length" class="backup-log">
          <summary>查看备份日志（最近 {{ status.log_tail.length }} 行）</summary>
          <pre class="backup-log-body">{{ status.log_tail.join('\n') }}</pre>
        </details>
      </template>
    </a-spin>
  </a-card>
</template>

<style scoped>
.backup-card {
  margin-bottom: 16px;
}
.backup-title {
  font-weight: 600;
}
.backup-state-tag {
  margin-left: 8px;
}
.backup-target {
  font-size: 13px;
  color: var(--color-text-2);
  word-break: break-all;
}
.backup-history {
  margin-top: 12px;
}
.backup-section-title {
  font-size: 13px;
  color: var(--color-text-3);
  margin-bottom: 8px;
}
.backup-history-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  font-size: 13px;
}
.backup-history-name {
  font-family: monospace;
  font-size: 13px;
}
.backup-log {
  margin-top: 12px;
}
.backup-log summary {
  cursor: pointer;
  color: rgb(var(--primary-6));
  font-size: 13px;
}
.backup-log-body {
  margin-top: 8px;
  max-height: 220px;
  overflow: auto;
  background: var(--color-fill-2);
  padding: 8px;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
