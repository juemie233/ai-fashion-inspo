<script setup lang="ts">
/** 后台任务进度提示条：批量删除/去重任务进行中时展示进度。 */

import type { AdminTask } from '@/types/admin'

defineProps<{ task: AdminTask | null }>()
</script>

<template>
  <n-alert v-if="task && (task.status === 'pending' || task.status === 'running')" type="info" style="margin: 16px 0">
    <template #header>
      {{ task.type === 'deduplicate' ? '去重任务' : '批量删除任务' }} #{{ task.id }} 进行中
      <span v-if="task.total > 0">（{{ task.done }}/{{ task.total }}）</span>
    </template>
    <n-progress type="line" :percentage="task.progress" style="margin-top:8px" />
  </n-alert>
</template>
