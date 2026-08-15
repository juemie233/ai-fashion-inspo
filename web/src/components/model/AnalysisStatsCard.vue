<script setup lang="ts">
/** 统计卡片：总素材 / 已分析 / 未分析 / 失败。 */

import type { QueueStats } from '@/types/analysis'

defineProps<{ queueStats: QueueStats }>()
</script>

<template>
  <n-grid :cols="4" :x-gap="12" style="margin-bottom:16px">
    <n-gi><n-card size="small"><n-statistic label="总素材" :value="queueStats.total" /></n-card></n-gi>
    <n-gi><n-card size="small"><n-statistic label="已分析" :value="queueStats.analyzed" /></n-card></n-gi>
    <n-gi><n-card size="small"><n-statistic label="未分析" :value="queueStats.unanalyzed" /></n-card></n-gi>
    <n-gi><n-card size="small"><n-statistic label="失败" :value="queueStats.failed" /></n-card></n-gi>
  </n-grid>
</template>
