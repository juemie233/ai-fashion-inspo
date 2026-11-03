<script setup lang="ts">
/**
 * 悬停自动放大预览：鼠标移到缩略图/候选图上，浮层显示大图。
 *
 * 为什么用「悬停」而不是点击：生成历史一屏几十张图，用户主要是快速扫一眼细节
 * （服装款式/脸部/手势），点开灯箱再关闭成本太高；点击仍保留给各自的原动作
 * （例如候选区点图看大图、入库弹窗里点图勾选）。
 *
 * 大图走候选图接口的 `?w=1024` 缩略图（后端首次生成后缓存在批次 `.thumbs/`），
 * 不直接拉 1.6~4 MB 的原图，悬停不会造成明显卡顿。
 */
withDefaults(
  defineProps<{
    /** 小图地址（列表缩略图或原图） */
    src: string
    /** 悬停时显示的大图地址（建议用候选图接口的 `?w=1024`） */
    previewSrc: string
    alt?: string
    /** 触发延迟（毫秒）：划过一排缩略图时不会疯狂弹层 */
    delay?: number
    /** 小图的 class（沿用列表原有尺寸样式） */
    imgClass?: string
  }>(),
  { alt: '', delay: 120, imgClass: '' },
)
</script>

<template>
  <a-popover
    trigger="hover"
    position="right"
    :mouse-enter-delay="delay"
    :mouse-leave-delay="60"
    :content-style="{ padding: '4px', borderRadius: '8px', overflow: 'hidden' }"
  >
    <template #content>
      <img :src="previewSrc" :alt="alt" class="ihp-large" loading="lazy" />
    </template>
    <img :src="src" :alt="alt" :class="imgClass" loading="lazy" />
  </a-popover>
</template>

<style scoped>
/* 大图按视口约束：竖图（1.5:2）也不会把弹层顶出屏幕 */
.ihp-large {
  display: block;
  max-width: min(58vw, 680px);
  max-height: 72vh;
  width: auto;
  height: auto;
  object-fit: contain;
  border-radius: 6px;
  background: var(--color-fill-2);
}
</style>
