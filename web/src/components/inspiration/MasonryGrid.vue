<script setup lang="ts">
/** 瀑布流网格：自适应列数，展示素材卡片列表。

  使用 CSS columns 实现，性能好且代码简单。 */

import InspirationCard from './InspirationCard.vue'
import type { InspirationOut } from '@/api/inspirations'

defineProps<{
  items: InspirationOut[]
  loading?: boolean
  density?: 'compact' | 'standard' | 'comfortable'
}>()

const emit = defineEmits<{
  (e: 'delete', id: string): void
  (e: 'toggleFavorite', id: string): void
}>()
</script>

<template>
  <div class="masonry-container">
    <div class="masonry-grid" :class="'density-' + (density || 'standard')">
      <InspirationCard
        v-for="item in items"
        :key="item.id"
        :item="item"
        @delete="emit('delete', item.id)"
        @toggle-favorite="emit('toggleFavorite', item.id)"
      />
    </div>

    <!-- 加载指示器 -->
    <div v-if="loading" class="loading-bar">
      <n-spin size="small" />
      <span style="margin-left: 8px">加载中...</span>
    </div>

    <!-- 空状态 -->
    <n-empty
      v-if="!loading && items.length === 0"
      description="还没有素材，去上传或采集一些吧"
      style="margin-top: 80px"
    />
  </div>
</template>

<style scoped>
/* 标准密度（默认） */
.masonry-grid.density-standard { column-count: 4; column-gap: 16px; }
@media (max-width: 1400px) { .masonry-grid.density-standard { column-count: 3; } }
@media (max-width: 1000px) { .masonry-grid.density-standard { column-count: 2; } }
@media (max-width: 700px)  { .masonry-grid.density-standard { column-count: 1; } }

/* 紧凑密度 */
.masonry-grid.density-compact { column-count: 6; column-gap: 8px; }
@media (max-width: 1400px) { .masonry-grid.density-compact { column-count: 4; } }
@media (max-width: 1000px) { .masonry-grid.density-compact { column-count: 3; } }
@media (max-width: 700px)  { .masonry-grid.density-compact { column-count: 2; } }

/* 宽松密度 */
.masonry-grid.density-comfortable { column-count: 3; column-gap: 24px; }
@media (max-width: 1400px) { .masonry-grid.density-comfortable { column-count: 2; } }
@media (max-width: 1000px) { .masonry-grid.density-comfortable { column-count: 2; } }
@media (max-width: 700px)  { .masonry-grid.density-comfortable { column-count: 1; } }

.masonry-grid > * {
  break-inside: avoid;
  margin-bottom: 16px;
}

.loading-bar {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  color: #999;
}
</style>
