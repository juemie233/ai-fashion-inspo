<script setup lang="ts">
/** 合集内的「编辑排序」条带：缩略图可拖拽，顺序由父组件的排序 composable 维护。
 *
 * 从 CollectionsView.vue 抽出（视图只做编排）。组件本身不持有顺序状态：
 * dragstart 记下起点、drop 时把目标 ID 回抛，重排与保存都在
 * useCollectionItemOrdering 里。
 */

import { getFileUrl } from '@/api/inspirations'
import type { InspirationOut } from '@/api/inspirations'

defineProps<{
  /** 待编排的素材（顺序即父组件维护的本地顺序） */
  items: InspirationOut[]
}>()

const emit = defineEmits<{
  (e: 'drag-start', id: string): void
  (e: 'drop', id: string): void
}>()
</script>

<template>
  <div class="ordering-strip">
    <p class="ordering-tip">拖动缩略图调整展示顺序，完成后点击「保存顺序」。</p>
    <div class="ordering-grid">
      <div
        v-for="it in items"
        :key="it.id"
        class="ordering-card"
        draggable="true"
        @dragstart="emit('drag-start', it.id)"
        @dragover.prevent
        @drop.prevent="emit('drop', it.id)"
      >
        <img :src="getFileUrl(it.thumbnail_path ?? it.file_path)" alt="" />
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 排序模式 */
.ordering-tip {
  font-size: 12px;
  color: #86909c;
  margin: 0 0 8px;
}
.ordering-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 12px;
  background: #f8f9fa;
  border-radius: 8px;
}
.ordering-card {
  width: 96px;
  height: 130px;
  border-radius: 6px;
  overflow: hidden;
  cursor: grab;
  border: 2px solid transparent;
  background: #f2f3f5;
}
.ordering-card:active {
  cursor: grabbing;
}
.ordering-card:hover {
  border-color: #94bfff;
}
.ordering-card img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  pointer-events: none;
}
</style>
