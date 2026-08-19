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
    <a-card size="small">
      <a-statistic title="素材总数" :value="stats?.total_count ?? 0" />
    </a-card>
    <a-card size="small">
      <div class="stat-custom">
        <span class="stat-custom-title">存储总大小</span>
        <span class="stat-custom-value">{{ fmtSize(totalBytes) }}</span>
      </div>
    </a-card>
    <a-card size="small">
      <div class="stat-custom">
        <span class="stat-custom-title">图片占用</span>
        <span class="stat-custom-value">{{ fmtSize(imagesBytes) }}</span>
      </div>
    </a-card>
    <a-card size="small">
      <div class="stat-custom">
        <span class="stat-custom-title">缩略图占用</span>
        <span class="stat-custom-value">{{ fmtSize(thumbnailsBytes) }}</span>
      </div>
    </a-card>
    <a-card size="small">
      <a-statistic title="标签总数" :value="stats?.total_tags ?? 0" />
    </a-card>
    <a-card size="small">
      <a-statistic title="收藏数" :value="stats?.favorite_count ?? 0" />
    </a-card>
    <a-card size="small" style="border-color: rgb(var(--primary-6))">
      <a-statistic title="📋 墓碑表记录" :value="stats?.tombstone_count ?? 0" />
      <div style="font-size: 11px; color: var(--color-text-3); margin-top: 6px">
        已采集 URL，防止重复入库
      </div>
    </a-card>
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

/* 自定义统计项（Arco Statistic 的 value 不接受字符串，尺寸类用文本展示） */
.stat-custom {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.stat-custom-title {
  font-size: 14px;
  color: var(--color-text-2);
}

.stat-custom-value {
  font-size: 22px;
  font-weight: 600;
  color: var(--color-text-1);
}
</style>
