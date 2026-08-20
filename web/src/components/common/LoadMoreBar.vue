<script setup lang="ts">
/**
 * 加载更多按钮条：统一「加载更多（x/y）」按钮 + loading 态 + 居中样式。
 * 由父组件在 loaded < total 时使用，点击触发 load-more。
 */

withDefaults(
  defineProps<{
    loading: boolean
    /** 已加载数量 */
    loaded: number
    /** 总数（用于展示与可见性判断） */
    total: number
  }>(),
  {},
)

const emit = defineEmits<{
  (e: 'load-more'): void
}>()
</script>

<template>
  <div v-if="loaded < total" class="load-more-bar">
    <a-button size="small" :loading="loading" @click="emit('load-more')">
      加载更多（{{ loaded }}/{{ total }}）
    </a-button>
  </div>
</template>

<style scoped>
.load-more-bar {
  display: flex;
  justify-content: center;
  padding: 12px 0 4px;
}
</style>
