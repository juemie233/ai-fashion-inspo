<script setup lang="ts">
/**
 * 缩略图卡片：统一「图片/视频 + 状态遮罩 + 序号角标 + 选中框 + 悬停大图 + 覆盖层 + 下方内容」。
 *
 * - 媒体区按 aspectRatio 撑高，网格列数/间距由父级布局控制
 * - 覆盖层（勾选框、操作按钮、标签、上传角标等）经 #extra 插槽注入
 *   （父级 scoped 样式控制定位，slot 内容属父作用域）
 * - 媒体下方内容（如垃圾桶卡片的来源标签/操作按钮行）经 #footer 插槽注入
 * - 传入 hoverSrc 后启用 HoverImagePreview 独立大图浮层
 */

import HoverImagePreview from '@/components/common/HoverImagePreview.vue'

/** 卡片状态（决定遮罩图标与配色） */
export type ThumbStatus = 'pending' | 'uploading' | 'done' | 'failed' | 'duplicate'

withDefaults(
  defineProps<{
    /** 图片地址（无视频时展示） */
    src?: string
    /** 视频地址（有视频且无缩略图时展示视频） */
    videoSrc?: string
    alt?: string
    /** 状态（默认 pending 无遮罩） */
    status?: ThumbStatus
    /** 状态提示文案（失败/重复原因的 title） */
    statusText?: string
    /** 上传进度（0-100）；status=uploading 且传入时显示进度圈，否则显示加载动画 */
    progress?: number
    /** 序号角标（如 1、2…），不传则不显示 */
    index?: number
    /** 选中态（蓝框高亮） */
    selected?: boolean
    /** 悬停大图 URL（传入后启用独立大图浮层） */
    hoverSrc?: string
    /** 媒体区宽高比（CSS aspect-ratio），默认 3/4 */
    aspectRatio?: string
  }>(),
  {
    src: '',
    videoSrc: '',
    alt: '',
    status: 'pending',
    statusText: '',
    progress: undefined,
    index: undefined,
    selected: false,
    hoverSrc: '',
    aspectRatio: '3/4',
  },
)
</script>

<template>
  <div class="thumb-card" :class="[`status-${status}`, { 'is-selected': selected }]">
    <!-- 媒体区（按 aspectRatio 撑高，overflow 裁剪） -->
    <div class="thumb-media-area" :style="{ aspectRatio }">
      <HoverImagePreview v-if="hoverSrc" :large-src="hoverSrc" class="thumb-media">
        <slot name="media">
          <video v-if="videoSrc" :src="videoSrc" muted playsinline preload="metadata" />
          <img v-else :src="src || ''" :alt="alt" loading="lazy" />
        </slot>
      </HoverImagePreview>
      <div v-else class="thumb-media">
        <slot name="media">
          <video v-if="videoSrc" :src="videoSrc" muted playsinline preload="metadata" />
          <img v-else :src="src || ''" :alt="alt" loading="lazy" />
        </slot>
      </div>

      <!-- 序号角标 -->
      <div v-if="index !== undefined" class="thumb-index">{{ index }}</div>

      <!-- 状态遮罩 -->
      <div v-if="status === 'uploading'" class="thumb-mask uploading">
        <a-progress v-if="progress !== undefined" type="circle" :percent="progress / 100" :width="44" />
        <a-spin v-else :size="18" />
      </div>
      <div v-else-if="status === 'done'" class="thumb-mask done">✓</div>
      <div v-else-if="status === 'failed'" class="thumb-mask failed" :title="statusText">✕</div>
      <div v-else-if="status === 'duplicate'" class="thumb-mask dup" :title="statusText">⧉</div>

      <!-- 选中框 -->
      <div v-if="selected" class="thumb-selected-mask" />

      <!-- 覆盖层：父级注入的勾选/按钮/标签/角标等 -->
      <slot name="extra" />
    </div>

    <!-- 下方内容：父级注入的流式信息/操作区 -->
    <slot name="footer" />
  </div>
</template>

<style scoped>
.thumb-card {
  position: relative;
  border-radius: 8px;
  overflow: hidden;
  background: #eef1f6;
}

/* 媒体区：按 aspectRatio 撑高，内部元素以此为基准定位 */
.thumb-media-area {
  position: relative;
  overflow: hidden;
}

/* 媒体容器撑满媒体区；:deep 同时命中默认渲染与父级 #media 插槽的 img/video */
.thumb-media {
  width: 100%;
  height: 100%;
}

.thumb-media :deep(img),
.thumb-media :deep(video) {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

/* 序号角标 */
.thumb-index {
  position: absolute;
  left: 4px;
  bottom: 4px;
  padding: 0 6px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  font-size: 11px;
}

/* 状态遮罩 */
.thumb-mask {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.55);
  font-size: 28px;
  font-weight: 700;
}

.thumb-mask.done {
  background: rgba(16, 185, 129, 0.35);
  color: #065f46;
}

.thumb-mask.failed {
  background: rgba(239, 68, 68, 0.35);
  color: #7f1d1d;
}

.thumb-mask.dup {
  background: rgba(250, 204, 21, 0.4);
  color: #713f12;
}

/* 选中框 */
.thumb-selected-mask {
  position: absolute;
  inset: 0;
  border: 2px solid #3b82f6;
  background: rgba(59, 130, 246, 0.15);
  pointer-events: none;
}
</style>
