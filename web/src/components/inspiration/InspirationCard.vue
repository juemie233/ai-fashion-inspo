<script setup lang="ts">
/** 素材卡片：瀑布流中的单个卡片，显示缩略图、标签和操作按钮。 */

import { computed } from 'vue'
import {
  IconCheck,
  IconDelete,
  IconEye,
  IconHeart,
  IconHeartFill,
} from '@arco-design/web-vue/es/icon'
import { useRouter, useRoute } from 'vue-router'
import { getFileUrl, type InspirationOut } from '@/api/inspirations'
import CategoryTag from './CategoryTag.vue'
import HoverImagePreview from '@/components/common/HoverImagePreview.vue'
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
  /** 是否显示「解除绑定」按钮（人物详情页解绑素材用）：显示时点击触发 unbind 事件 */
  showUnbind?: boolean
  /** 「解除绑定」二次确认文案（如「解除与该博主的绑定？」） */
  unbindTip?: string
}>()

const emit = defineEmits<{
  (e: 'delete'): void
  (e: 'toggleFavorite'): void
  (e: 'rate', value: number): void
  (e: 'approve'): void
  (e: 'toggleSelect'): void
  (e: 'unbind'): void
}>()

const router = useRouter()
const route = useRoute()

/** 网格图片源：图片素材用原图保证清晰（缩略图仅 400x600，放大显示会发虚/有压缩感）；视频用首帧缩略图 */
const gridSrc = computed(() => {
  const { media_type: mt, file_path: fp, thumbnail_path: tp } = props.item
  if (mt === 'video') return tp || fp || ''
  return fp || tp || ''
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
  if (props.selectable) {
    emit('toggleSelect')
  } else {
    goToDetail()
  }
}

/** 浏览详情：选择模式下通过专用按钮进入详情页（阻止冒泡，不触发勾选切换） */
function openDetail() {
  goToDetail()
}
</script>

<template>
  <div class="card" @click="handleCardClick">
    <!-- 缩略图 / 视频首帧 -->
    <div class="card-image">
      <!-- 悬停放大预览：复用通用 HoverImagePreview（仅图片素材，停留 2s 弹出原图大图） -->
      <HoverImagePreview
        v-if="hoverZoom && item.media_type !== 'video'"
        :large-src="getFileUrl(item.file_path)"
        :delay="HOVER_ZOOM_DELAY"
      >
        <img :src="getFileUrl(gridSrc)" :alt="item.source_author || '穿搭素材'" loading="lazy" />
      </HoverImagePreview>
      <template v-else>
        <video
          v-if="item.media_type === 'video' && !item.thumbnail_path"
          :src="getFileUrl(item.file_path)"
          muted
          playsinline
          preload="metadata"
        />
        <img
          v-else-if="gridSrc"
          :src="getFileUrl(gridSrc)"
          :alt="item.source_author || '穿搭素材'"
          loading="lazy"
        />
      </template>

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
      <div v-if="showViewButton" class="view-detail-btn" title="浏览详情" @click.stop="openDetail">
        <IconEye :size="14" />
      </div>

      <!-- 悬浮操作层 -->
      <div v-if="showActions !== false || showUnbind" class="card-overlay">
        <!-- 解除绑定（人物详情页）：左上角文字小按钮 + 二次确认 -->
        <a-popconfirm
          v-if="showUnbind"
          :content="unbindTip || '解除绑定？'"
          :ok-button-props="{ status: 'warning' }"
          ok-text="解除"
          @ok="emit('unbind')"
        >
          <a-button size="mini" status="warning" type="primary" class="unbind-btn" @click.stop>
            解除绑定
          </a-button>
        </a-popconfirm>
        <template v-if="showActions !== false">
          <a-button
            v-if="item.quality_status === 'rejected'"
            size="mini"
            shape="circle"
            type="text"
            status="success"
            title="标记为通过（翻案）"
            @click.stop="emit('approve')"
          >
            <template #icon>
              <IconCheck :size="14" />
            </template>
          </a-button>
          <a-popconfirm
            content="移入垃圾桶？保留期内可在「素材管理 → 垃圾桶」恢复"
            :ok-button-props="{ status: 'danger' }"
            @ok="emit('delete')"
          >
            <a-button size="mini" shape="circle" type="text" status="danger" @click.stop>
              <template #icon>
                <IconDelete :size="14" />
              </template>
            </a-button>
          </a-popconfirm>
          <a-button
            size="mini"
            shape="circle"
            :type="item.is_favorite ? 'primary' : 'text'"
            :status="item.is_favorite ? 'danger' : undefined"
            @click.stop="emit('toggleFavorite')"
          >
            <template #icon>
              <IconHeartFill v-if="item.is_favorite" :size="14" />
              <IconHeart v-else :size="14" />
            </template>
          </a-button>
          <!-- 五星评分：仅整数（不允许半星），点击星设置评分，再点已选星清除（0 分） -->
          <a-rate
            :model-value="item.rating || 0"
            allow-clear
            class="card-rate"
            title="评分（点击星设置，再点清除）"
            @click.stop
            @change="(v: number) => emit('rate', v)"
          />
        </template>
      </div>

      <!-- 分析状态 -->
      <div v-if="analysisStatusLabel()" class="analysis-badge">
        {{ analysisStatusLabel() }}
      </div>

      <!-- 质量审核状态 + 疑似 AI 标记（正交，可同时显示） -->
      <div v-if="item.quality_status !== 'approved' || item.is_ai_generated" class="quality-badges">
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
        <div v-if="item.is_ai_generated" class="quality-badge ai" title="疑似 AI 生成，请人工确认">
          疑似 AI
        </div>
      </div>
    </div>

    <!-- 信息区 -->
    <div class="card-body">
      <!-- 来源（空来源不渲染，避免出现空白胶囊） -->
      <div class="card-source">
        <a-tag v-if="item.source_type" size="small" color="arcoblue">
          {{ sourceLabel(item.source_type) }}
        </a-tag>
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
</template>

<style scoped>
.card {
  background: var(--color-bg-2);
  border-radius: 12px;
  overflow: hidden;
  cursor: pointer;
  transition:
    transform 0.15s,
    box-shadow 0.15s;
  border: 1px solid var(--color-border-2);
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
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 4px;
  max-width: calc(100% - 16px);
  opacity: 0;
  transition: opacity 0.15s;
}
.card:hover .card-overlay {
  opacity: 1;
}

/* 「解除绑定」按钮：固定到左上角，与右上角常规操作区分开（人物详情页用） */
.unbind-btn {
  position: absolute;
  top: 0;
  left: 0;
  font-size: 12px;
  line-height: 1;
}

/* 评分控件：与收藏按钮并列，白色描边保证在任意图片上可辨；
   Arco rate 无 size 属性，用字体大小控制星号尺寸，并收紧星间间距保持紧凑 */
.card-rate {
  margin-left: 2px;
  padding: 2px 4px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.75);
  font-size: 16px;
  min-height: 0;
}
.card-rate :deep(.arco-rate-character:not(:last-child)) {
  margin-right: 2px;
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
</style>
