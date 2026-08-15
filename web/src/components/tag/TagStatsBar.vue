<script setup lang="ts">
/** 标签统计卡片条：总标签 / 未使用 / 关联数 / AI生成 / 手动。 */

import type { TagStats } from '@/api/tags'

defineProps<{ stats: TagStats | null }>()
</script>

<template>
  <n-grid v-if="stats" :cols="5" :x-gap="12" style="margin-bottom:16px">
    <n-gi><n-card size="small"><n-statistic label="总标签" :value="stats.total" /></n-card></n-gi>
    <n-gi><n-card size="small"><n-statistic label="未使用" :value="stats.unused" /></n-card></n-gi>
    <n-gi><n-card size="small"><n-statistic label="关联数" :value="stats.total_links" /></n-card></n-gi>
    <n-gi><n-card size="small"><n-statistic label="AI生成" :value="stats.by_source?.ai_generated || 0" /></n-card></n-gi>
    <n-gi><n-card size="small"><n-statistic label="手动" :value="stats.by_source?.manual || 0" /></n-card></n-gi>
  </n-grid>
</template>
