<script setup lang="ts">
/** AI 分析队列总览：进度条、批量分析入口、批量任务进度、活动分析与排队素材。 */

import { getFileUrl } from '@/api/inspirations'
import type { QueueStats, TaskInfo, QueueItem } from '@/types/analysis'

defineProps<{
  queueStats: QueueStats
  batchAnalyzing: boolean
  batchTask: TaskInfo | null
  activeAnalyses: Record<string, string>
  pendingQueue: QueueItem[]
  queuePaused: boolean
}>()

const emit = defineEmits<{
  (e: 'analyzeAll'): void
  (e: 'cancelBatchTask'): void
  (e: 'closeBatchTask'): void
  (e: 'togglePause'): void
  (e: 'cancelQueueItem', inspirationId: string): void
}>()

/** 任务状态中文标签 */
const taskStatusLabel: Record<string, string> = {
  pending: '排队中',
  running: '进行中',
  success: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

/** 任务状态 → Arco Tag 预设色 */
function statusColor(status: string): string {
  if (status === 'success') return 'green'
  if (status === 'failed') return 'red'
  if (status === 'cancelled') return 'gray'
  return 'arcoblue'
}
</script>

<template>
  <div>
    <!-- 进度条 + 操作 -->
    <div style="display: flex; align-items: center; gap: 16px; margin-bottom: 20px">
      <a-progress
        v-if="queueStats.total > 0"
        type="line"
        :percent="queueStats.analyzed / queueStats.total"
        :stroke-width="24"
        style="flex: 1"
      />
      <a-button
        type="primary"
        @click="emit('analyzeAll')"
        :loading="batchAnalyzing"
        :disabled="queueStats.unanalyzed === 0"
      >
        {{ queueStats.unanalyzed > 0 ? `分析全部未分析 (${queueStats.unanalyzed})` : '全部已分析' }}
      </a-button>
    </div>

    <!-- 批量分析任务进度（数据库驱动任务队列） -->
    <a-card v-if="batchTask" size="small" style="margin-bottom: 16px">
      <template #title>
        <span>批量分析任务 #{{ batchTask.id }}</span>
        <a-tag :color="statusColor(batchTask.status)" size="small" style="margin-left: 8px">
          {{ taskStatusLabel[batchTask.status] }}
        </a-tag>
        <a-button
          v-if="['success', 'failed', 'cancelled'].includes(batchTask.status)"
          size="mini"
          type="text"
          style="margin-left: auto"
          @click="emit('closeBatchTask')"
        >
          关闭
        </a-button>
      </template>
      <a-progress
        type="line"
        :percent="batchTask.progress / 100"
        :stroke-width="20"
        :status="
          batchTask.status === 'failed'
            ? 'danger'
            : batchTask.status === 'success'
              ? 'success'
              : undefined
        "
      />
      <div
        style="
          display: flex;
          align-items: center;
          gap: 12px;
          margin-top: 6px;
          font-size: 12px;
          color: #888;
          flex-wrap: wrap;
        "
      >
        <span>{{ batchTask.done }} / {{ batchTask.total }} 已完成</span>
        <span v-if="batchTask.retry_count > 0" style="color: #f0a020"
          >已重试 {{ batchTask.retry_count }} 次</span
        >
        <span
          v-if="batchTask.status === 'pending' && batchTask.next_retry_at"
          style="color: #f0a020"
          >等待自动重试中...</span
        >
        <a-button
          v-if="batchTask.status === 'pending'"
          size="mini"
          type="outline"
          status="danger"
          style="margin-left: auto"
          @click="emit('cancelBatchTask')"
        >
          取消任务
        </a-button>
      </div>
      <div v-if="batchTask.error" style="font-size: 12px; color: #ef4444; margin-top: 4px">
        {{ batchTask.error }}
      </div>
    </a-card>

    <!-- 正在分析提示 + 暂停/恢复 -->
    <div
      style="display: flex; align-items: center; gap: 12px; margin-bottom: 16px; flex-wrap: wrap"
    >
      <a-alert
        v-if="Object.keys(activeAnalyses).length > 0"
        type="info"
        style="flex: 1; min-width: 300px"
        closable
      >
        <template #title>正在分析 {{ Object.keys(activeAnalyses).length }} 个素材...</template>
        <div v-for="(status, id) in activeAnalyses" :key="id" style="font-size: 12px; color: #666">
          素材 {{ id.slice(0, 8) }}... — {{ status }}
        </div>
      </a-alert>
      <a-button
        v-if="Object.keys(activeAnalyses).length > 0 || pendingQueue.length > 0"
        :type="queuePaused ? 'primary' : 'secondary'"
        :status="queuePaused ? 'success' : 'warning'"
        size="small"
        @click="emit('togglePause')"
      >
        {{ queuePaused ? '▶ 恢复队列' : '⏸ 暂停队列' }}
      </a-button>
    </div>

    <!-- 排队中素材缩略图 -->
    <div v-if="pendingQueue.length > 0" class="pending-queue">
      <div style="font-size: 13px; font-weight: 600; margin-bottom: 8px">
        📋 排队中 ({{ pendingQueue.length }})
        <span v-if="queuePaused" style="color: #f0a020; font-size: 12px"> — 已暂停</span>
      </div>
      <div class="pending-grid">
        <div v-for="item in pendingQueue" :key="item.inspiration_id" class="pending-card">
          <img
            v-if="item.thumbnail_path"
            :src="getFileUrl(item.thumbnail_path)"
            style="width: 80px; height: 120px; object-fit: cover; border-radius: 4px"
          />
          <img
            v-else-if="item.file_path"
            :src="getFileUrl(item.file_path)"
            style="width: 80px; height: 120px; object-fit: cover; border-radius: 4px"
          />
          <div style="font-size: 10px; color: #999; text-align: center; margin-top: 2px">
            {{ item.inspiration_id.slice(0, 6) }}...
          </div>
          <div style="font-size: 10px; color: #666; text-align: center">{{ item.status }}</div>
          <a-button
            v-if="item.status === '排队中'"
            size="mini"
            type="outline"
            status="danger"
            style="margin-top: 2px; font-size: 10px"
            @click="emit('cancelQueueItem', item.inspiration_id)"
          >
            取消
          </a-button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pending-queue {
  margin-bottom: 16px;
}
.pending-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.pending-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 4px;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  background: #fafafa;
}
</style>
