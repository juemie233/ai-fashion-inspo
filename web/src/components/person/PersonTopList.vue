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
        <!-- image-url 传头像：Arco 只在 imageUrl 分支加 arco-avatar-image，
             圆形裁剪才生效（插槽 img 会渲染成方形并溢出） -->
        <a-avatar :size="28" :image-url="p.avatar_path ? getFileUrl(p.avatar_path) : undefined">
          <span v-if="!p.avatar_path" aria-hidden="true">👤</span>
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

/* ── 印样台：热门人物是选角名单，名次是真实序列（用印刷数字，不做圆形徽章）── */
.top-card {
  margin-top: 16px;
}

.top-row {
  padding: 9px 4px;
  border-radius: 0;
  border-bottom: 1px solid var(--line);
}

.top-row:last-child {
  border-bottom: 0;
}

.top-row:hover {
  background: var(--paper);
}

.top-rank {
  width: 28px;
  height: auto;
  border-radius: 0;
  background: transparent;
  color: var(--ink-3);
  font-family: var(--font-numeric);
  font-variant-numeric: tabular-nums lining-nums;
  font-size: 15px;
  font-weight: 400;
}

.top-row:first-child .top-rank,
.top-row:nth-child(2) .top-rank,
.top-row:nth-child(3) .top-rank {
  color: var(--seal);
}

.top-name {
  font-weight: 500;
  color: var(--ink);
}
</style>
