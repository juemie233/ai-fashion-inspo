<script setup lang="ts">
/** 标签统计卡片条：总标签 / 未使用 / 关联数 / AI生成 / 手动。 */

import type { TagStats } from '@/api/tags'

defineProps<{ stats: TagStats | null }>()
</script>

<template>
  <a-row v-if="stats" :gutter="[12, 12]" style="margin-bottom:16px">
    <a-col :span="5"><a-card size="small"><a-statistic title="总标签" :value="stats.total" /></a-card></a-col>
    <a-col :span="5"><a-card size="small"><a-statistic title="未使用" :value="stats.unused" /></a-card></a-col>
    <a-col :span="5"><a-card size="small"><a-statistic title="关联数" :value="stats.total_links" /></a-card></a-col>
    <a-col :span="5"><a-card size="small"><a-statistic title="AI生成" :value="stats.by_source?.ai_generated || 0" /></a-card></a-col>
    <a-col :span="5"><a-card size="small"><a-statistic title="手动" :value="stats.by_source?.manual || 0" /></a-card></a-col>
  </a-row>
</template>
