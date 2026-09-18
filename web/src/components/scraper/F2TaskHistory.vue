<script setup lang="ts">
/** 抖音采集历史（f2 增量下载）：只列 task_queue 里 f2_import 的任务。
 *
 * 与下方「CDP 采集历史」分开：那条链路是浏览器 CDP 搜索/博主页采集，这条是
 * f2 增量下载已登记博主的新作品，两者的操作与排查口径完全不同。
 */

import { onMounted, onUnmounted } from 'vue'
import TaskList from '@/components/task/TaskList.vue'
import { F2_HISTORY_PAGE_SIZE, useF2TaskHistory } from '@/composables/useF2TaskHistory'

const {
  tasks,
  loading,
  total,
  page,
  pageCount,
  hasActive,
  loadTasks,
  onPageChange,
  startPoll,
  stopPoll,
  cancelTask,
  deleteTask,
  pauseTask,
  resumeTask,
} = useF2TaskHistory()

onMounted(() => {
  void loadTasks()
  startPoll()
})
onUnmounted(stopPoll)
</script>

<template>
  <a-card title="抖音采集历史（f2 增量下载）" size="small" style="margin-bottom: 16px">
    <template #extra>
      <a-space align="center" size="small">
        <span style="font-size: 12px; color: #666">
          共 <b>{{ total }}</b> 条{{ hasActive ? ' · 有任务进行中' : '' }}
        </span>
        <a-button size="mini" :loading="loading" @click="loadTasks()">刷新</a-button>
      </a-space>
    </template>

    <TaskList
      v-if="tasks.length"
      :tasks="tasks"
      :loading="loading"
      @cancel="cancelTask"
      @delete="deleteTask"
      @pause="pauseTask"
      @resume="resumeTask"
    />

    <a-empty v-else description="暂无抖音采集任务（点上方「一键获取素材」开始）" />

    <a-pagination
      v-if="pageCount > 1"
      style="margin-top: 12px; justify-content: flex-end"
      :current="page"
      :page-size="F2_HISTORY_PAGE_SIZE"
      :total="total"
      @change="onPageChange"
    />
  </a-card>
</template>
