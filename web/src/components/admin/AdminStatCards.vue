<script setup lang="ts">
/** 管理后台概览统计卡片：素材总数、存储占用、标签、收藏、墓碑表记录。 */

import StatCardGrid from '@/components/common/StatCardGrid.vue'
import type { Stats } from '@/types/admin'
import { fmtSize } from '@/utils/format'

defineProps<{ stats: Stats | null }>()
</script>

<template>
  <StatCardGrid
    v-if="stats"
    :span="8"
    :items="[
      { title: '素材总数', value: stats.total_count ?? 0 },
      { title: '存储总大小', text: fmtSize(stats.total_size_bytes ?? 0) },
      { title: '图片占用', text: fmtSize(stats.images_size_bytes ?? 0) },
      { title: '缩略图占用', text: fmtSize(stats.thumbnail_size_bytes ?? 0) },
      { title: '标签总数', value: stats.total_tags ?? 0 },
      { title: '收藏数', value: stats.favorite_count ?? 0 },
      {
        title: '📋 墓碑表记录',
        value: stats.tombstone_count ?? 0,
        highlight: true,
        note: '已采集 URL，防止重复入库',
      },
    ]"
  />
</template>
