<script setup lang="ts">
/** 风格相近 + 疑似同名（B5）：把「还该关注谁」和「是不是重复登记了」放在一起。
 *
 * - 相近：按标签集合余弦（共同标签 ≥3 才算，1~2 个共同标签基本都是「白色」这种高频词巧合）；
 * - 同名：归一化昵称相同 → 可能就是同一个人登记了两次，给一键合并（建人物组）。
 */

import { getFileUrl } from '@/api/inspirations'
import type { PersonSimilarItem } from '@/api/persons'

defineProps<{
  similar: PersonSimilarItem[]
  /** 疑似重复分组（只含「包含当前人物」的组） */
  duplicates: Array<{
    key: string
    items: Array<{ id: number; name: string; inspiration_count?: number }>
  }>
  loading: boolean
  currentId: number
}>()

const emit = defineEmits<{
  (e: 'open-person', id: number): void
  (e: 'merge-duplicate', ids: number[], name: string): void
}>()

function avatar(path: string | null | undefined): string {
  return path ? getFileUrl(path) : ''
}

/** 合并时把「当前人物」也算上：一个人物组里至少要有两个账号才有意义 */
function mergeIds(group: { items: Array<{ id: number }> }, currentId: number): number[] {
  return [...new Set([currentId, ...group.items.map((i) => i.id)])]
}
</script>

<template>
  <a-card size="small" class="similar-card" :loading="loading">
    <template #title>风格相近 / 疑似同名</template>

    <div class="similar-block">
      <div class="block-title">标签相似的博主</div>
      <div v-if="!similar.length" class="hint">暂无相似博主（需要至少 3 个共同标签才会推荐）</div>
      <div v-else class="similar-list">
        <div
          v-for="item in similar"
          :key="item.id"
          class="similar-item"
          :title="`共同标签 ${item.shared_tags} 个 · 相似度 ${item.similarity}`"
          @click="emit('open-person', item.id)"
        >
          <img v-if="item.avatar_path" :src="avatar(item.avatar_path)" alt="" />
          <span v-else class="avatar-fallback">{{ item.name.slice(0, 1) }}</span>
          <div class="similar-meta">
            <div class="similar-name">{{ item.name }}</div>
            <div class="hint">
              共同标签 {{ item.shared_tags }} · {{ item.inspiration_count }} 条素材
            </div>
          </div>
          <a-tag size="small">{{ Math.round(item.similarity * 100) }}%</a-tag>
        </div>
      </div>
    </div>

    <div v-if="duplicates.length" class="duplicate-block">
      <div class="block-title">疑似同名账号（可能就是同一个人）</div>
      <div v-for="group in duplicates" :key="group.key" class="duplicate-group">
        <span class="hint">
          {{ group.items.map((i) => `${i.name}（${i.inspiration_count ?? 0} 条）`).join(' / ') }}
        </span>
        <a-button
          size="mini"
          type="text"
          @click="emit('merge-duplicate', mergeIds(group, currentId), group.key)"
        >
          合并为同一人
        </a-button>
      </div>
    </div>
  </a-card>
</template>

<style scoped>
.similar-card {
  margin-bottom: 16px;
}
.block-title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
}
.hint {
  color: var(--color-text-3);
  font-size: 12px;
}
.similar-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 8px;
}
.similar-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border: 1px solid var(--color-border-2);
  border-radius: 6px;
  cursor: pointer;
}
.similar-item:hover {
  border-color: rgb(var(--primary-5));
}
.similar-item img,
.avatar-fallback {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  object-fit: cover;
  flex: 0 0 auto;
}
.avatar-fallback {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--color-fill-3);
  font-size: 14px;
}
.similar-meta {
  flex: 1;
  min-width: 0;
}
.similar-name {
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.duplicate-block {
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px dashed var(--color-border-2);
}
.duplicate-group {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 4px 0;
}
</style>
