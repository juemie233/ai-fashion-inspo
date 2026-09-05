<script setup lang="ts">
/** AI 分析队列总览：进度条、分析任务列表（含暂停/进行中/排队）、活动分析与排队素材。 */

import { getFileUrl } from '@/api/inspirations'
import StatusTag from '@/components/common/StatusTag.vue'
import type { QueueStats, TaskInfo, QueueItem } from '@/types/analysis'

defineProps<{
  queueStats: QueueStats
  batchAnalyzing: boolean
  /** 全部分析任务（batch/multi，含 paused/running/pending 及近期终态），按 id 倒序 */
  analysisTasks: TaskInfo[]
  activeAnalyses: Record<string, string>
  pendingQueue: QueueItem[]
  queuePaused: boolean
}>()

const emit = defineEmits<{
  (e: 'analyzeAll'): void
  (e: 'pauseTask', task: TaskInfo): void
  (e: 'resumeTask', task: TaskInfo): void
  (e: 'cancelTask', task: TaskInfo): void
  (e: 'togglePause'): void
  (e: 'cancelQueueItem', inspirationId: string): void
}>()

/** 判断文件路径是否为视频（缩略图缺失时禁止把 mp4 当 <img> 加载） */
function isVideoFile(path: string | null): boolean {
  return !!path && /\.(mp4|webm|mov|m4v)$/i.test(path)
}

/** 任务类型中文 */
function taskTypeLabel(type: string): string {
  return type === 'multi_analyze' ? '组合分析' : '批量分析'
}
</script>

<template>
  <div>
    <!-- 进度条 + 操作 -->
    <div style="display: flex; align-items: center; gap: 16px; margin-bottom: 20px">
      <a-progress
        v-if="queueStats.total > 0"
        type="line"
        :percent="Math.round((queueStats.analyzed / queueStats.total) * 100) / 100"
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

    <!-- 分析任务列表（含暂停/进行中/排队中及近期已完成，按 id 倒序） -->
    <a-card v-if="analysisTasks.length > 0" size="small" style="margin-bottom: 16px">
      <template #title>
        <span>分析任务（{{ analysisTasks.length }}）</span>
        <span style="font-size: 12px; color: #888; margin-left: 8px"
          >含暂停/进行中任务，可在此恢复或取消</span
        >
      </template>

      <div v-for="task in analysisTasks" :key="task.id" class="task-row">
        <div class="task-head">
          <span class="task-title">
            {{ taskTypeLabel(task.type) }} #{{ task.id }}
            <StatusTag :status="task.status" style="margin-left: 6px" />
          </span>
          <span class="task-meta">
            {{ task.done }} / {{ task.total }} 已完成
            <template v-if="task.retry_count > 0"> · 已重试 {{ task.retry_count }} 次</template>
          </span>
        </div>

        <!-- 运行中：进度条；pending（含等待自动重试）：提示；paused：显示已保存进度 -->
        <a-progress
          v-if="task.status === 'running'"
          type="line"
          :percent="task.progress"
          :stroke-width="14"
          size="small"
        />
        <div
          v-else-if="task.status === 'pending' && task.next_retry_at"
          style="font-size: 12px; color: #f0a020"
        >
          等待自动重试中...
        </div>
        <div v-else-if="task.status === 'paused'" style="font-size: 12px; color: #666">
          ⏸ 已暂停于 {{ task.progress }}%（{{ task.done }}/{{ task.total }}），恢复后从断点续算
        </div>

        <div v-if="task.error" class="task-error">{{ task.error }}</div>

        <div class="task-actions">
          <a-button
            v-if="task.status === 'pending'"
            size="mini"
            type="outline"
            status="danger"
            @click="emit('cancelTask', task)"
          >
            取消
          </a-button>
          <a-button
            v-if="task.status === 'running'"
            size="mini"
            type="outline"
            status="warning"
            @click="emit('pauseTask', task)"
          >
            ⏸ 暂停
          </a-button>
          <a-button
            v-if="task.status === 'paused'"
            size="mini"
            type="outline"
            status="success"
            @click="emit('resumeTask', task)"
          >
            ▶ 恢复
          </a-button>
        </div>
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
          <!-- 视频素材：file_path 是 mp4，不能当 <img> 加载（必破图），显示占位符 -->
          <div
            v-else-if="isVideoFile(item.file_path)"
            title="视频素材（缩略图生成中或缺失）"
            class="video-placeholder"
          >
            🎬
          </div>
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
.task-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 0;
  border-bottom: 1px dashed #f0f0f0;
}
.task-row:last-child {
  border-bottom: none;
}
.task-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}
.task-title {
  font-size: 13px;
  font-weight: 500;
}
.task-meta {
  font-size: 12px;
  color: #888;
}
.task-error {
  font-size: 12px;
  color: #ef4444;
}
.task-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}

/* 排队素材 */
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

.video-placeholder {
  width: 80px;
  height: 120px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  background: #f2f3f5;
  color: #86909c;
  font-size: 24px;
}
</style>
