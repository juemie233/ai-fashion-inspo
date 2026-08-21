<script setup lang="ts">
/**
 * 图片手动裁剪弹窗：进入裁剪模式后，图片上显示上下两条可拖动的分割线，
 * 保留中间区域（裁掉上下部分），确认后交由后端按比例就地裁剪原图。
 *
 * 交互约定：
 * - 裁剪框左右边缘始终贴合图片全宽（只裁上下，不裁左右）；
 * - 上/下分割线可独立垂直拖动、不能越过对方，最小保留高度 50px（自然像素）；
 * - 裁剪框外区域使用半透明遮罩变暗（Cropper.js 默认 modal 行为）；
 * - 取消/关闭不修改原图；确认时展示加载状态，失败提示错误且原图不变。
 */

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import Cropper from 'cropperjs'
import 'cropperjs/dist/cropper.css'
import { Message } from '@arco-design/web-vue'
import { cropInspirationRegion, getFileUrl } from '@/api/inspirations'
import { getApiErrorMessage } from '@/utils/apiError'

const props = defineProps<{
  /** 是否打开裁剪弹窗 */
  visible: boolean
  /** 待裁剪素材 ID */
  inspirationId: string
  /** 待裁剪图片的存储相对路径（详情页大图） */
  imagePath: string
  /** 图片版本标识（可选）：裁剪等原地替换图片后附加 ?v= 绕过浏览器缓存 */
  imageVersion?: string
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'success'): void
}>()

/** 最小保留高度（自然像素，与后端 MIN_MANUAL_CROP_HEIGHT_PX 口径一致） */
const MIN_CROP_HEIGHT = 50
/** 初始裁剪框：默认保留图片高度 80% 的中间区域（上边界 10% / 下边界 90%） */
const INITIAL_REGION = { y1: 0.1, y2: 0.9 }

const imgRef = ref<HTMLImageElement | null>(null)
const containerRef = ref<HTMLDivElement | null>(null)
let cropper: Cropper | null = null

/** 当前裁剪区域的上下边界（相对 EXIF 校正后图片高度的比例，0~1） */
const cropRatios = ref<{ y1: number; y2: number }>({ ...INITIAL_REGION })
/** 保留高度是否满足最小高度（自然像素） */
const heightOk = ref(true)
/** Cropper 是否已完成初始化（未就绪前禁用确认） */
const imageReady = ref(false)
/** 图片加载失败（无法裁剪） */
const loadFailed = ref(false)
/** 提交中（等待后端处理，期间禁止重复操作） */
const cropping = ref(false)

/** 当前显示的图片 URL（带可选版本参数，裁剪后刷新缓存） */
const imageSrc = computed(() => {
  const base = getFileUrl(props.imagePath)
  return props.imageVersion ? `${base}?v=${props.imageVersion}` : base
})

function destroyCropper() {
  if (cropper) {
    cropper.destroy()
    cropper = null
  }
  imageReady.value = false
}

/** 等待图片解码完成（缓存命中时 load 事件不触发，需要 complete 兜底） */
function waitImageLoaded(): Promise<void> {
  const img = imgRef.value
  if (!img) return Promise.resolve()
  // complete 为 true 时事件流已结束：成功（naturalWidth>0）或已失败（由 @error 标记 loadFailed）
  if (img.complete) return Promise.resolve()
  return new Promise((resolve) => {
    img.addEventListener('load', () => resolve(), { once: true })
    img.addEventListener('error', () => resolve(), { once: true })
  })
}

/** 初始化 Cropper：视图内图片 + 仅上下可拖动的裁剪框 */
async function initCropper() {
  destroyCropper()
  loadFailed.value = false
  cropRatios.value = { ...INITIAL_REGION }
  heightOk.value = true
  await nextTick()
  const img = imgRef.value
  if (!img) return
  await waitImageLoaded()
  if (loadFailed.value) return
  await nextTick()
  if (!imgRef.value) return
  cropper = new Cropper(imgRef.value, {
    viewMode: 1, // 裁剪框不能超出图片范围
    dragMode: 'move', // 拖拽图片平移查看，不绘制新裁剪框
    autoCropArea: 0.8, // 初始裁剪框占图片 80% 面积 → 上边框 10%、下边框 90%
    minCropBoxHeight: MIN_CROP_HEIGHT, // 最小裁剪高度（不能越过对方）
    cropBoxMovable: false, // 裁剪框整体不可平移：位置恒为图片全宽，只允许上下拖边
    cropBoxResizable: true,
    modal: true, // 裁剪框外半透明遮罩变暗（默认行为）
    guides: false, // 隐藏裁剪框内九宫格虚线，仅保留上下分割线
    center: false, // 隐藏中心十字
    highlight: false, // 不做裁剪框内高亮，由 modal 遮罩表达明暗对比
    background: false, // 容器不使用棋盘格背景
    checkOrientation: true, // 按 EXIF 方向校正，与后端 ImageOps.exif_transpose 一致
    toggleDragModeOnDblclick: false, // 禁止双击切换出「绘制裁剪框」模式
    ready: onReady,
    crop: onCrop,
  })
}

/** Cropper 就绪：把裁剪框拉满图片全宽，上下边界取初始 10% / 90% */
function onReady() {
  const c = cropper
  if (!c) return
  const img = c.getImageData()
  const h = img.naturalHeight
  c.setData({
    x: 0,
    y: Math.round(h * INITIAL_REGION.y1),
    width: img.naturalWidth,
    height: Math.round(h * (INITIAL_REGION.y2 - INITIAL_REGION.y1)),
  })
  updateRatios()
  imageReady.value = true
}

/** 裁剪框移动/缩放时持续触发：实时换算上下边界比例 */
function onCrop() {
  updateRatios()
}

/**
 * 从 Cropper 读取裁剪区域并换算为相对高度的上下边界比例。
 * getData() 返回「原始图片自然像素坐标」（checkOrientation 已按 EXIF 校正），
 * 与后端裁剪比例基准（exif_transpose 后高度）一致，无需再做坐标换算。
 */
function updateRatios() {
  const c = cropper
  if (!c) return
  const data = c.getData()
  const naturalH = c.getImageData().naturalHeight || 1
  cropRatios.value = {
    y1: Math.min(1, Math.max(0, data.y / naturalH)),
    y2: Math.min(1, Math.max(0, (data.y + data.height) / naturalH)),
  }
  heightOk.value = data.height >= MIN_CROP_HEIGHT
}

/** 重置：恢复初始裁剪框（上边界 10%、下边界 90%、图片全宽） */
function resetCrop() {
  const c = cropper
  if (!c) return
  const img = c.getImageData()
  const h = img.naturalHeight
  c.setData({
    x: 0,
    y: Math.round(h * INITIAL_REGION.y1),
    width: img.naturalWidth,
    height: Math.round(h * (INITIAL_REGION.y2 - INITIAL_REGION.y1)),
  })
  updateRatios()
}

/** 确认裁剪：把上下边界比例提交给后端（裁剪由后端统一处理派生数据） */
async function confirmCrop() {
  if (cropping.value) return
  if (!imageReady.value || !cropper) return
  if (!heightOk.value) {
    Message.warning(`保留区域高度至少需要 ${MIN_CROP_HEIGHT}px`)
    return
  }
  cropping.value = true
  try {
    await cropInspirationRegion(props.inspirationId, cropRatios.value.y1, cropRatios.value.y2)
    Message.success('裁剪成功，素材已更新')
    emit('success')
    emit('close')
  } catch (e) {
    Message.error(getApiErrorMessage(e, '裁剪失败，原素材未修改'))
  } finally {
    cropping.value = false
  }
}

/** 取消/关闭：不修改原图 */
function close() {
  if (cropping.value) return // 提交中禁止关闭，避免页面状态与后端结果不一致
  emit('close')
}

/** 提交中禁止 Esc 关闭 */
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && !cropping.value) close()
}

// 打开时初始化 Cropper 并锁定 body 滚动，关闭时销毁实例并恢复
watch(
  () => props.visible,
  (val) => {
    if (val) {
      document.addEventListener('keydown', onKeydown)
      document.body.style.overflow = 'hidden'
      initCropper()
    } else {
      document.removeEventListener('keydown', onKeydown)
      document.body.style.overflow = ''
      destroyCropper()
    }
  },
  // flush: 'post'：确保 visible 变化后的 DOM（img）已渲染，imgRef 可用
  { flush: 'post' },
)

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown)
  document.body.style.overflow = ''
  destroyCropper()
})
</script>

<template>
  <Teleport to="body">
    <div v-if="visible" class="crop-modal-mask" @click.self="close">
      <div class="crop-modal-panel">
        <!-- 标题 + 关闭 -->
        <div class="crop-modal-header">
          <span class="crop-modal-title">✂️ 裁剪图片</span>
          <a-button
            type="text"
            size="small"
            class="crop-close-btn"
            :disabled="cropping"
            @click="close"
          >
            ✕
          </a-button>
        </div>

        <!-- 裁剪主体 -->
        <div class="crop-modal-body">
          <div ref="containerRef" class="crop-container">
            <img
              v-if="!loadFailed"
              ref="imgRef"
              :src="imageSrc"
              alt="待裁剪图片"
              class="crop-source-img"
              @error="loadFailed = true"
            />
            <a-empty v-else description="图片加载失败，无法裁剪" />
          </div>
          <p class="crop-tip">拖动上下分割线选择保留区域（仅纵向裁剪，左右固定为图片全宽）</p>
        </div>

        <!-- 底部操作 -->
        <div class="crop-modal-footer">
          <a-button :disabled="cropping" @click="close">取消</a-button>
          <a-button :disabled="cropping || !imageReady" @click="resetCrop">重置</a-button>
          <a-button
            type="primary"
            :loading="cropping"
            :disabled="cropping || !imageReady || !heightOk || loadFailed"
            @click="confirmCrop"
          >
            {{ cropping ? '裁剪中...' : '确认裁剪' }}
          </a-button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
/* 弹窗层：全屏遮罩 + 居中面板 */
.crop-modal-mask {
  position: fixed;
  inset: 0;
  z-index: 9999;
  background: rgba(0, 0, 0, 0.78);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}

.crop-modal-panel {
  width: min(92vw, 900px);
  max-height: 92vh;
  background: #fff;
  border-radius: 12px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
}

.crop-modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px 0;
}

.crop-modal-title {
  font-size: 16px;
  font-weight: 600;
}

.crop-close-btn {
  font-size: 14px;
}

.crop-modal-body {
  padding: 12px 16px;
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

/* Cropper 容器：显式高度（initContainer 读取父元素尺寸作为容器尺寸） */
.crop-container {
  position: relative;
  width: 100%;
  height: min(62vh, 560px);
  background: #1a1a1a;
  border-radius: 8px;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* 初始化前的原图展示（Cropper 接管后由 cropper.css 控制） */
.crop-source-img {
  display: block;
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}

.crop-tip {
  margin: 8px 0 0;
  text-align: center;
  color: #86909c;
  font-size: 12px;
}

.crop-modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 0 16px 16px;
}
</style>

<!--
  Cropper 内部结构（.cropper-container 内）的定制样式：
  仅「上下分割线 + 上下中间手柄」可见，左右分割线、角点与裁剪框描边全部隐藏。
  选择器以 .crop-container 开头限定作用域，避免影响其他页面元素。
-->
<style>
.crop-container .cropper-line.line-e,
.crop-container .cropper-line.line-w,
.crop-container .cropper-point:not(.point-n):not(.point-s) {
  display: none !important;
}

/* 隐藏裁剪框四边描边：边界完全由上下分割线表达 */
.crop-container .cropper-view-box {
  outline: none;
}

/* 裁剪框内不叠加白色高亮，遮罩明暗对比已足够表达保留区域 */
.crop-container .cropper-face {
  background-color: transparent !important;
}

/* 上下分割线：常显、加粗、亮色，作为可见的拖动手柄 */
.crop-container .cropper-line.line-n,
.crop-container .cropper-line.line-s {
  background-color: #fff;
  height: 8px;
  opacity: 1;
  box-shadow: 0 0 4px rgba(0, 0, 0, 0.6);
}

/* 上下中间手柄：白色横向圆角条，放大命中区域便于拖动 */
.crop-container .cropper-point.point-n,
.crop-container .cropper-point.point-s {
  background-color: #fff;
  border-radius: 4px;
  height: 14px;
  margin-left: -26px;
  opacity: 1;
  width: 52px;
  box-shadow: 0 0 4px rgba(0, 0, 0, 0.6);
}
.crop-container .cropper-point.point-n {
  top: -7px;
}
.crop-container .cropper-point.point-s {
  bottom: -7px;
}
</style>
