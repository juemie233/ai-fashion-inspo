<script setup lang="ts">
/** 素材卡片：瀑布流中的单个卡片，显示缩略图、标签和操作按钮。 */

import { h, type Component } from 'vue'
import { NIcon } from 'naive-ui'
import { Heart, HeartOutline, TrashOutline, EyeOutline, CheckmarkOutline } from '@vicons/ionicons5'
import { useRouter } from 'vue-router'
import { getFileUrl, type InspirationOut } from '@/api/inspirations'

const props = defineProps<{
  item: InspirationOut
}>()

const emit = defineEmits<{
  (e: 'delete'): void
  (e: 'toggleFavorite'): void
  (e: 'approve'): void
}>()

const router = useRouter()

/** 获取首行展示的标签（最多 4 个） */
function displayTags() {
  return props.item.tags?.slice(0, 4).map((t) => t.tag) ?? []
}

/** 获取标签颜色（按类别） */
function tagColor(category: string): string {
  const colors: Record<string, string> = {
    style: '#8b5cf6',
    item_type: '#3b82f6',
    color: '#f59e0b',
    body_part: '#10b981',
    fit: '#ec4899',
    occasion: '#06b6d4',
    attribute: '#6b7280',
    free: '#9ca3af',
    outfit: '#e11d48',
  }
  return colors[category] || '#9ca3af'
}

/** 来源图标和标签 */
function sourceLabel(type: string): string {
  const labels: Record<string, string> = {
    xiaohongshu: '小红书',
    douyin: '抖音',
    scraper: '自动采集',
    manual_upload: '手动上传',
    browser_extension: '浏览器插件',
  }
  return labels[type] || type
}

/** 分析状态标签 */
function analysisStatusLabel(): string | null {
  if (props.item.analysis_status === 'analyzing') return '分析中...'
  if (props.item.analysis_status === 'error') return '分析失败'
  return null
}

function goToDetail() {
  router.push(`/detail/${props.item.id}`)
}
</script>

<template>
  <div class="card" @click="goToDetail">
    <!-- 缩略图 -->
    <div class="card-image">
      <img
        v-if="item.thumbnail_path"
        :src="getFileUrl(item.thumbnail_path)"
        :alt="item.source_author || '穿搭素材'"
        loading="lazy"
      />
      <img
        v-else
        :src="getFileUrl(item.file_path)"
        :alt="item.source_author || '穿搭素材'"
        loading="lazy"
      />

      <!-- 悬浮操作层 -->
      <div class="card-overlay">
        <n-button
          v-if="item.quality_status === 'rejected'"
          size="tiny"
          circle
          quaternary
          type="success"
          title="标记为通过（翻案）"
          @click.stop="emit('approve')"
        >
          <template #icon>
            <n-icon><CheckmarkOutline /></n-icon>
          </template>
        </n-button>
        <n-button
          size="tiny"
          circle
          quaternary
          type="error"
          @click.stop="emit('delete')"
        >
          <template #icon>
            <n-icon><TrashOutline /></n-icon>
          </template>
        </n-button>
        <n-button
          size="tiny"
          circle
          :quaternary="!item.is_favorite"
          :type="item.is_favorite ? 'error' : 'default'"
          @click.stop="emit('toggleFavorite')"
        >
          <template #icon>
            <n-icon>
              <Heart v-if="item.is_favorite" />
              <HeartOutline v-else />
            </n-icon>
          </template>
        </n-button>
      </div>

      <!-- 分析状态 -->
      <div v-if="analysisStatusLabel()" class="analysis-badge">
        {{ analysisStatusLabel() }}
      </div>

      <!-- 质量审核状态 -->
      <div
        v-if="item.quality_status === 'rejected'"
        class="quality-badge rejected"
        :title="item.quality_reason || 'AI 判定为非穿搭内容'"
      >
        已拒绝
      </div>
      <div v-else-if="item.quality_status === 'pending'" class="quality-badge pending">
        待审核
      </div>
    </div>

    <!-- 信息区 -->
    <div class="card-body">
      <!-- 来源 -->
      <div class="card-source">
        <n-tag size="tiny" :bordered="false" type="info">
          {{ sourceLabel(item.source_type) }}
        </n-tag>
        <span v-if="item.source_author" class="author">@{{ item.source_author }}</span>
      </div>

      <!-- 标签 -->
      <div v-if="displayTags().length > 0" class="card-tags">
        <n-tag
          v-for="tag in displayTags()"
          :key="tag.id"
          size="tiny"
          :bordered="false"
          :style="{ backgroundColor: tagColor(tag.category) + '20', color: tagColor(tag.category) }"
        >
          {{ tag.name }}
        </n-tag>
      </div>
    </div>
  </div>
</template>

<style scoped>
.card {
  background: var(--n-color);
  border-radius: 12px;
  overflow: hidden;
  cursor: pointer;
  transition: transform 0.15s, box-shadow 0.15s;
  border: 1px solid var(--n-border-color);
}
.card:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1);
}

.card-image {
  position: relative;
  width: 100%;
  background: #f5f5f5;
}
.card-image img {
  width: 100%;
  display: block;
  object-fit: cover;
}

.card-overlay {
  position: absolute;
  top: 8px;
  right: 8px;
  display: flex;
  gap: 4px;
  opacity: 0;
  transition: opacity 0.15s;
}
.card:hover .card-overlay {
  opacity: 1;
}

.analysis-badge {
  position: absolute;
  bottom: 8px;
  left: 8px;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 4px;
}

.quality-badge {
  position: absolute;
  top: 8px;
  left: 8px;
  color: #fff;
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 4px;
}
.quality-badge.rejected {
  background: rgba(208, 48, 80, 0.85);
}
.quality-badge.pending {
  background: rgba(160, 160, 160, 0.8);
}

.card-body {
  padding: 10px 12px;
}

.card-source {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
}
.author {
  font-size: 12px;
  color: #999;
}

.card-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
</style>
