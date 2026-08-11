<script setup lang="ts">
/** 大图灯箱：点击图片全屏查看，支持左右切换和键盘导航。 */

import { watch } from 'vue'
import { getFileUrl } from '@/api/inspirations'

const props = defineProps<{
  show: boolean
  imagePath: string
}>()

const emit = defineEmits<{
  (e: 'close'): void
}>()

/** 按 ESC 关闭 */
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    emit('close')
  }
}

watch(
  () => props.show,
  (val) => {
    if (val) {
      document.addEventListener('keydown', onKeydown)
    } else {
      document.removeEventListener('keydown', onKeydown)
    }
  }
)
</script>

<template>
  <Teleport to="body">
    <div v-if="show" class="lightbox-backdrop" @click="emit('close')">
      <div class="lightbox-content" @click.stop>
        <img :src="getFileUrl(imagePath)" alt="大图浏览" />
        <n-button
          class="close-btn"
          circle
          size="large"
          @click="emit('close')"
        >
          ✕
        </n-button>
      </div>
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
}

.lightbox-content img {
  max-width: 90vw;
  max-height: 90vh;
  object-fit: contain;
  border-radius: 4px;
}

.close-btn {
  position: absolute;
  top: -16px;
  right: -16px;
}
</style>
