<script setup lang="ts">
/** 统计卡片：总素材 / 已分析 / 未分析 / 失败。 */

import StatCardGrid from '@/components/common/StatCardGrid.vue'
import type { QueueStats } from '@/types/analysis'

defineProps<{ queueStats: QueueStats }>()
</script>

<template>
  <StatCardGrid
    :items="[
      { title: '总素材', value: queueStats.total },
      { title: '已分析', value: queueStats.analyzed },
      { title: '未分析', value: queueStats.unanalyzed },
      { title: '失败', value: queueStats.failed },
    ]"
  />
</template>
