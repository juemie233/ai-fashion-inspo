<script setup lang="ts">
/** 最近上传：sessionStorage 记录的缩略图网格，点击跳转详情。 */

import { getFileUrl } from '@/api/inspirations'
import type { RecentUpload } from '@/types/upload'

defineProps<{
  items: RecentUpload[]
}>()

const emit = defineEmits<{
  (e: 'openDetail', id: string): void
}>()
</script>

<template>
  <div class="recent-section">
    <h3>最近上传 ({{ items.length }})</h3>
    <div class="recent-grid">
      <div
        v-for="item in items"
        :key="item.id"
        class="recent-card"
        @click="emit('openDetail', item.id)"
      >
        <video
          v-if="item.mediaType === 'video' && !item.thumbnailPath"
          :src="getFileUrl(item.filePath)"
          muted
          playsinline
          preload="metadata"
        />
        <img
          v-else-if="item.thumbnailPath || item.filePath"
          :src="getFileUrl(item.thumbnailPath || item.filePath)"
        />
        <div class="recent-card-overlay"><span>查看详情</span></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 最近上传 */
.recent-section {
  margin-top: 32px;
}

.recent-section h3 {
  margin: 0 0 12px;
  font-size: 16px;
  font-weight: 500;
}

.recent-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 12px;
}

.recent-card {
  position: relative;
  width: 100%;
  padding-bottom: 150%;
  border-radius: 10px;
  overflow: hidden;
  cursor: pointer;
  background: #f3f4f6;
  border: 1px solid #e5e7eb;
  transition: transform 0.15s, box-shadow 0.15s;
}

.recent-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1);
}

.recent-card img,
.recent-card video {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.recent-card-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  transition: opacity 0.15s;
  color: #fff;
  font-size: 14px;
}

.recent-card:hover .recent-card-overlay {
  opacity: 1;
}
</style>
