<script setup lang="ts">
/**
 * 任务状态标签：统一各页面的状态中文文案与颜色（单一来源 taskLabel.ts）。
 *
 * 同时兼容两种状态语义：success（任务队列）与 completed（采集任务），
 * 内部经 normalizeTaskStatus 归一后展示，未知状态回退原文 + 灰色。
 */

import { normalizeTaskStatus, TASK_STATUS_LABELS, taskStatusType } from '@/utils/taskLabel'

defineProps<{
  /** 原始状态值（如 pending/running/success/completed/failed/cancelled） */
  status: string
}>()
</script>

<template>
  <a-tag :color="taskStatusType(normalizeTaskStatus(status))" size="small">
    {{ TASK_STATUS_LABELS[normalizeTaskStatus(status)] || status }}
  </a-tag>
</template>
