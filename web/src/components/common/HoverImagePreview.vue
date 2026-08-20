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
    largeSrc: string
    /** 停留时长（毫秒）：短暂停留才弹出，快速扫过不闪烁 */
    delay?: number
  }>(),
  {
    delay: 250,
  },
)

/** 浮层是否显示 */
const visible = ref(false)
/** 停留计时器 */
let timer: number | null = null

function start() {
  clear()
  if (!props.largeSrc) return
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
  <div class="hover-preview-trigger" @mouseenter="start" @mouseleave="clear">
    <slot />
    <Teleport to="body">
      <div v-if="visible" class="hover-preview-layer">
        <div class="hover-preview-panel">
          <img :src="largeSrc" alt="悬停大图预览" />
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
