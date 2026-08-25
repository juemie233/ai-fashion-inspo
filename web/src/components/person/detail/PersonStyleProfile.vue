<script setup lang="ts">
/** 风格画像卡：高频标签 / 类别分布 / 素材趋势（基于该人物素材标签聚合）。 */

import type { PersonStyleProfile as StyleProfile } from '@shared/types/person'

defineProps<{
  profile: StyleProfile
}>()

defineEmits<{
  'search-tag': [name: string]
}>()
</script>

<template>
  <a-card size="small" class="profile-card" title="风格画像（基于该人物素材标签聚合）">
    <div class="profile-grid">
      <!-- 高频标签 -->
      <div class="profile-block">
        <h4>高频标签</h4>
        <div class="tag-chips">
          <template v-if="profile.top_tags.length">
            <span
              v-for="t in profile.top_tags"
              :key="t.tag_id"
              class="tag-chip"
              @click="$emit('search-tag', t.name)"
            >
              {{ t.name }}
              <span class="tag-count">{{ t.count }}</span>
            </span>
          </template>
          <a-empty v-else description="暂无标签数据" size="small" />
        </div>
      </div>

      <!-- 类别分布 -->
      <div class="profile-block">
        <h4>类别分布</h4>
        <div class="cat-list">
          <template v-if="Object.keys(profile.by_category).length">
            <div v-for="(count, cat) in profile.by_category" :key="cat" class="cat-row">
              <span class="cat-name">{{ cat }}</span>
              <span class="cat-bar"
                ><span class="cat-fill" :style="{ width: Math.min(100, count * 8) + '%' }"
              /></span>
              <span class="cat-count">{{ count }}</span>
            </div>
          </template>
          <a-empty v-else description="暂无数据" size="small" />
        </div>
      </div>

      <!-- 趋势 -->
      <div class="profile-block">
        <h4>素材趋势（按月）</h4>
        <div class="trend-list">
          <template v-if="profile.trend.length">
            <div v-for="t in profile.trend.slice(-12)" :key="t.bucket" class="trend-row">
              <span class="trend-bucket">{{ t.bucket }}</span>
              <span class="trend-bar"
                ><span class="trend-fill" :style="{ width: Math.min(100, t.count * 12) + '%' }"
              /></span>
              <span class="trend-count">{{ t.count }}</span>
            </div>
          </template>
          <a-empty v-else description="暂无趋势" size="small" />
        </div>
      </div>
    </div>
  </a-card>
</template>

<style scoped>
.profile-card {
  margin-bottom: 12px;
}

.profile-grid {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 20px;
}

.profile-block h4 {
  margin: 0 0 10px;
  font-size: 14px;
  color: #4b5563;
}

.tag-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.tag-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  border-radius: 999px;
  background: #eef4ff;
  color: #2f5bd0;
  font-size: 13px;
  cursor: pointer;
  transition: background 0.15s;
}

.tag-chip:hover {
  background: #dce8ff;
}

.tag-count {
  font-size: 11px;
  color: #8aa1c8;
}

.cat-list,
.trend-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.cat-row,
.trend-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.cat-name,
.trend-bucket {
  width: 72px;
  flex-shrink: 0;
  color: #4b5563;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cat-bar,
.trend-bar {
  flex: 1;
  height: 8px;
  background: #eef1f6;
  border-radius: 4px;
  overflow: hidden;
}

.cat-fill {
  display: block;
  height: 100%;
  background: #7ba7f0;
  border-radius: 4px;
}

.trend-fill {
  display: block;
  height: 100%;
  background: #9aa7f0;
  border-radius: 4px;
}

.cat-count,
.trend-count {
  width: 28px;
  text-align: right;
  color: #6b7280;
  font-size: 12px;
}

@media (max-width: 900px) {
  .profile-grid {
    grid-template-columns: 1fr;
  }
}
</style>
