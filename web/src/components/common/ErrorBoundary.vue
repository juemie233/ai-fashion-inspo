<script setup lang="ts">
/**
 * 错误边界：捕获子树渲染错误，降级显示占位 + 重试按钮，避免局部渲染失败
 * 导致页面停留在异常加载态（此前「任务管理页一直加载」即渲染错误无兜底所致）。
 *
 * 用法：包裹可能出错的区块（路由视图 / 面板卡片）：
 *   <ErrorBoundary><RouterView /></ErrorBoundary>
 */

import { onErrorCaptured, ref } from 'vue'

const error = ref<Error | null>(null)

onErrorCaptured((e) => {
  error.value = e
  // 记录到控制台便于排查；阻止继续向上传播（错误边界自行接管）
  console.error('[ErrorBoundary] 捕获渲染错误:', e)
  return false
})

/** 重试：清除错误状态，子树重新渲染 */
function retry() {
  error.value = null
}
</script>

<template>
  <div v-if="error" class="error-boundary">
    <a-result status="error" title="页面渲染出错">
      <template #subtitle>
        {{ error.message || '发生未知错误，请重试或刷新页面' }}
      </template>
      <template #extra>
        <a-button type="primary" @click="retry">重试</a-button>
      </template>
    </a-result>
  </div>
  <slot v-else />
</template>

<style scoped>
.error-boundary {
  padding: 48px 16px;
}
</style>
