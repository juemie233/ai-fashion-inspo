<script setup lang="ts">
/**
 * 统一状态标签：任务、质量审核、AI 标记、AI 分析、垃圾桶原因共用一套文案/颜色。
 *
 * - `kind="task"`（默认）保持原任务中心语义，依赖 utils/taskLabel；
 * - 其余 kind 由 utils/statusMeta 提供域内映射，页面不再手写 `a-tag` 颜色；
 * - `variant="badge"` 用于素材卡左上角的自绘小徽标（保留卡片原有布局，
 *   颜色仍由 statusMeta 单一来源决定）。
 */

import { computed } from 'vue'
import { normalizeTaskStatus, TASK_STATUS_LABELS, taskStatusType } from '@/utils/taskLabel'
import {
  aiStatusMeta,
  analysisStatusMeta,
  qualityStatusMeta,
  trashReasonMeta,
  type StatusMeta,
  type StatusTagKind,
} from '@/utils/statusMeta'

const props = withDefaults(
  defineProps<{
    /** 原始状态值（任务/质量/分析/垃圾桶原因等） */
    status: string
    /** 状态域；默认任务状态 */
    kind?: StatusTagKind
    /** badge 变体：素材卡自绘小徽标；默认 Arco tag */
    variant?: 'tag' | 'badge'
    /** AI 标记的来源类型（generated 时文案为「AI 生成」，否则「疑似 AI」） */
    sourceType?: string | null
    /** 覆盖默认 title */
    title?: string
  }>(),
  {
    kind: 'task',
    variant: 'tag',
    sourceType: null,
    title: '',
  },
)

const meta = computed<StatusMeta | null>(() => {
  if (props.kind === 'quality') return qualityStatusMeta(props.status)
  if (props.kind === 'ai') return aiStatusMeta(props.status === 'ai', props.sourceType)
  if (props.kind === 'analysis') return analysisStatusMeta(props.status)
  if (props.kind === 'trash') return trashReasonMeta(props.status)

  const normalized = normalizeTaskStatus(props.status)
  return {
    label: TASK_STATUS_LABELS[normalized] || props.status,
    color: taskStatusType(normalized),
    bg: 'rgba(160,160,160,.8)',
  }
})

const titleText = computed(() => props.title || meta.value?.title || '')
</script>

<template>
  <span class="status-tag-host" :title="titleText">
    <a-tag v-if="meta && variant === 'tag'" :color="meta.color" size="small">
      {{ meta.label }}
    </a-tag>
    <span v-else-if="meta" class="status-badge" :style="{ background: meta.bg }">{{
      meta.label
    }}</span>
  </span>
</template>

<style scoped>
.status-tag-host {
  display: inline-block;
}

.status-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  color: #fff;
  font-size: 11px;
  line-height: 1.4;
}
</style>
