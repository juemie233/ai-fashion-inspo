<script setup lang="ts">
/** 任务管理页：聚合任务队列与采集任务，统一查看与操作。 */

import { onMounted, onUnmounted } from 'vue'
import { Button, Select, Pagination, Space } from '@arco-design/web-vue'
import { useTaskCenter } from '@/composables/useTaskCenter'
import { TASK_TYPE_LABELS } from '@/utils/taskLabel'
import TaskList from '@/components/task/TaskList.vue'

const {
  loading,
  statusFilter,
  typeFilter,
  page,
  pageCount,
  retrying,
  total,
  pageItems,
  hasActive,
  hasFailedScraper,
  loadTasks,
  onFilterChange,
  cancelTask,
  deleteTask,
  retryFailedScraper,
  startPoll,
  stopPoll,
} = useTaskCenter()

// 类型筛选选项从映射表派生：与 TASK_TYPE_LABELS 单一来源，避免漏配新类型
const typeOptions = [
  { label: '全部类型', value: '' },
  ...Object.entries(TASK_TYPE_LABELS).map(([value, label]) => ({ label, value })),
]

const statusOptions = [
  { label: '全部状态', value: '' },
  { label: '排队中', value: 'pending' },
  { label: '运行中', value: 'running' },
  { label: '已完成', value: 'success' },
  { label: '失败', value: 'failed' },
  { label: '已取消', value: 'cancelled' },
]

onMounted(() => {
  loadTasks()
  startPoll()
})

onUnmounted(stopPoll)
</script>

<template>
  <div class="task-page">
    <h2>任务管理</h2>
    <p class="subtitle">查看后台处理与采集任务的状态、进度与结果</p>

    <div class="toolbar">
      <a-space>
        <a-select
          v-model="typeFilter"
          :options="typeOptions"
          style="width: 140px"
          @change="onFilterChange"
        />
        <a-select
          v-model="statusFilter"
          :options="statusOptions"
          style="width: 120px"
          @change="onFilterChange"
        />
        <a-button @click="loadTasks">刷新</a-button>
        <a-button v-if="hasFailedScraper" :loading="retrying" @click="retryFailedScraper">
          重试失败采集任务
        </a-button>
      </a-space>
      <span class="summary">共 {{ total }} 条{{ hasActive ? '（有任务运行中）' : '' }}</span>
    </div>

    <task-list :tasks="pageItems" :loading="loading" @cancel="cancelTask" @delete="deleteTask" />

    <a-pagination
      v-model:current="page"
      :total="total"
      :page-size="20"
      style="margin-top: 16px; justify-content: flex-end"
    />
  </div>
</template>

<style scoped>
.task-page {
  max-width: 1100px;
  margin: 0 auto;
}
.subtitle {
  color: #999;
  margin-bottom: 20px;
}
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.summary {
  color: #999;
  font-size: 13px;
}
</style>
