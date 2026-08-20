<script setup lang="ts">
/** 标签统计卡片条：总标签 / 未使用 / 关联数 / AI生成 / 手动。 */

import StatCardGrid from '@/components/common/StatCardGrid.vue'
import type { TagStats } from '@/api/tags'

defineProps<{ stats: TagStats | null }>()
</script>

<template>
  <StatCardGrid
    v-if="stats"
    :span="5"
    :items="[
      { title: '总标签', value: stats.total },
      { title: '未使用', value: stats.unused },
      { title: '关联数', value: stats.total_links },
      { title: 'AI生成', value: stats.by_source?.ai_generated || 0 },
      { title: '手动', value: stats.by_source?.manual || 0 },
    ]"
  />
</template>
