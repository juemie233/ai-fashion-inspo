<script setup lang="ts">
/** 统计卡片：总素材 / 已分析 / 未分析 / 失败。 */

import type { QueueStats } from '@/types/analysis'

defineProps<{ queueStats: QueueStats }>()
</script>

<template>
  <a-row :gutter="[12, 12]" style="margin-bottom: 16px">
    <a-col :span="6"
      ><a-card size="small"><a-statistic title="总素材" :value="queueStats.total" /></a-card
    ></a-col>
    <a-col :span="6"
      ><a-card size="small"><a-statistic title="已分析" :value="queueStats.analyzed" /></a-card
    ></a-col>
    <a-col :span="6"
      ><a-card size="small"><a-statistic title="未分析" :value="queueStats.unanalyzed" /></a-card
    ></a-col>
    <a-col :span="6"
      ><a-card size="small"><a-statistic title="失败" :value="queueStats.failed" /></a-card
    ></a-col>
  </a-row>
</template>
