<script setup lang="ts">
/** 上传队列：缩略图网格、状态角标、错误提示、移除与视频预览。 */

import type { UploadQueueItem } from '@/types/upload'
import { isVideoFile } from '@/utils/media'

defineProps<{
  queue: UploadQueueItem[]
  pending: number
  done: number
  failed: number
  dups: number
  uploading: boolean
  speed: string
}>()

const emit = defineEmits<{
  (e: 'clear'): void
  (e: 'remove', id: string): void
  (e: 'preview', item: UploadQueueItem): void
}>()
</script>

<template>
  <div class="queue-section">
    <div class="queue-header">
      <span>上传队列 ({{ queue.length }})</span>
      <span style="font-size:12px;color:#999">
        待上传 {{ pending }} · 已完成 {{ done }} · 失败 {{ failed }}
        <template v-if="dups > 0"> · 跳过 {{ dups }}</template>
        <template v-if="uploading && speed"> · <span style="color:#6366f1">{{ speed }}</span></template>
      </span>
      <a-space>
        <a-button size="mini" @click="emit('clear')" :disabled="uploading">清空队列</a-button>
      </a-space>
    </div>

    <div class="queue-grid">
      <div
        v-for="item in queue"
        :key="item.id"
        class="queue-card"
        :class="item.status"
      >
        <video
          v-if="isVideoFile(item.file)"
          :src="item.thumbnail"
          muted
          playsinline
          preload="metadata"
          class="queue-thumb"
          title="点击预览"
          @click="emit('preview', item)"
        />
        <img
          v-else
          :src="item.thumbnail"
          :alt="item.file.name"
          class="queue-thumb"
        />
        <div v-if="isVideoFile(item.file)" class="queue-video-badge" title="点击预览">▶</div>
        <div class="queue-card-status">
          <template v-if="item.status === 'pending'">⏳</template>
          <template v-else-if="item.status === 'uploading'">
            <a-spin :size="14" />
          </template>
          <template v-else-if="item.status === 'done'">✅</template>
          <template v-else-if="item.status === 'duplicate'">🔄</template>
          <template v-else-if="item.status === 'failed'">❌</template>
        </div>
        <div class="queue-card-name">{{ item.file.name.slice(0, 20) }}</div>
        <div v-if="item.status === 'failed'" class="queue-card-error" :title="item.errorMsg">
          {{ item.errorMsg?.slice(0, 30) }}
        </div>
        <a-button
          v-if="item.status === 'pending'"
          size="mini"
          type="primary"
          status="danger"
          @click="emit('remove', item.id)"
        >
          ✕
        </a-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 队列 */
.queue-section {
  margin-top: 16px;
  background: #fff;
  border-radius: 10px;
  padding: 12px;
}

.queue-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
  font-size: 14px;
  font-weight: 600;
}

.queue-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 8px;
  max-height: 360px;
  overflow-y: auto;
}

.queue-card {
  position: relative;
  aspect-ratio: 3/4;
  border-radius: 8px;
  overflow: hidden;
  background: #f5f5f5;
  border: 2px solid #e5e7eb;
}

.queue-card.done { border-color: #22c55e; }
.queue-card.failed { border-color: #ef4444; }
.queue-card.duplicate { border-color: #f59e0b; }

.queue-card img,
.queue-card video.queue-thumb {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.queue-video-badge {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  pointer-events: none;
  z-index: 1;
}

.queue-card-status {
  position: absolute;
  top: 4px;
  right: 4px;
  font-size: 16px;
}

.queue-card-name {
  position: absolute;
  bottom: 24px;
  left: 0;
  right: 0;
  font-size: 10px;
  color: #fff;
  background: rgba(0,0,0,0.6);
  padding: 2px 4px;
  text-align: center;
}

.queue-card-error {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  font-size: 10px;
  color: #fff;
  background: rgba(239,68,68,0.8);
  padding: 2px 4px;
  text-align: center;
}
</style>
