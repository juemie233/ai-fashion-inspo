<script setup lang="ts">
/**
 * 通用素材网格浏览器：网格/多选/悬停大图预览/排序密度/加载更多/跳详情。
 *
 * 数据由父组件加载并通过 props 传入（本组件不负责请求）；卡片操作按钮（如翻案/移除）
 * 与批量操作栏通过 slot 注入，供「标签素材网格」「质量审核未通过素材」等场景复用，
 * 避免重复实现网格交互（跳详情、悬停大图预览、多选、密度切换、加载更多）。
 */

import { computed, onBeforeUnmount, ref } from 'vue'
import { getFileUrl } from '@/api/inspirations'

/** 网格条目：id/缩略图/原图/媒体类型为通用字段，其余字段原样透传（如 quality_reason） */
export interface GridBrowserItem {
  id: string
  file_path?: string | null
  thumbnail_path?: string | null
  media_type?: string
  [key: string]: unknown
}

export type GridDensity = 'compact' | 'standard'

const props = withDefaults(
  defineProps<{
    items: GridBrowserItem[]
    total: number
    loading: boolean
    /** 密度（紧凑 4 列 / 标准 3 列），v-model 由父组件持有并持久化 */
    density?: GridDensity
    /** 排序值；showSort 为 true 时展示排序下拉 */
    sort?: string
    sortOptions?: { label: string; value: string }[]
    showSort?: boolean
    /** 空列表占位文案 */
    emptyText?: string
  }>(),
  {
    density: 'compact',
    sort: '',
    sortOptions: () => [],
    showSort: false,
    emptyText: '暂无素材',
  },
)

const emit = defineEmits<{
  (e: 'update:density', v: GridDensity): void
  (e: 'update:sort', v: string): void
  (e: 'load-more'): void
  (e: 'open-detail', item: GridBrowserItem): void
}>()

// ===== 多选 =====
const selectedIds = ref<Set<string>>(new Set())
const selectedCount = computed(() => selectedIds.value.size)
const allVisibleSelected = computed(
  () => props.items.length > 0 && props.items.every((i) => selectedIds.value.has(i.id)),
)

function toggleSelect(id: string) {
  const next = new Set(selectedIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selectedIds.value = next
}

function toggleSelectAll() {
  selectedIds.value = allVisibleSelected.value
    ? new Set()
    : new Set(props.items.map((i) => i.id))
}

function clearSelection() {
  selectedIds.value = new Set()
}

/** 清除选中集中某个 id（父组件单条移除素材后调用，避免计数残留） */
function removeSelectedId(id: string) {
  if (!selectedIds.value.has(id)) return
  const next = new Set(selectedIds.value)
  next.delete(id)
  selectedIds.value = next
}

defineExpose({ clearSelection, removeSelectedId })

/** 缩略图 URL（无缩略图时回退到原图） */
function fileUrl(item: GridBrowserItem): string {
  return getFileUrl(item.thumbnail_path || item.file_path || '')
}

// ===== 悬停快速预览：停留后屏幕中央弹出大图（复用标签分析历史卡片的交互） =====
/** 当前预览大图路径（null = 关闭） */
const hoverPreviewPath = ref<string | null>(null)
/** 悬停停留计时器：短暂停留才弹出预览，扫过网格时不闪烁 */
let hoverPreviewTimer: number | null = null

/** 预览用大图路径：视频素材回退首帧缩略图，图片用原图保证清晰 */
function previewPath(item: GridBrowserItem): string {
  if (item.media_type === 'video') return item.thumbnail_path || item.file_path || ''
  return item.file_path || item.thumbnail_path || ''
}

/** 鼠标进入卡片：短暂停留后显示居中大图预览 */
function startHoverPreview(item: GridBrowserItem) {
  clearHoverPreview()
  const path = previewPath(item)
  if (!path) return
  hoverPreviewTimer = window.setTimeout(() => {
    hoverPreviewPath.value = path
  }, 250)
}

/** 清除预览与计时器 */
function clearHoverPreview() {
  if (hoverPreviewTimer !== null) {
    window.clearTimeout(hoverPreviewTimer)
    hoverPreviewTimer = null
  }
  hoverPreviewPath.value = null
}

onBeforeUnmount(clearHoverPreview)
</script>

<template>
  <div class="grid-browser">
    <!-- 头部：左侧标题（slot）+ 排序 + 密度 -->
    <div class="grid-header">
      <div class="grid-header-left"><slot name="header-left" /></div>
      <n-space size="small" :wrap="false">
        <n-select
          v-if="showSort"
          :value="sort"
          :options="sortOptions"
          size="tiny"
          style="width:90px"
          @update:value="(v: string) => emit('update:sort', v)"
        />
        <n-button-group size="tiny">
          <n-button :type="density === 'compact' ? 'primary' : 'default'" @click="emit('update:density', 'compact')">⊞</n-button>
          <n-button :type="density === 'standard' ? 'primary' : 'default'" @click="emit('update:density', 'standard')">⊟</n-button>
        </n-button-group>
      </n-space>
    </div>

    <!-- 批量操作栏（有选中后出现，操作按钮由父组件通过 slot 注入） -->
    <div v-if="selectedCount > 0" class="batch-bar">
      <slot
        name="batch-actions"
        :ids="[...selectedIds]"
        :count="selectedCount"
        :clear="clearSelection"
        :all-selected="allVisibleSelected"
        :toggle-all="toggleSelectAll"
      />
    </div>

    <n-spin :show="loading">
      <div v-if="items.length === 0 && !loading" class="grid-empty">{{ emptyText }}</div>
      <div v-else :class="['image-grid', 'density-' + density]">
        <div
          v-for="item in items"
          :key="item.id"
          class="image-card"
          :class="{ 'is-selected': selectedIds.has(item.id) }"
          @click="emit('open-detail', item)"
        >
          <!-- 图片区域：干净展示，悬停停留弹出大图预览 -->
          <div class="image-wrap" @mouseenter="startHoverPreview(item)" @mouseleave="clearHoverPreview">
            <video
              v-if="item.media_type === 'video' && !item.thumbnail_path"
              :src="getFileUrl(item.file_path || '')"
              muted
              playsinline
              preload="metadata"
            />
            <img v-else-if="item.thumbnail_path || item.file_path" :src="fileUrl(item)" :alt="item.id" loading="lazy" />
            <div v-else class="no-preview">无预览</div>

            <!-- 卡片附加展示（父组件按需注入，如审核原因覆盖条） -->
            <slot name="card-extra" :item="item" />

            <!-- 选中遮罩 -->
            <div v-if="selectedIds.has(item.id)" class="card-selected-mask" />
          </div>

          <!-- 操作按钮区：多选勾选 + 父组件注入的操作按钮，独立显示在图片下方（常显） -->
          <div class="card-actions" @click.stop>
            <n-checkbox
              class="card-checkbox"
              :checked="selectedIds.has(item.id)"
              @update:checked="toggleSelect(item.id)"
            />
            <slot name="card-actions" :item="item" />
          </div>
        </div>
      </div>

      <div v-if="items.length < total" style="text-align:center;padding:12px">
        <n-button size="small" :loading="loading" @click="emit('load-more')">加载更多（{{ items.length }}/{{ total }}）</n-button>
      </div>
    </n-spin>

    <!-- 悬停快速预览：fixed 居中浮层，永不超出视口；整层指针穿透，不遮挡网格操作 -->
    <Teleport to="body">
      <div v-if="hoverPreviewPath" class="hover-preview-layer">
        <div class="hover-preview-panel">
          <img :src="getFileUrl(hoverPreviewPath)" alt="悬停大图预览" />
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.grid-header {
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid #f0f0f0;
  position: sticky;
  top: 0;
  background: #fff;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.grid-header-left {
  display: flex;
  align-items: center;
  min-width: 0;
  flex: 1;
}

.grid-empty {
  color: #999;
  text-align: center;
  padding: 60px 20px;
  font-size: 14px;
}

.batch-bar {
  display: flex;
  align-items: center;
  padding: 6px 10px;
  margin-bottom: 10px;
  background: #f0f6ff;
  border: 1px solid #c8dfff;
  border-radius: 6px;
  gap: 8px;
  flex-wrap: wrap;
}

.image-grid {
  display: grid;
}
.image-grid.density-compact {
  grid-template-columns: repeat(4, 1fr);
  gap: 6px;
}
.image-grid.density-standard {
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
}

.image-card {
  position: relative;
  cursor: pointer;
  border-radius: 4px;
  background: #fff;
  border: 1px solid #f0f0f0;
  padding: 3px;
}

/* 图片区域：相对定位容器，覆盖元素（勾选/原因/选中遮罩）以此为基准 */
.image-wrap {
  position: relative;
  overflow: hidden;
  border-radius: 4px;
}

/* 图片/视频：中图网格展示；大图浏览由「悬停快速预览」浮层承担 */
.image-wrap img,
.image-wrap video {
  width: 100%;
  aspect-ratio: 2/3;
  object-fit: cover;
  border-radius: 4px;
  display: block;
}

.no-preview {
  width: 100%;
  aspect-ratio: 2/3;
  background: #f5f5f5;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #ccc;
  font-size: 12px;
}

/* 多选勾选：与操作按钮一起常显在图片下方按钮区 */
.card-checkbox {
  background: rgba(255, 255, 255, 0.9);
  border-radius: 4px;
  padding: 2px;
}

/* 操作按钮：独立显示在图片下方（常显，不覆盖图片） */
.card-actions {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 6px;
  padding: 4px 2px 2px;
}

/* 选中遮罩（仅覆盖图片区域） */
.card-selected-mask {
  position: absolute;
  inset: 0;
  border: 2px solid #3b82f6;
  background: rgba(59, 130, 246, 0.15);
  border-radius: 4px;
  pointer-events: none;
  z-index: 1;
}

/* 悬停快速预览：固定定位 + flex 居中，图片限制在视口内，任何屏幕尺寸都不会越界 */
.hover-preview-layer {
  position: fixed;
  inset: 0;
  z-index: 2500;
  display: flex;
  align-items: center;
  justify-content: center;
  /* 指针穿透：预览浮层不拦截任何鼠标事件，网格可正常点击/悬停 */
  pointer-events: none;
}

.hover-preview-panel {
  max-width: 90vw;
  max-height: 88vh;
  border-radius: 12px;
  overflow: hidden;
  background: #fff;
  box-shadow: 0 12px 48px rgba(0, 0, 0, 0.35);
  animation: hover-preview-in 0.15s ease;
}

.hover-preview-panel img {
  display: block;
  max-width: 90vw;
  max-height: 88vh;
  object-fit: contain;
}

@keyframes hover-preview-in {
  from {
    opacity: 0;
    transform: scale(0.97);
  }
  to {
    opacity: 1;
    transform: scale(1);
  }
}

@media (max-width: 900px) {
  .image-grid {
    grid-template-columns: repeat(3, 1fr);
  }
}
</style>
