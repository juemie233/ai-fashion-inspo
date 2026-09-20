<script setup lang="ts">
/** 素材详情页左栏：视频播放（带关键帧条带）/ 图片大图（带裁剪入口）+ 灯箱。
 *
 * 从 DetailView 模板原样抽出（提取式重构，标记与判定逻辑不变）。
 */

import type { InspirationDetailOut } from '@/api/inspirations'
import { getFileUrl } from '@/api/inspirations'
import ImageLightbox from '@/components/inspiration/ImageLightbox.vue'
import VideoKeyframes from '@/components/inspiration/VideoKeyframes.vue'

defineProps<{
  /** 素材详情 */
  detail: InspirationDetailOut
  /** 主图地址（预览优先，无法预览回退原图） */
  mainImageSrc: string
  /** 灯箱可切换的图片路径（主图 + 相似推荐） */
  lightboxPaths: string[]
  /** 图片版本号（裁剪/替换后强制刷新缓存） */
  fileVersion: string
}>()

/** 灯箱开关（原视图状态，经 v-model 双向绑定） */
const lightboxOpen = defineModel<boolean>('lightboxOpen', { required: true })
/** 裁剪弹窗开关（原视图状态，经 v-model 双向绑定） */
const cropOpen = defineModel<boolean>('cropOpen', { required: true })
</script>

<template>
  <div class="image-section">
    <div v-if="detail.media_type === 'video'" class="main-image-wrap">
      <video :src="getFileUrl(detail.file_path)" controls playsinline class="main-image" />
      <!-- 关键帧缩略图条带（懒提取，失败时组件内部静默隐藏） -->
      <VideoKeyframes :inspiration-id="detail.id" />
    </div>
    <div v-else class="main-image-wrap">
      <img :src="mainImageSrc" alt="穿搭素材" @click="lightboxOpen = true" class="main-image" />
      <!-- 裁剪入口：仅图片素材显示（视频缩略图/非图片不显示） -->
      <a-button
        v-if="!detail.deleted_at"
        class="crop-entry-btn"
        size="small"
        @click.stop="cropOpen = true"
        title="裁剪图片（保留中间区域，裁掉上下部分）"
      >
        ✂️ 裁剪
      </a-button>
    </div>

    <!-- 大图灯箱（仅图片，可左右切换到相似推荐图） -->
    <ImageLightbox
      v-if="detail.media_type !== 'video'"
      :show="lightboxOpen"
      :image-paths="lightboxPaths"
      :initial-index="0"
      :image-version="fileVersion"
      @close="lightboxOpen = false"
    />
  </div>
</template>

<style scoped>
/* 左栏样式随标记从 DetailView 迁入：scoped 样式不跨组件生效，留在视图会失效 */
.image-section {
  flex: 1;
  min-width: 0;
}

/* 主图容器：相对定位，供右上角裁剪入口按钮悬浮 */
.main-image-wrap {
  position: relative;
}

/* 裁剪入口：悬浮在图片右上角，不与图片的点击开灯箱冲突 */
.crop-entry-btn {
  position: absolute;
  top: 12px;
  right: 12px;
  z-index: 2;
  background: rgba(255, 255, 255, 0.92);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
}

.main-image {
  width: 100%;
  border-radius: 12px;
  cursor: zoom-in;
  object-fit: contain;
  max-height: 85vh;
  background: #f5f5f5;
}
</style>
