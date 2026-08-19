<script setup lang="ts">
/** 后台任务进度提示条：批量删除/去重/向量回填等任务进行中时展示进度。 */

import type { AdminTask } from '@/types/admin'
import { TASK_TYPE_LABELS } from '@/utils/taskLabel'

defineProps<{ task: AdminTask | null }>()
</script>

<template>
  <a-alert
    v-if="task && (task.status === 'pending' || task.status === 'running')"
    type="info"
    style="margin: 16px 0"
  >
    <template #title>
      {{ TASK_TYPE_LABELS[task.type] || task.type }}任务 #{{ task.id }} 进行中
      <span v-if="task.total > 0">（{{ task.done }}/{{ task.total }}）</span>
    </template>
    <a-progress type="line" :percent="task.progress" style="margin-top: 8px" />
  </a-alert>
</template>
