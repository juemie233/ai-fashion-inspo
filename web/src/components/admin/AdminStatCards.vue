<script setup lang="ts">
/** 管理后台概览统计卡片：素材总数、存储占用、标签、收藏、墓碑表记录。 */

import { computed } from 'vue'
import type { Stats } from '@/types/admin'
import { fmtSize } from '@/utils/format'

const props = defineProps<{ stats: Stats | null }>()

const totalBytes = computed(() => props.stats?.total_size_bytes ?? 0)
const imagesBytes = computed(() => props.stats?.images_size_bytes ?? 0)
const thumbnailsBytes = computed(() => props.stats?.thumbnail_size_bytes ?? 0)
</script>

<template>
  <div class="stat-cards">
    <n-card size="small">
      <n-statistic label="素材总数" :value="stats?.total_count ?? '-'" />
    </n-card>
    <n-card size="small">
      <n-statistic label="存储总大小" :value="fmtSize(totalBytes)" />
    </n-card>
    <n-card size="small">
      <n-statistic label="图片占用" :value="fmtSize(imagesBytes)" />
    </n-card>
    <n-card size="small">
      <n-statistic label="缩略图占用" :value="fmtSize(thumbnailsBytes)" />
    </n-card>
    <n-card size="small">
      <n-statistic label="标签总数" :value="stats?.total_tags ?? '-'" />
    </n-card>
    <n-card size="small">
      <n-statistic label="收藏数" :value="stats?.favorite_count ?? '-'" />
    </n-card>
    <n-card size="small" :bordered="true" style="border-color: #2080f0">
      <n-statistic label="📋 墓碑表记录" :value="stats?.tombstone_count ?? '-'" />
      <template #footer>
        <span style="font-size: 11px; color: #999">已采集 URL，防止重复入库</span>
      </template>
    </n-card>
  </div>
</template>

<style scoped>
/* 统计卡片网格 */
.stat-cards {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}
</style>
