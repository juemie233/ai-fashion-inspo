<script setup lang="ts">
/** 任务日志查看器：展示单个任务的运行日志，支持关闭。 */

defineProps<{
  taskId: number | null
  content: string
  loading: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
}>()
</script>

<template>
<div class="log-viewer">
  <div class="log-header">
    <span>📄 任务 #{{ taskId }} 日志</span>
    <a-button size="mini" @click="emit('close')">关闭</a-button>
  </div>
  <a-spin :loading="loading">
    <pre class="log-content">{{ content || '（空）' }}</pre>
  </a-spin>
</div>
</template>

<style scoped>
.log-viewer{margin-top:12px;border:1px solid #333;border-radius:8px;overflow:hidden}
.log-header{display:flex;justify-content:space-between;align-items:center;padding:6px 12px;background:#333;color:#0f0;font-size:13px}
.log-content{margin:0;padding:12px;background:#1a1a1a;color:#0f0;font-size:11px;line-height:1.5;max-height:400px;overflow:auto;white-space:pre-wrap;word-break:break-all}
</style>
