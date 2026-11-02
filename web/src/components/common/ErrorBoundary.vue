<script setup lang="ts">
/**
 * 错误边界：捕获子树渲染错误，降级显示占位 + 重试按钮，避免局部渲染失败
 * 导致页面停留在异常加载态（此前「任务管理页一直加载」即渲染错误无兜底所致）。
 *
 * 用法：包裹可能出错的区块（路由视图 / 面板卡片）：
 *   <ErrorBoundary><RouterView /></ErrorBoundary>
 *
 * 诊断（2026-11-02 日志扫描 P2-4）：渲染错误不再只进控制台——写入前端环形
 * 缓冲（utils/logger），页面展示错误摘要 + 最近请求 ID，并提供「复制诊断信息」
 * 按钮，用户可直接把环境与最近日志贴给开发者。
 */

import { onErrorCaptured, ref, computed } from 'vue'
import { copyDiagnostics, getLastRequestId, logger } from '@/utils/logger'

const error = ref<Error | null>(null)
const copied = ref(false)

/** 最近一次 API 请求的 request_id（用于和后端日志对齐） */
const requestId = computed(() => getLastRequestId())

onErrorCaptured((e) => {
  error.value = e
  // 写入前端环形缓冲（同时也打到控制台）；阻止继续向上传播（错误边界自行接管）
  logger.error('[ErrorBoundary] 捕获渲染错误', e)
  return false
})

/** 重试：清除错误状态，子树重新渲染 */
function retry() {
  error.value = null
  copied.value = false
}

/** 复制诊断信息（环境 + 最近 200 条前端日志 + request_id） */
async function copyDiagnosticInfo() {
  copied.value = await copyDiagnostics()
  if (!copied.value) {
    logger.warn('[ErrorBoundary] 复制诊断信息失败（剪贴板不可用）')
  }
}
</script>

<template>
  <div v-if="error" class="error-boundary">
    <a-result status="error" title="页面渲染出错">
      <template #subtitle>
        <div>{{ error.message || '发生未知错误，请重试或刷新页面' }}</div>
        <div v-if="requestId" class="error-meta">最近请求ID：{{ requestId }}</div>
      </template>
      <template #extra>
        <a-space>
          <a-button type="primary" @click="retry">重试</a-button>
          <a-button @click="copyDiagnosticInfo">
            {{ copied ? '已复制诊断信息' : '复制诊断信息' }}
          </a-button>
        </a-space>
      </template>
    </a-result>
  </div>
  <slot v-else />
</template>

<style scoped>
.error-boundary {
  padding: 48px 16px;
}

.error-meta {
  margin-top: 8px;
  color: var(--color-text-3);
  font-family: monospace;
  font-size: 12px;
}
</style>
