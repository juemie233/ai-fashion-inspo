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
import StatusTag from '@/components/common/StatusTag.vue'
import { sourceLabel } from '@/utils/sourceLabel'
import { formatDateTime } from '@/utils/format'
import { openInspiration } from '@/utils/openInspiration'

/** 悬停放大预览的触发阈值（毫秒）：鼠标停留在素材上超过该时长才弹出放大图 */
const HOVER_ZOOM_DELAY = 2000

const props = withDefaults(
  defineProps<{
    item: InspirationOut
    /** 右上角角标文本（如相似度「92% 视觉相似」，用于相似推荐场景） */
    badge?: string
    /** 是否显示悬浮操作按钮（删除/收藏），相似推荐卡片默认关闭 */
    showActions?: boolean
    /** 是否显示收藏按钮：与 showActions 独立，人物/合集详情可只开收藏 */
    showFavorite?: boolean
    /** 是否显示删除按钮：与 showActions 独立，人物详情可按张移入垃圾桶 */
    showDelete?: boolean
    /** 删除前是否在卡片内用轻量 popconfirm 确认；false 时只 emit，由调用方弹公共原因框 */
    deleteConfirm?: boolean
    /** 收藏请求进行中：禁用按钮防快速连点 */
    favoritePending?: boolean
    /** 是否显示五星评分；与 showActions 独立，人物详情「全部」视图只开评分 */
    showRating?: boolean
    /** 批量选择模式：显示勾选框，点击卡片切换勾选而非跳转详情 */
    selectable?: boolean
    /** 批量选择模式下是否已勾选 */
    selected?: boolean
    /** 悬停放大预览：鼠标停留在素材上超过 2 秒时弹出大图（疑似 AI 页面启用） */
    hoverZoom?: boolean
    /** 是否显示「浏览详情」按钮：选择模式下点击卡片只能勾选，需单独提供入口跳转详情页 */
    showViewButton?: boolean
  }>(),
  {
    badge: '',
    showActions: false,
    showFavorite: false,
    showDelete: false,
    deleteConfirm: true,
    favoritePending: false,
    showRating: true,
    selectable: false,
    selected: false,
    hoverZoom: false,
    showViewButton: false,
  },
)

const emit = defineEmits<{
  (e: 'delete'): void
  (e: 'toggleFavorite'): void
  (e: 'rate', value: number): void
  (e: 'approve'): void
  (e: 'toggleSelect'): void
}>()

const router = useRouter()
const route = useRoute()

/** 网格图片源：优先缩略图（约 400×600 / 几 KB），避免瀑布流为每张卡片解码数 MB 原图；
 *  悬停放大与详情页仍用原图。视频仍用首帧缩略图。 */
const gridSrc = computed(() => {
  const { media_type: mt, file_path: fp, thumbnail_path: tp } = props.item
  if (mt === 'video') return tp || fp || ''
  return tp || fp || ''
})

/** 获取首行展示的标签（最多 4 个；computed 避免模板重复创建数组） */
const displayTags = computed(() => props.item.tags?.slice(0, 4).map((t) => t.tag) ?? [])

/** 说明区是否有可见内容：AI 生成素材的来源标签已由顶部徽标承担，
 *  若再没有作者/标签，说明区整块不渲染，样张直接收边（接触印样的留白更干净）。 */
const hasCaption = computed(
  () =>
    Boolean(props.item.source_type && !props.item.is_ai_generated) ||
    Boolean(props.item.source_author) ||
    displayTags.value.length > 0 ||
    // 收藏时间也是卡片说明的一部分（收藏中心用来交代「什么时候收藏的」）
    Boolean(props.item.favorited_at),
)

/** 分析状态标签（computed：模板里多处使用不再重复计算） */
const analysisStatusText = computed<string | null>(() => {
  if (props.item.analysis_status === 'analyzing') return '分析中...'
  if (props.item.analysis_status === 'error') return '分析失败'
  return null
})

/** 卡片主操作的真实链接：支持 Ctrl/⌘/中键在新标签页打开，也保留路由偏好 */
const detailHref = computed(() => {
  // 测试/极简 router 桩可能只有 push，甚至没有 router：退回可聚焦的占位链接
  if (!router || typeof router.resolve !== 'function') return '#'
  return router.resolve({
    name: 'detail',
    params: { id: props.item.id },
    query: route?.query ?? {},
  }).href
})

function goToDetail() {
  if (!router) return
  // 携带当前筛选 query 进入详情，便于删除/返回后恢复素材库筛选状态；
  // 新标签页 / 当前页由全局「素材打开模式」偏好决定（openInspiration 统一处理）
  openInspiration(router, props.item.id, route?.query ?? {})
}

/** 卡片点击：批量模式下切换勾选，否则跳转详情 */
function handleCardClick() {
  if (props.selectable) {
    emit('toggleSelect')
  } else {
    goToDetail()
  }
}

/** 读屏标签：区分「打开素材」与「勾选素材」两种语义 */
const cardAriaLabel = computed(() => {
  const kind = props.item.media_type === 'video' ? '视频' : '图片'
  const author = props.item.source_author ? `，作者 ${props.item.source_author}` : ''
  return props.selectable ? `选择${kind}素材${author}` : `打开${kind}素材${author}`
})

/** 浏览详情：选择模式下通过专用按钮进入详情页（阻止冒泡，不触发勾选切换） */
function openDetail() {
  goToDetail()
}
</script>

<template>
  <div
    class="card"
    :class="{
      'is-favorite': item.is_favorite,
      'is-selected': selectable && selected,
      'is-rejected': item.quality_status === 'rejected',
    }"
    @click="handleCardClick"
  >
    <!-- 主操作铺满整卡：真实 <a> 支持 Ctrl/⌘/中键新标签页；操作按钮是它的同级兄弟 -->
    <a
      class="card-hit"
      :href="detailHref"
      :role="selectable ? 'checkbox' : undefined"
      :aria-checked="selectable ? selected : undefined"
      :aria-label="cardAriaLabel"
      @click.prevent="handleCardClick"
      @keydown.enter.prevent="handleCardClick"
      @keydown.space.prevent="selectable && handleCardClick()"
    />

    <!-- 缩略图 / 视频首帧 -->
    <div class="card-image">
      <!-- 悬停放大预览：复用通用 HoverImagePreview（仅图片素材，停留 2s 弹出原图大图） -->
      <HoverImagePreview
        v-if="hoverZoom && item.media_type !== 'video'"
        :large-src="getFileUrl(item.file_path)"
        :delay="HOVER_ZOOM_DELAY"
      >
        <img :src="getFileUrl(gridSrc)" alt="" loading="lazy" decoding="async" />
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
          alt=""
          loading="lazy"
          decoding="async"
        />
      </template>

      <!-- 留版标记：收藏 / 批量选中的样张四角出现朱砂裁切标记（状态即标记，不做装饰） -->
      <div
        v-if="item.is_favorite || (selectable && selected)"
        class="crop-marks"
        aria-hidden="true"
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
        aria-hidden="true"
        @click.stop="emit('toggleSelect')"
      >
        <span v-if="selected">✓</span>
      </div>

      <!-- 浏览详情按钮（选择模式下仍可进入详情页） -->
      <div
        v-if="showViewButton"
        class="view-detail-btn"
        role="button"
        tabindex="0"
        title="浏览详情"
        aria-label="浏览详情"
        @click.stop="openDetail"
        @keydown.enter.stop.prevent="openDetail"
        @keydown.space.stop.prevent="openDetail"
      >
        <IconEye :size="14" />
      </div>

      <!-- 悬浮操作层：showActions=false 时可只保留评分/收藏/删除（人物/合集详情） -->
      <div v-if="showActions || showRating || showFavorite || showDelete" class="card-overlay">
        <template v-if="showActions || showDelete">
          <a-button
            v-if="showActions && item.quality_status === 'rejected'"
            size="mini"
            shape="circle"
            type="text"
            status="success"
            title="标记为通过（翻案）"
            aria-label="标记为通过"
            @click.stop="emit('approve')"
          >
            <template #icon>
              <IconCheck :size="14" />
            </template>
          </a-button>
          <a-popconfirm
            v-if="deleteConfirm && !item.deleted_at"
            content="移入垃圾桶？保留期内可在「素材管理 → 垃圾桶」恢复"
            :ok-button-props="{ status: 'danger' }"
            @ok="emit('delete')"
          >
            <a-button
              class="card-delete"
              size="mini"
              shape="circle"
              type="text"
              status="danger"
              title="移入垃圾桶"
              aria-label="移入垃圾桶"
              @click.stop
            >
              <template #icon>
                <IconDelete :size="14" />
              </template>
            </a-button>
          </a-popconfirm>
          <a-button
            v-else-if="!item.deleted_at"
            class="card-delete"
            size="mini"
            shape="circle"
            type="text"
            status="danger"
            title="移入垃圾桶"
            aria-label="移入垃圾桶"
            @click.stop="emit('delete')"
          >
            <template #icon>
              <IconDelete :size="14" />
            </template>
          </a-button>
        </template>
        <a-button
          v-if="(showActions || showFavorite) && !item.deleted_at"
          size="mini"
          shape="circle"
          :type="item.is_favorite ? 'primary' : 'text'"
          :status="item.is_favorite ? 'danger' : undefined"
          :disabled="favoritePending"
          :loading="favoritePending"
          :title="item.is_favorite ? '取消收藏' : '收藏'"
          :aria-label="item.is_favorite ? '取消收藏' : '收藏'"
          @click.stop="emit('toggleFavorite')"
        >
          <template #icon>
            <IconHeartFill v-if="item.is_favorite" :size="14" />
            <IconHeart v-else :size="14" />
          </template>
        </a-button>
        <!-- 五星评分：仅整数（不允许半星），点击星设置评分，再点已选星清除（0 分） -->
        <a-rate
          v-if="showRating && !item.deleted_at"
          :model-value="item.rating || 0"
          :aria-label="`评分，当前 ${item.rating || 0} 星，点击修改`"
          allow-clear
          class="card-rate"
          title="评分（点击星设置，再点清除）"
          @click.stop
          @change="(v: number) => emit('rate', v)"
        />
      </div>

      <!-- 分析状态 -->
      <div v-if="analysisStatusText" class="analysis-badge">
        {{ analysisStatusText }}
      </div>

      <!-- 质量审核状态 + 疑似 AI 标记（正交，可同时显示；文案/颜色统一走 StatusTag） -->
      <div v-if="item.quality_status !== 'approved' || item.is_ai_generated" class="quality-badges">
        <StatusTag
          v-if="item.quality_status === 'rejected'"
          kind="quality"
          :status="item.quality_status"
          variant="badge"
          class="quality-badge rejected"
          :title="item.quality_reason || undefined"
        />
        <StatusTag
          v-else-if="item.quality_status === 'pending'"
          kind="quality"
          :status="item.quality_status"
          variant="badge"
          class="quality-badge pending"
        />
        <!-- 本地出图是**确定**的 AI 生成，不叫「疑似」；两者共用 is_ai_generated 这一个标记 -->
        <StatusTag
          v-if="item.is_ai_generated"
          kind="ai"
          status="ai"
          :source-type="item.source_type"
          variant="badge"
          class="quality-badge ai"
        />
      </div>
    </div>

    <!-- 信息区 -->
    <div v-if="hasCaption" class="card-body">
      <!-- 来源（空来源不渲染，避免出现空白胶囊） -->
      <div class="card-source">
        <a-tag v-if="item.source_type && !item.is_ai_generated" size="small" color="arcoblue">
          {{ sourceLabel(item.source_type) }}
        </a-tag>
        <span v-if="item.source_author" class="author">@{{ item.source_author }}</span>
      </div>

      <!-- 标签 -->
      <div v-if="displayTags.length > 0" class="card-tags">
        <CategoryTag v-for="tag in displayTags" :key="tag.id" :category="tag.category" size="tiny">
          {{ tag.name }}
        </CategoryTag>
      </div>

      <!-- 收藏时间：收藏中心/收藏合集里交代「什么时候收藏的」。
           必须同时判断 is_favorite：取消收藏后 favorited_at 可能还停留在旧值，
           只看时间会显示「已取消收藏但还写着收藏于 …」 -->
      <div v-if="item.is_favorite && item.favorited_at" class="favorited-at">
        收藏于 {{ formatDateTime(item.favorited_at) }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.card {
  background: var(--color-bg-2);
  border-radius: var(--radius-md);
  overflow: hidden;
  cursor: pointer;
  transition:
    transform 0.15s,
    box-shadow 0.15s;
  border: 1px solid var(--color-border-2);
}

.card-image {
  position: relative;
  width: 100%;
  background: var(--sheet-2);
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

/* 评分控件：与收藏按钮并列，白色描边保证在任意图片上可辨；
   Arco rate 无 size 属性，用字体大小控制星号尺寸，并收紧星间间距保持紧凑 */
.card-rate {
  margin-left: 2px;
  padding: 2px 4px;
  border-radius: var(--radius-sm);
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
  border-radius: var(--radius-sm);
}

.sim-badge {
  position: absolute;
  bottom: 8px;
  right: 8px;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  font-size: 11px;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
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
  background: var(--seal);
  border-color: var(--seal);
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

/* 触摸端（iPad / 手机）：没有 hover，收藏/删除/评分等操作常显，
   否则这些按钮在触摸设备上永远不出现 —— 网格里等于无法操作素材 */
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
  color: var(--ink-3);
}

.card-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

/* ── 印样台外观覆盖（第二 style 块：只覆盖视觉，不改原结构/交互）── */
.card {
  position: relative;
  background: var(--sheet);
  border: 1px solid var(--line);
  border-radius: 0;
  overflow: hidden;
  transition: border-color 0.12s ease;
}

/* 悬停只加深墨线，不做上浮/发光（反模板：不是每张卡片都悬浮） */
@media (hover: hover) {
  .card:hover {
    transform: none;
    box-shadow: none;
    border-color: var(--ink-3);
  }
}

.card:focus-visible {
  outline: 2px solid var(--pine);
  outline-offset: 1px;
}

.card-image {
  background: var(--sheet-2);
}

/* 留版裁切标记：四个角的朱砂角线，由渐变一次画成 */
.crop-marks {
  position: absolute;
  inset: 6px;
  pointer-events: none;
  z-index: 4;
  background-image:
    linear-gradient(var(--seal), var(--seal)), linear-gradient(var(--seal), var(--seal)),
    linear-gradient(var(--seal), var(--seal)), linear-gradient(var(--seal), var(--seal)),
    linear-gradient(var(--seal), var(--seal)), linear-gradient(var(--seal), var(--seal)),
    linear-gradient(var(--seal), var(--seal)), linear-gradient(var(--seal), var(--seal));
  background-repeat: no-repeat;
  background-size:
    14px 1.5px,
    1.5px 14px,
    14px 1.5px,
    1.5px 14px,
    14px 1.5px,
    1.5px 14px,
    14px 1.5px,
    1.5px 14px;
  background-position:
    left top,
    left top,
    right top,
    right top,
    left bottom,
    left bottom,
    right bottom,
    right bottom;
}

.video-badge {
  width: 32px;
  height: 32px;
  border-radius: 2px;
  background: rgba(28, 32, 30, 0.72);
  font-size: 13px;
}

/* 操作层：方角纸面按钮，不用圆形浮钮 */
.card-overlay {
  top: 6px;
  right: 6px;
  gap: 2px;
}

.card-overlay :deep(.arco-btn) {
  border-radius: 2px;
  background: rgba(250, 249, 244, 0.92);
  border: 1px solid var(--line-strong);
  color: var(--ink);
}

.card-overlay :deep(.arco-btn:hover) {
  background: var(--sheet);
  border-color: var(--ink-3);
}

.card-overlay :deep(.arco-btn-primary.arco-btn-status-danger) {
  background: var(--seal);
  border-color: var(--seal);
  color: var(--sheet);
}

.card-rate {
  border-radius: 2px;
  background: rgba(250, 249, 244, 0.92);
  border: 1px solid var(--line);
  font-size: 14px;
}

.analysis-badge,
.sim-badge {
  border-radius: 2px;
  background: rgba(28, 32, 30, 0.74);
  font-size: 11px;
  padding: 2px 7px;
}

/* 批量勾选：方角墨线，选中朱砂 */
.select-checkbox {
  width: 22px;
  height: 22px;
  border-radius: 2px;
  background: rgba(250, 249, 244, 0.92);
  border: 1px solid var(--line-strong);
}

.select-checkbox.checked {
  background: var(--seal);
  border-color: var(--seal);
}

.view-detail-btn {
  border-radius: 2px;
  background: rgba(28, 32, 30, 0.72);
}

/* 说明栏：样张纸上的手写标签行 */
.card-body {
  padding: 8px 10px 10px;
  border-top: 1px solid var(--line);
  background: var(--sheet);
}

.card-source {
  gap: 8px;
  margin-bottom: 6px;
}

.card-source :deep(.arco-tag) {
  height: 18px;
  line-height: 16px;
  padding: 0 5px;
  background: transparent;
  border: 1px solid var(--line-strong);
  color: var(--ink-2);
  font-size: 11px;
  font-weight: 500;
  border-radius: 2px;
}

.author {
  font-size: 12px;
  color: var(--ink-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card-tags {
  gap: 3px;
}

.quality-badges {
  top: 6px;
  left: 6px;
  gap: 3px;
}

/* 状态徽标收进印样台色系：AI=墨、拒绝=朱砂、待审核=灰墨（紫色留给旧页面，第二阶段统一） */
.quality-badge.ai :deep(.status-badge) {
  background: var(--ink) !important;
}

.quality-badge.rejected :deep(.status-badge) {
  background: var(--seal) !important;
}

.quality-badge.pending :deep(.status-badge) {
  background: rgba(28, 32, 30, 0.55) !important;
}

/* 收藏时间：等宽数字 + 弱墨，不抢样张 */
.favorited-at {
  margin-top: 5px;
  font-size: 11px;
  color: var(--ink-3);
  font-family: var(--font-numeric);
  font-variant-numeric: tabular-nums lining-nums;
  white-space: nowrap;
}

/* ── 主操作：真实链接负责键盘/读屏，卡片容器负责鼠标点击（不覆盖图片，避免挡住悬停预览） ── */
.card-hit {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  padding: 0;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
  border: 0;
}
.card:focus-within {
  outline: 2px solid var(--pine);
  outline-offset: 1px;
}

.card:hover .card-overlay,
.card:focus-within .card-overlay,
.card:hover .view-detail-btn,
.card:focus-within .view-detail-btn {
  opacity: 1;
}

.card-overlay {
  z-index: 3;
  top: 46px;
}
.select-checkbox {
  z-index: 5;
}
.sim-badge {
  bottom: 42px;
}
.view-detail-btn {
  z-index: 4;
}
.card.is-rejected .card-image {
  opacity: 0.7;
}
.card.is-favorite,
.card.is-selected {
  border-color: var(--seal);
}

@media (hover: none) {
  .card-overlay {
    opacity: 1;
  }
  .card-overlay :deep(.arco-btn-size-mini) {
    width: 44px;
    min-width: 44px;
    height: 44px;
    padding: 0;
  }
  .card-rate {
    font-size: 20px;
    padding: 6px;
  }
  .view-detail-btn {
    opacity: 1;
    width: 44px;
    height: 44px;
  }
  .select-checkbox {
    width: 44px;
    height: 44px;
  }
}
</style>
