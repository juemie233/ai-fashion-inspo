<script setup lang="ts">
/** 生成历史表：每条的「同参重跑」与「删除本批记录」入口。
 *
 * 从 GenerateView.vue 抽出（视图只做编排）。表格只读不写：刷新、重跑、删除都由
 * emit 交给视图，删除逻辑（含清空当前批次的详情区）仍留在视图里。
 */

import type { GenerateStatus, GenerationRunBrief } from '@/api/generate'
import { formatDateTime, shortModelLabel } from '@/utils/format'

const props = defineProps<{
  /** 历史记录（按时间倒序，后端已排好） */
  history: GenerationRunBrief[]
  /** 列表刷新中 */
  loading: boolean
  /** 提交中（「同参重跑」的 loading） */
  submitting: boolean
  /** 正在删除的记录 ID（那一行按钮转 loading） */
  deletingId: number | null
  /** 环境状态：只为读 models_meta 里的档位名与「未审查」标记 */
  status: GenerateStatus | null
}>()

const emit = defineEmits<{
  (e: 'refresh'): void
  (e: 'retry', id: number): void
  (e: 'remove', id: number): void
}>()

/** 档位 key → 展示名（后端没给档位表时退回 key，不显示空白） */
function modelLabel(key: string): string {
  const meta = props.status?.models_meta?.[key]
  return meta ? shortModelLabel(meta.label) : key || '—'
}

/** 该档位是否为未审查权重（打标用） */
function modelUncensored(key: string): boolean {
  return props.status?.models_meta?.[key]?.uncensored === true
}
</script>

<template>
  <div class="gen-result-head">
    <span class="gen-label" style="margin: 0">生成历史</span>
    <a-button size="mini" :loading="loading" @click="emit('refresh')">刷新</a-button>
  </div>
  <div v-if="!history.length" class="gen-empty">还没有生成记录。</div>
  <a-table
    v-else
    :data="history"
    :pagination="false"
    row-key="id"
    size="small"
    :scroll="{ x: 960 }"
  >
    <template #columns>
      <a-table-column title="#" data-index="id" :width="70" />
      <a-table-column title="描述" data-index="prompt" :width="320" ellipsis />
      <a-table-column title="模型" :width="190">
        <template #cell="{ record }">
          <span class="gen-model-cell">
            {{ modelLabel(record.model_key) }}
            <a-tag v-if="modelUncensored(record.model_key)" color="red" size="small">
              未审查
            </a-tag>
          </span>
        </template>
      </a-table-column>
      <a-table-column title="状态" :width="110">
        <template #cell="{ record }">
          {{ record.status }}（{{ record.done }}/{{ record.total }}）
        </template>
      </a-table-column>
      <a-table-column title="时间" :width="150">
        <template #cell="{ record }">
          <span style="white-space: nowrap">{{ formatDateTime(record.created_at) }}</span>
        </template>
      </a-table-column>
      <a-table-column title="操作" :width="210">
        <template #cell="{ record }">
          <a-button size="mini" :loading="submitting" @click="emit('retry', record.id)">
            同参重跑
          </a-button>
          <!-- 只对已结束的批次给删除：运行中的批次后端会拒绝（400），按钮不显示更清楚 -->
          <a-popconfirm
            v-if="['done', 'failed', 'cancelled'].includes(record.status)"
            content="删除这条生成记录及其候选图？（只清理本批次的候选文件）"
            ok-text="删除"
            @ok="emit('remove', record.id)"
          >
            <a-button
              size="mini"
              status="danger"
              style="margin-left: 6px"
              :loading="deletingId === record.id"
            >
              删除
            </a-button>
          </a-popconfirm>
        </template>
      </a-table-column>
    </template>
  </a-table>
</template>

<style scoped>
/* 下面 .gen-label / .gen-result-head / .gen-hint 在视图里另有一份：
   生成结果卡与参数面板也要用，scoped 样式不能跨组件继承，故各自保留。 */
.gen-label {
  font-size: 13px;
  font-weight: 600;
  margin: 12px 0 6px;
}
.gen-result-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.gen-model-cell {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}
.gen-empty {
  color: #8a8f99;
  font-size: 13px;
  padding: 24px 0;
  text-align: center;
}
</style>
