<script setup lang="ts">
/**
 * 异步状态容器：统一 loading / error / empty / default 四态，并提供重试入口。
 *
 * 背景：审查（docs/features/前端公共组件使用审查-2026-11-13.md P1-2）发现 80 个
 * 组件各自拼 `a-spin` / `a-empty` / `a-result`，错误经常被伪装成空态。这里先作为
 * 新试点组件使用，不强制一次性迁移存量页面。
 *
 * 用法：
 *   <AsyncState :loading="loading" :error="error" :empty="!items.length" @retry="load">
 *     <内容 />
 *   </AsyncState>
 */

withDefaults(
  defineProps<{
    /** 加载中（优先级最高） */
    loading?: boolean
    /** 错误文案（null/空串表示无错误） */
    error?: string | null
    /** 无数据（仅在非 loading / 非 error 时展示） */
    empty?: boolean
    /** 空态文案 */
    emptyText?: string
    /** 加载态文案 */
    loadingText?: string
  }>(),
  {
    loading: false,
    error: null,
    empty: false,
    emptyText: '暂无数据',
    loadingText: '加载中...',
  },
)

const emit = defineEmits<{
  (e: 'retry'): void
}>()
</script>

<template>
  <div class="async-state">
    <div v-if="loading" class="async-state-block">
      <a-spin :size="20" />
      <span v-if="loadingText" class="async-state-text">{{ loadingText }}</span>
    </div>
    <a-result v-else-if="error" status="error" title="加载失败" :subtitle="error">
      <template #extra>
        <a-button type="primary" @click="emit('retry')">重试</a-button>
      </template>
    </a-result>
    <a-empty v-else-if="empty" :description="emptyText" />
    <slot v-else />
  </div>
</template>

<style scoped>
.async-state {
  width: 100%;
}

.async-state-block {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 32px 0;
}

.async-state-text {
  color: var(--color-text-3);
  font-size: 13px;
}
</style>
