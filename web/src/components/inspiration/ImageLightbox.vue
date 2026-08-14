<script setup lang="ts">
/** 大图灯箱：支持多图左右切换、滚轮/按键/双击缩放、加载提示与 body 滚动锁定。 */

import { ref, computed, watch, onUnmounted } from 'vue'
import { getFileUrl } from '@/api/inspirations'

const props = defineProps<{
  /** 是否打开 */
  show: boolean
  /** 单图路径（兼容旧调用：单图时退化为仅查看 + 缩放） */
  imagePath?: string
  /** 多图列表（可选），传入后支持左右切换 */
  imagePaths?: string[]
  /** 打开时初始显示索引（与 imagePaths 配合） */
  initialIndex?: number
}>()

const emit = defineEmits<{
  (e: 'close'): void
}>()

/** 实际图片列表：优先使用 imagePaths，否则退化为单图 */
const imageList = computed<string[]>(() => {
  if (props.imagePaths && props.imagePaths.length > 0) return props.imagePaths
  if (props.imagePath) return [props.imagePath]
  return []
})

const currentIndex = ref(0)
/** 当前显示的图片路径 */
const currentImage = computed(() => imageList.value[currentIndex.value] || '')

// ===== 缩放 =====
const MIN_SCALE = 0.5
const MAX_SCALE = 5
const SCALE_STEP = 0.2
const scale = ref(1)

function zoomIn() { scale.value = Math.min(MAX_SCALE, +(scale.value + SCALE_STEP).toFixed(2)) }
function zoomOut() { scale.value = Math.max(MIN_SCALE, +(scale.value - SCALE_STEP).toFixed(2)) }
function resetZoom() { scale.value = 1 }
/** 双击在 1x 与 2x 间切换 */
function onDblClick() {
  scale.value = scale.value === 1 ? 2 : 1
}

/** 滚轮缩放：向上放大，向下缩小 */
function onWheel(e: WheelEvent) {
  if (e.deltaY < 0) zoomIn()
  else zoomOut()
}

// ===== 左右切换 =====
const canPrev = computed(() => imageList.value.length > 1 && currentIndex.value > 0)
const canNext = computed(() => imageList.value.length > 1 && currentIndex.value < imageList.value.length - 1)

function goPrev() {
  if (!canPrev.value) return
  currentIndex.value--
  resetZoom()
  imageLoading.value = true  // 切换图片后重新进入加载态，新图加载完成由 @load 清除
}
function goNext() {
  if (!canNext.value) return
  currentIndex.value++
  resetZoom()
  imageLoading.value = true
}

// ===== 加载状态 =====
const imageLoading = ref(false)
function onImageLoad() { imageLoading.value = false }

// ===== 滚动锁定与键盘 =====
/** 打开前的 body overflow，关闭/卸载时恢复 */
let prevBodyOverflow = ''
/** 当前是否由本组件锁定了 body 滚动 */
let bodyLocked = false

/** 键盘：Esc 关闭优先，左右箭头切换、+/- 缩放 */
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    emit('close')
  } else if (e.key === 'ArrowLeft') {
    e.preventDefault()
    goPrev()
  } else if (e.key === 'ArrowRight') {
    e.preventDefault()
    goNext()
  } else if (e.key === '+' || e.key === '=') {
    e.preventDefault()
    zoomIn()
  } else if (e.key === '-' || e.key === '_') {
    e.preventDefault()
    zoomOut()
  }
}

watch(
  () => props.show,
  (val) => {
    if (val) {
      // 打开：锁定 body 滚动，重置索引与缩放，标记图片为加载中
      bodyLocked = true
      prevBodyOverflow = document.body.style.overflow
      document.body.style.overflow = 'hidden'
      const len = imageList.value.length
      currentIndex.value = Math.max(0, Math.min(props.initialIndex ?? 0, Math.max(len - 1, 0)))
      resetZoom()
      imageLoading.value = true
      document.addEventListener('keydown', onKeydown)
    } else {
      if (bodyLocked) {
        document.body.style.overflow = prevBodyOverflow
        bodyLocked = false
      }
      document.removeEventListener('keydown', onKeydown)
    }
  }
)

// 组件被卸载（如灯箱未关闭直接路由跳走）时也要移除监听器并恢复滚动
onUnmounted(() => {
  document.removeEventListener('keydown', onKeydown)
  if (bodyLocked) {
    document.body.style.overflow = prevBodyOverflow
    bodyLocked = false
  }
})
</script>

<template>
  <Teleport to="body">
    <div v-if="show" class="lightbox-backdrop" @click="emit('close')">
      <div class="lightbox-content" @click.stop @wheel.prevent="onWheel">
        <!-- 图片 + 加载中提示 -->
        <div class="lightbox-img-wrap" @dblclick="onDblClick">
          <n-spin v-if="currentImage" :show="imageLoading" class="lightbox-loading" size="large">
            <img
              :src="getFileUrl(currentImage)"
              :alt="imageList.length > 1 ? `大图浏览 ${currentIndex + 1}/${imageList.length}` : '大图浏览'"
              class="lightbox-img"
              :class="{ 'is-zoomed': scale !== 1 }"
              :style="{ transform: `scale(${scale})` }"
              @load="onImageLoad"
              @error="imageLoading = false"
            />
          </n-spin>
        </div>

        <!-- 图片计数（仅多图） -->
        <div v-if="imageList.length > 1" class="lightbox-count">
          {{ currentIndex + 1 }} / {{ imageList.length }}
        </div>

        <!-- 缩放控制 -->
        <div class="zoom-controls">
          <n-button size="small" circle @click="zoomOut" title="缩小（-）">−</n-button>
          <span class="zoom-level">{{ Math.round(scale * 100) }}%</span>
          <n-button size="small" circle @click="zoomIn" title="放大（+）">＋</n-button>
          <n-button size="small" @click="resetZoom" title="重置缩放">重置</n-button>
        </div>

        <!-- 关闭按钮 -->
        <n-button class="close-btn" circle size="large" @click="emit('close')">
          ✕
        </n-button>
      </div>

      <!-- 左右切换（仅多图时显示，点击不触发射出 close） -->
      <n-button v-if="canPrev" class="nav-btn nav-prev" circle size="large" @click.stop="goPrev">‹</n-button>
      <n-button v-if="canNext" class="nav-btn nav-next" circle size="large" @click.stop="goNext">›</n-button>
    </div>
  </Teleport>
</template>

<style scoped>
.lightbox-backdrop {
  position: fixed;
  inset: 0;
  z-index: 9999;
  background: rgba(0, 0, 0, 0.85);
  display: flex;
  align-items: center;
  justify-content: center;
}

.lightbox-content {
  position: relative;
  max-width: 90vw;
  max-height: 90vh;
  display: flex;
  align-items: center;
  justify-content: center;
}

.lightbox-img-wrap {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  max-width: 90vw;
  max-height: 90vh;
  overflow: hidden;
  cursor: zoom-in;
}

.lightbox-img {
  max-width: 90vw;
  max-height: 90vh;
  object-fit: contain;
  border-radius: 4px;
  transition: transform 0.2s ease;
  transform-origin: center center;
  user-select: none;
}

.lightbox-img.is-zoomed {
  cursor: zoom-out;
}

.lightbox-count {
  position: absolute;
  top: 16px;
  left: 50%;
  transform: translateX(-50%);
  color: #fff;
  font-size: 14px;
  background: rgba(0, 0, 0, 0.5);
  border-radius: 4px;
  padding: 2px 10px;
  pointer-events: none;
}

.zoom-controls {
  position: absolute;
  bottom: 20px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 8px;
  background: rgba(0, 0, 0, 0.5);
  border-radius: 20px;
  padding: 6px 12px;
}

.zoom-level {
  color: #fff;
  font-size: 12px;
  min-width: 44px;
  text-align: center;
}

.nav-btn {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  z-index: 10;
}

.nav-prev {
  left: 24px;
}

.nav-next {
  right: 24px;
}

.close-btn {
  position: absolute;
  top: -16px;
  right: -16px;
}
</style>
