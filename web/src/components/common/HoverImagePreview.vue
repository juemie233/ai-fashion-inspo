<script setup lang="ts">
/**
 * 悬停大图预览：鼠标在包裹内容上短暂停留后，弹出独立大图浮层（Teleport 到 body，
 * fixed 居中、指针穿透、原图清晰显示），移出即消失。
 *
 * 复用于素材网格（标签管理、质量审核）与采集结果预览等场景；触发区域样式由
 * 父组件通过 class（fallthrough 到根元素）控制，大图 URL 由父组件负责拼接
 * （getFileUrl）。包裹内容为缩略图等触发元素，浮层不影响原布局。
 */

import { onBeforeUnmount, ref } from 'vue'

const props = withDefaults(
  defineProps<{
    /** 大图 URL（原图/清晰图，由父组件拼接完整地址） */
    largeSrc?: string
    /** 可选：直接渲染小图；不传时使用默认插槽作为触发内容 */
    src?: string
    /** 小图 alt（src 模式使用） */
    alt?: string
    /** 小图 class（src 模式使用） */
    imgClass?: string
    /** 停留时长（毫秒）：短暂停留才弹出，快速扫过不闪烁 */
    delay?: number
    /** 触发区域按内容宽度（默认占满父容器）：表格缩略图等 flex 场景使用 */
    inline?: boolean
  }>(),
  {
    largeSrc: '',
    src: '',
    alt: '',
    imgClass: '',
    delay: 250,
    inline: false,
  },
)

/** 浮层是否显示 */
const visible = ref(false)
/** 只有真正支持 hover 的设备才启用预览；iPad 摸到 mouseenter 时不弹层 */
const canHover =
  typeof window === 'undefined' || typeof window.matchMedia !== 'function'
    ? true
    : window.matchMedia('(hover: hover)').matches
/** 停留计时器 */
let timer: number | null = null

function start() {
  clear()
  if (!canHover || !props.largeSrc) return
  timer = window.setTimeout(() => {
    visible.value = true
  }, props.delay)
}

function clear() {
  if (timer !== null) {
    window.clearTimeout(timer)
    timer = null
  }
  visible.value = false
}

onBeforeUnmount(clear)
</script>

<template>
  <div
    class="hover-preview-trigger"
    :class="{ 'is-inline': inline }"
    @mouseenter="start"
    @mouseleave="clear"
  >
    <img v-if="src" :src="src" :alt="alt" :class="imgClass" loading="lazy" />
    <slot v-else />
    <Teleport to="body">
      <div v-if="visible" class="hover-preview-layer">
        <div class="hover-preview-panel">
          <img :src="largeSrc" alt="悬停大图预览" loading="lazy" />
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
/* 触发区域：默认不干扰布局，具体尺寸/裁剪由父组件 class 控制（如 image-wrap） */
.hover-preview-trigger {
  width: 100%;
}

/* 表格缩略图等 flex 场景：按内容宽度占位，避免每个预览各占满一行 */
.hover-preview-trigger.is-inline {
  display: inline-block;
  width: auto;
}

/* 悬停大图预览：fixed 定位 + flex 居中，图片限制在视口内，任何屏幕尺寸都不会越界 */
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
</style>
