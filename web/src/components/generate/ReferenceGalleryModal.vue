<script setup lang="ts">
/** 已选参考图的「浏览大图」弹窗：左右翻看你选中的那几张参考。
 *
 * 为什么单开一个弹窗而不是复用素材灯箱：这里要的是**参考顺序**（第 1 张是主参考，
 * 会直接影响出图），灯箱只有「第 N / M 张」；而且用户是在描述框上方点缩略图进来的，
 * 期望「看完退回去接着写描述」，所以只做一个轻量的大图翻阅，不做收藏/标签等动作。
 */

import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { getFileUrl } from '@/api/inspirations'

const props = defineProps<{
  visible: boolean
  /** 已选参考（按参考顺序） */
  items: Array<{ id: string; file_path: string; thumbnail_path?: string | null }>
  /** 打开时定位到第几张（0 起） */
  startIndex?: number
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
}>()

const index = ref(0)

// 每次打开回到「用户点进来的那张」；翻到越界时夹紧（参考图被删/数量变化也不会白屏）
watch(
  () => [props.visible, props.startIndex, props.items.length] as const,
  ([open]) => {
    if (!open) return
    const max = Math.max(0, props.items.length - 1)
    index.value = Math.min(Math.max(0, props.startIndex ?? 0), max)
  },
  { immediate: true },
)

const current = computed(() => props.items[index.value] ?? null)

/** 大图优先用原图（参考图要看细节、版型与面料） */
const src = computed(() => {
  const item = current.value
  if (!item) return ''
  return getFileUrl(item.file_path || item.thumbnail_path || '')
})

function prev(): void {
  if (index.value > 0) index.value -= 1
}

function next(): void {
  if (index.value < props.items.length - 1) index.value += 1
}

function onKeydown(event: KeyboardEvent): void {
  if (!props.visible) return
  if (event.key === 'ArrowLeft') {
    event.preventDefault()
    prev()
  } else if (event.key === 'ArrowRight') {
    event.preventDefault()
    next()
  }
}

// immediate：组件有可能「挂着就已经打开」（父级先置 true 再渲染），
// 否则第一次打开时键盘不生效，要关了再开才行
watch(
  () => props.visible,
  (open) => {
    if (open) window.addEventListener('keydown', onKeydown)
    else window.removeEventListener('keydown', onKeydown)
  },
  { immediate: true },
)

onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <a-modal
    :visible="visible"
    title="已选参考图"
    :width="880"
    :footer="false"
    unmount-on-close
    @cancel="emit('update:visible', false)"
  >
    <div class="rg-head">
      <span class="rg-hint">
        参考 {{ index + 1 }} / {{ items.length }} · 第 1 张是主参考（出图会优先对齐它的人物与版型）
      </span>
      <span class="rg-hint">← → 翻看 · Esc 关闭</span>
    </div>

    <div class="rg-stage">
      <img v-if="src" :src="src" alt="" class="rg-img" />
      <div v-else class="rg-empty">这张参考图已经不在素材库里了</div>
    </div>

    <div class="rg-footer">
      <a-space>
        <a-button size="small" :disabled="index === 0" @click="prev">← 上一张</a-button>
        <a-button size="small" :disabled="index >= items.length - 1" @click="next">
          下一张 →
        </a-button>
      </a-space>
      <span v-if="current" class="rg-id">{{ current.id }}</span>
    </div>
  </a-modal>
</template>

<style scoped>
.rg-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 10px;
  flex-wrap: wrap;
}
.rg-hint {
  color: var(--color-text-3);
  font-size: 12px;
}
.rg-stage {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 60vh;
  background: var(--color-fill-2);
  border-radius: 6px;
  overflow: hidden;
}
.rg-img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  display: block;
}
.rg-empty {
  color: var(--color-text-3);
  font-size: 13px;
}
.rg-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 10px;
}
.rg-id {
  color: var(--color-text-3);
  font-size: 12px;
  font-family: var(--font-family-mono, monospace);
}
</style>
