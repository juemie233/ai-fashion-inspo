<script setup lang="ts">
/** 照片组卡片（模特写真，仅职业模特展示）：封面网格 + 浏览/删除，与穿搭素材分离。 */

import { getFileUrl } from '@/api/inspirations'
import type { ModelPhotoSet } from '@/api/persons'

defineProps<{
  photoSets: ModelPhotoSet[]
  loading: boolean
}>()

defineEmits<{
  open: [set: ModelPhotoSet]
  delete: [set: ModelPhotoSet]
  add: []
}>()
</script>

<template>
  <a-card size="small" class="photo-sets-card">
    <div class="items-header">
      <h3 style="margin: 0">照片组（模特写真）</h3>
      <a-button size="small" type="secondary" @click="$emit('add')"> ＋ 添加照片 </a-button>
    </div>

    <div v-if="photoSets.length > 0" class="photo-sets-grid">
      <div v-for="set in photoSets" :key="set.id" class="photo-set-card">
        <div class="photo-set-cover" @click="$emit('open', set)">
          <img v-if="set.cover_path" :src="getFileUrl(set.cover_path)" :alt="set.name" />
          <span v-else class="cover-fallback">🖼️</span>
          <div class="photo-set-count">{{ set.photo_count }} 张</div>
        </div>
        <div class="photo-set-meta">
          <span class="photo-set-name" :title="set.name">{{ set.name }}</span>
          <a-space :size="4">
            <a-button size="mini" type="text" @click="$emit('open', set)">浏览</a-button>
            <a-popconfirm
              :content="`确定删除照片组「${set.name}」？组内照片将一并删除。`"
              @ok="$emit('delete', set)"
            >
              <a-button size="mini" type="text" status="danger">删除</a-button>
            </a-popconfirm>
          </a-space>
        </div>
      </div>
    </div>

    <a-empty
      v-else-if="!loading"
      description="暂无照片组，点击右上角「添加照片」从文件夹导入"
      size="small"
      style="margin: 24px 0"
    />
    <a-spin v-if="loading" :loading="true" style="margin: 24px 0" />
  </a-card>
</template>

<style scoped>
.photo-sets-card {
  margin-bottom: 12px;
}

.items-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.photo-sets-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 12px;
}

.photo-set-card {
  border: 1px solid #eef1f6;
  border-radius: 8px;
  overflow: hidden;
  background: #fff;
}

.photo-set-cover {
  position: relative;
  aspect-ratio: 3 / 4;
  cursor: pointer;
  background: #f3f5f9;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}

.photo-set-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.cover-fallback {
  font-size: 36px;
}

.photo-set-count {
  position: absolute;
  right: 6px;
  bottom: 6px;
  padding: 0 6px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  font-size: 11px;
}

.photo-set-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 4px;
  padding: 8px;
}

.photo-set-name {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
