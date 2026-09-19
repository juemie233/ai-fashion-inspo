<script setup lang="ts">
/** 抖音采集历史（f2 增量下载）：只列 task_queue 里 f2_import 的任务。
 *
 * 与下方「CDP 采集历史」分开：那条链路是浏览器 CDP 搜索/博主页采集，这条是
 * f2 增量下载已登记博主的新作品，两者的操作与排查口径完全不同。
 *
 * 每行可按「查看结果」展开该批的**结果浏览与审查面板**（缩略图 + 筛选 + 勾选 +
 * 移入垃圾桶/还原/彻底删除），依据是任务落盘的批次清单。
 */

import { onMounted, onUnmounted, ref } from 'vue'
import TaskList from '@/components/task/TaskList.vue'
import F2ResultsPanel from '@/components/scraper/F2ResultsPanel.vue'
import type { UnifiedTask } from '@/types/task'
import { F2_HISTORY_PAGE_SIZE, useF2TaskHistory } from '@/composables/useF2TaskHistory'

const {
  tasks,
  loading,
  total,
  page,
  pageCount,
  hasActive,
  resultTaskIds,
  loadTasks,
  onPageChange,
  startPoll,
  stopPoll,
  cancelTask,
  deleteTask,
  pauseTask,
  resumeTask,
} = useF2TaskHistory()

/** 当前展开结果面板的任务 id（null 表示收起） */
const resultsTaskId = ref<number | null>(null)

/** 只有落盘了批次清单的任务才有可浏览的结果（入库阶段未完成的任务没有） */
function canViewResults(task: UnifiedTask): boolean {
  return resultTaskIds.value.has(task.id)
}

function openResults(task: UnifiedTask) {
  resultsTaskId.value = task.id
}

/** 供父组件调用：两条 f2 入口提交任务后立即刷新（不必等 WS 首个事件） */
defineExpose({ reload: () => loadTasks() })

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
      :can-view-results="canViewResults"
      @cancel="cancelTask"
      @delete="deleteTask"
      @pause="pauseTask"
      @resume="resumeTask"
      @results="openResults"
    />

    <a-empty v-else description="暂无抖音采集任务（点上方「一键获取素材」开始）" />

    <!-- 结果浏览与审查：key 绑任务 id，切换任务时重建面板状态（筛选/勾选不串味） -->
    <F2ResultsPanel
      v-if="resultsTaskId !== null"
      :key="resultsTaskId"
      :task-id="resultsTaskId"
      @close="resultsTaskId = null"
    />

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
