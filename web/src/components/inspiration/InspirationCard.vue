<script setup lang="ts">
/** 素材卡片：瀑布流中的单个卡片，显示缩略图、标签和操作按钮。 */

import { h, onBeforeUnmount, ref, watch, type Component } from 'vue'
import { NIcon } from 'naive-ui'
import { Heart, HeartOutline, TrashOutline, EyeOutline, CheckmarkOutline } from '@vicons/ionicons5'
import { useRouter, useRoute } from 'vue-router'
import { getFileUrl, type InspirationOut } from '@/api/inspirations'
import CategoryTag from './CategoryTag.vue'
import { sourceLabel } from '@/utils/sourceLabel'

/** 悬停放大预览的触发阈值（毫秒）：鼠标停留在素材上超过该时长才弹出放大图 */
const HOVER_ZOOM_DELAY = 2000

const props = defineProps<{
  item: InspirationOut
  /** 右上角角标文本（如相似度「92% 视觉相似」，用于相似推荐场景） */
  badge?: string
  /** 是否显示悬浮操作按钮（删除/收藏），相似推荐卡片默认关闭 */
  showActions?: boolean
  /** 批量选择模式：显示勾选框，点击卡片切换勾选而非跳转详情 */
  selectable?: boolean
  /** 批量选择模式下是否已勾选 */
  selected?: boolean
  /** 悬停放大预览：鼠标停留在素材上超过 2 秒时弹出大图（疑似 AI 页面启用） */
  hoverZoom?: boolean
  /** 是否显示「浏览详情」按钮：选择模式下点击卡片只能勾选，需单独提供入口跳转详情页 */
  showViewButton?: boolean
}>()

const emit = defineEmits<{
  (e: 'delete'): void
  (e: 'toggleFavorite'): void
  (e: 'approve'): void
  (e: 'toggleSelect'): void
}>()

const router = useRouter()
const route = useRoute()

// ── 悬停放大预览状态 ──
const zoomOpen = ref(false)
let zoomTimer: number | null = null

function clearZoomTimer() {
  if (zoomTimer !== null) {
    window.clearTimeout(zoomTimer)
    zoomTimer = null
  }
}

/** 鼠标进入卡片：仅图片素材启用，停留超过 2 秒弹出放大预览 */
function startZoomTimer() {
  if (!props.hoverZoom || props.item.media_type === 'video') return
  clearZoomTimer()
  zoomTimer = window.setTimeout(() => {
    zoomOpen.value = true
  }, HOVER_ZOOM_DELAY)
}

/** 取消放大：鼠标离开、滚动或点击卡片时关闭预览并重置计时 */
function cancelZoom() {
  clearZoomTimer()
  zoomOpen.value = false
}

// 放大预览打开期间监听滚动：页面滚动时立即关闭，避免悬浮大图遮挡浏览
watch(zoomOpen, (open) => {
  if (open) {
    window.addEventListener('scroll', cancelZoom, { passive: true })
  } else {
    window.removeEventListener('scroll', cancelZoom)
  }
})

onBeforeUnmount(() => {
  clearZoomTimer()
  window.removeEventListener('scroll', cancelZoom)
})

/** 获取首行展示的标签（最多 4 个） */
function displayTags() {
  return props.item.tags?.slice(0, 4).map((t) => t.tag) ?? []
}

/** 分析状态标签 */
function analysisStatusLabel(): string | null {
  if (props.item.analysis_status === 'analyzing') return '分析中...'
  if (props.item.analysis_status === 'error') return '分析失败'
  return null
}

function goToDetail() {
  // 携带当前筛选 query 进入详情，便于删除/返回后恢复素材库筛选状态
  router.push({ path: `/detail/${props.item.id}`, query: route.query })
}

/** 卡片点击：批量模式下切换勾选，否则跳转详情 */
function handleCardClick() {
  // 点击视为有意交互，取消悬停放大（防止点击后残留预览）
  cancelZoom()
  if (props.selectable) {
    emit('toggleSelect')
  } else {
    goToDetail()
  }
}

/** 浏览详情：选择模式下通过专用按钮进入详情页（阻止冒泡，不触发勾选切换） */
function openDetail() {
  cancelZoom()
  goToDetail()
}
</script>

<template>
  <div class="card" @click="handleCardClick" @mouseenter="startZoomTimer" @mouseleave="cancelZoom">
    <!-- 缩略图 / 视频首帧 -->
    <div class="card-image">
      <video
        v-if="item.media_type === 'video' && !item.thumbnail_path"
        :src="getFileUrl(item.file_path)"
        muted
        playsinline
        preload="metadata"
      />
      <img
        v-else
        :src="getFileUrl(item.thumbnail_path || item.file_path)"
        :alt="item.source_author || '穿搭素材'"
        loading="lazy"
      />

      <!-- 视频播放角标 -->
      <div v-if="item.media_type === 'video'" class="video-badge" title="视频">▶</div>

      <!-- 角标（相似度 / 相似来源） -->
      <div v-if="badge" class="sim-badge" :title="badge">
        {{ badge }}
      </div>

      <!-- 批量勾选框 -->
      <div
        v-if="selectable"
        class="select-checkbox"
        :class="{ checked: selected }"
        @click.stop="emit('toggleSelect')"
      >
        <span v-if="selected">✓</span>
      </div>

      <!-- 浏览详情按钮（选择模式下仍可进入详情页） -->
      <div
        v-if="showViewButton"
        class="view-detail-btn"
        title="浏览详情"
        @click.stop="openDetail"
      >
        <n-icon><EyeOutline /></n-icon>
      </div>

      <!-- 悬浮操作层 -->
      <div v-if="showActions !== false" class="card-overlay">
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
        <n-popconfirm @positive-click="emit('delete')">
          <template #trigger>
            <n-button
              size="tiny"
              circle
              quaternary
              type="error"
              @click.stop
            >
              <template #icon>
                <n-icon><TrashOutline /></n-icon>
              </template>
            </n-button>
          </template>
          移入垃圾桶？保留期内可在「素材管理 → 垃圾桶」恢复
        </n-popconfirm>
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

      <!-- 质量审核状态 + 疑似 AI 标记（正交，可同时显示） -->
      <div
        v-if="item.quality_status !== 'approved' || item.is_ai_generated"
        class="quality-badges"
      >
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
        <div
          v-if="item.is_ai_generated"
          class="quality-badge ai"
          title="疑似 AI 生成，请人工确认"
        >
          疑似 AI
        </div>
      </div>
    </div>

    <!-- 信息区 -->
    <div class="card-body">
      <!-- 来源（空来源不渲染，避免出现空白胶囊） -->
      <div class="card-source">
        <n-tag v-if="item.source_type" size="tiny" :bordered="false" type="info">
          {{ sourceLabel(item.source_type) }}
        </n-tag>
        <span v-if="item.source_author" class="author">@{{ item.source_author }}</span>
      </div>

      <!-- 标签 -->
      <div v-if="displayTags().length > 0" class="card-tags">
        <CategoryTag
          v-for="tag in displayTags()"
          :key="tag.id"
          :category="tag.category"
          size="tiny"
        >
          {{ tag.name }}
        </CategoryTag>
      </div>
    </div>
  </div>

  <!-- 悬停放大预览：鼠标停留在素材上超过 2 秒时弹出原图大图（仅图片素材）。
       teleport 到 body，避免被卡片的 overflow:hidden / hover transform 裁剪。 -->
  <Teleport to="body">
    <div v-if="zoomOpen" class="hover-zoom-panel" title="点击关闭" @click="cancelZoom">
      <img :src="getFileUrl(item.file_path)" alt="素材放大预览" />
    </div>
  </Teleport>
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
.card-image img,
.card-image video {
  width: 100%;
  display: block;
  object-fit: cover;
}
.card-image video {
  aspect-ratio: 3 / 4;
  background: #000;
}

.video-badge {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  pointer-events: none;
  z-index: 1;
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

.sim-badge {
  position: absolute;
  bottom: 8px;
  right: 8px;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 4px;
  z-index: 2;
  white-space: nowrap;
}

.select-checkbox {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.9);
  border: 2px solid #ccc;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  line-height: 1;
  color: #fff;
  cursor: pointer;
  z-index: 3;
  transition: all 0.15s;
}
.select-checkbox.checked {
  background: #e0465e;
  border-color: #e0465e;
}

/* 浏览详情按钮：悬停卡片时出现，位于缩略图右下角 */
.view-detail-btn {
  position: absolute;
  bottom: 8px;
  right: 8px;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  cursor: pointer;
  z-index: 3;
  opacity: 0;
  transition: opacity 0.15s;
}
.card:hover .view-detail-btn {
  opacity: 1;
}
.view-detail-btn:hover {
  background: rgba(0, 0, 0, 0.75);
}

.quality-badges {
  position: absolute;
  top: 8px;
  left: 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  z-index: 2;
}
.quality-badge {
  width: fit-content;
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
.quality-badge.ai {
  background: rgba(122, 80, 200, 0.9);
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

/* 悬停放大预览（teleport 到 body，fixed 定位不受卡片裁剪影响） */
.hover-zoom-panel {
  position: fixed;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  max-width: 90vw;
  max-height: 88vh;
  z-index: 3000;
  border-radius: 12px;
  overflow: hidden;
  background: #fff;
  box-shadow: 0 12px 48px rgba(0, 0, 0, 0.35);
  cursor: zoom-out;
  animation: hover-zoom-in 0.18s ease;
}
.hover-zoom-panel img {
  display: block;
  max-width: 90vw;
  max-height: 88vh;
  object-fit: contain;
}
@keyframes hover-zoom-in {
  from {
    opacity: 0;
    transform: translate(-50%, -46%) scale(0.96);
  }
  to {
    opacity: 1;
    transform: translate(-50%, -50%) scale(1);
  }
}
</style>
