<script setup lang="ts">
/** 热门人物排行卡（按素材数）：点击行跳转详情。 */

import { getFileUrl } from '@/api/inspirations'
import type { Person } from '@shared/types/person'

defineProps<{
  persons: Person[]
}>()

defineEmits<{
  'go-detail': [person: Person]
}>()
</script>

<template>
  <a-card v-if="persons.length > 0" size="small" class="top-card" title="热门人物（按素材数）">
    <a-space direction="vertical" :size="8">
      <div v-for="(p, i) in persons" :key="p.id" class="top-row" @click="$emit('go-detail', p)">
        <span class="top-rank">{{ i + 1 }}</span>
        <a-avatar :size="28">
          <img
            v-if="p.face_thumb_path || p.avatar_path"
            :src="getFileUrl(p.face_thumb_path || (p.avatar_path as string))"
            :alt="p.name"
          />
          <span v-else aria-hidden="true">👤</span>
        </a-avatar>
        <span class="top-name">{{ p.name }}</span>
        <span style="color: #999; font-size: 12px">{{ p.inspiration_count ?? 0 }} 素材</span>
      </div>
    </a-space>
  </a-card>
</template>

<style scoped>
.top-card {
  margin-top: 12px;
}

.top-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s;
}

.top-row:hover {
  background: #f5f7fa;
}

.top-rank {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: #e8ecf2;
  color: #3b4a63;
  font-size: 12px;
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.top-name {
  font-weight: 500;
}
</style>
