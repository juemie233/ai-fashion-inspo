<script setup lang="ts">
/** 生成历史表：每条的「同参重跑」与「删除本批记录」入口。
 *
 * 从 GenerateView.vue 抽出（视图只做编排）。表格只读不写：刷新、重跑、删除都由
 * emit 交给视图，删除逻辑（含清空当前批次的详情区）仍留在视图里。
 *
 * 描述列只做单行省略，**完整描述与参数在展开行里看**：历史接口返回完整 prompt
 * （不再截 120 字），行首箭头展开后给全文 + 负面词 + 尺寸/步数/CFG/seed 等。
 */

import { Message } from '@arco-design/web-vue'
import type { GenerateStatus, GenerationRunBrief } from '@/api/generate'
import StatusTag from '@/components/common/StatusTag.vue'
import { copyToClipboard } from '@/utils/clipboard'
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

/** 生成方式中文标签（kind 由档位决定） */
function kindLabel(kind: string): string {
  return kind === 'i2i' ? '参考图改款' : '文生图'
}

/** 档位 key → 展示名（后端没给档位表时退回 key，不显示空白） */
function modelLabel(key: string): string {
  const meta = props.status?.models_meta?.[key]
  return meta ? shortModelLabel(meta.label) : key || '—'
}

/** 该档位是否为未审查权重（打标用） */
function modelUncensored(key: string): boolean {
  return props.status?.models_meta?.[key]?.uncensored === true
}

/** 从历史参数快照里取数值（老记录可能缺字段，取不到就不展示） */
function paramNumber(record: GenerationRunBrief, key: string): number | null {
  const value = record.params?.[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function paramText(record: GenerationRunBrief, key: string): string {
  const value = record.params?.[key]
  return typeof value === 'string' ? value : ''
}

/** 尺寸摘要：宽/高都有才显示 */
function dimensions(record: GenerationRunBrief): string {
  const w = paramNumber(record, 'width')
  const h = paramNumber(record, 'height')
  return w && h ? `${w}×${h}` : ''
}

/** 参考素材张数（文生图为 0） */
function referenceCount(record: GenerationRunBrief): number {
  const ids = record.params?.reference_ids
  return Array.isArray(ids) ? ids.length : 0
}

/** 复制完整描述：历史列表只存参数，用户常需要把描述搬到别处复用 */
async function copyPrompt(prompt: string): Promise<void> {
  if (!prompt) return
  const ok = await copyToClipboard(prompt)
  if (ok) Message.success('描述已复制')
  else Message.error('复制失败，请手动选择文本')
}
</script>

<template>
  <div class="gen-result-head">
    <span class="gen-label" style="margin: 0">生成历史</span>
    <span class="gen-history-hint">点行首箭头看完整描述与参数</span>
    <a-button size="mini" :loading="loading" @click="emit('refresh')">刷新</a-button>
  </div>
  <div v-if="!history.length" class="gen-empty">还没有生成记录。</div>
  <a-table
    v-else
    :data="history"
    :pagination="false"
    row-key="id"
    size="small"
    :scroll="{ x: 1170 }"
    :expandable="{ width: 40 }"
  >
    <template #columns>
      <a-table-column title="#" data-index="id" :width="70" />
      <!-- 单行省略；悬停看全文，或用行首箭头展开完整详情 -->
      <a-table-column title="描述" data-index="prompt" :width="360" ellipsis tooltip />
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
      <a-table-column title="状态" :width="150">
        <template #cell="{ record }">
          <!-- 状态文案与配色统一走 common/StatusTag（单一来源 utils/taskLabel.ts） -->
          <StatusTag :status="record.status" />
          <span class="gen-status-count">{{ record.done }}/{{ record.total }}</span>
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

    <!-- 展开行：完整描述 + 负面词 + 生成参数 + 失败原因（列表列放不下的都在这里） -->
    <template #expand-row="{ record }">
      <div class="ghd" data-test="history-detail">
        <div class="ghd-row">
          <span class="ghd-label">完整描述</span>
          <div class="ghd-text">{{ record.prompt || '—' }}</div>
        </div>
        <div v-if="record.negative" class="ghd-row">
          <span class="ghd-label">负面提示词</span>
          <div class="ghd-text">{{ record.negative }}</div>
        </div>
        <div class="ghd-row">
          <span class="ghd-label">生成参数</span>
          <div class="ghd-params">
            <span class="ghd-chip">{{ kindLabel(record.kind) }}</span>
            <span v-if="dimensions(record)" class="ghd-chip">尺寸 {{ dimensions(record) }}</span>
            <span v-if="paramNumber(record, 'steps') !== null" class="ghd-chip">
              步数 {{ paramNumber(record, 'steps') }}
            </span>
            <span v-if="paramNumber(record, 'cfg') !== null" class="ghd-chip">
              CFG {{ paramNumber(record, 'cfg') }}
            </span>
            <span v-if="paramText(record, 'sampler')" class="ghd-chip">
              采样器 {{ paramText(record, 'sampler') }}
            </span>
            <span v-if="paramText(record, 'scheduler')" class="ghd-chip">
              调度器 {{ paramText(record, 'scheduler') }}
            </span>
            <span v-if="paramNumber(record, 'seed_base') !== null" class="ghd-chip">
              seed {{ paramNumber(record, 'seed_base') }}
            </span>
            <span class="ghd-chip">张数 {{ record.total }}</span>
            <span v-if="referenceCount(record)" class="ghd-chip">
              参考素材 {{ referenceCount(record) }} 张
            </span>
          </div>
        </div>
        <div v-if="record.error" class="ghd-row">
          <span class="ghd-label">失败原因</span>
          <div class="ghd-text ghd-error">{{ record.error }}</div>
        </div>
        <div class="ghd-meta">
          <span>模型 {{ modelLabel(record.model_key) }}</span>
          <span>工作流 {{ record.workflow_name || '—' }}</span>
          <span>创建 {{ formatDateTime(record.created_at) }}</span>
          <span>最后更新 {{ formatDateTime(record.updated_at) }}</span>
          <a-button size="mini" type="text" @click="copyPrompt(record.prompt)">复制描述</a-button>
        </div>
      </div>
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
.gen-history-hint {
  flex: 1;
  margin-left: 8px;
  color: #8a8f99;
  font-size: 12px;
}
.gen-model-cell {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}
.gen-status-count {
  margin-left: 6px;
  color: #8a8f99;
  font-size: 12px;
  white-space: nowrap;
}
.gen-empty {
  color: #8a8f99;
  font-size: 13px;
  padding: 24px 0;
  text-align: center;
}
/* 展开行：描述按原样保留换行（提示词里常有分句换行） */
.ghd {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 8px 12px 10px;
}
.ghd-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}
.ghd-label {
  flex: 0 0 76px;
  color: #86909c;
  font-size: 12px;
  line-height: 20px;
}
.ghd-text {
  flex: 1;
  min-width: 0;
  color: #1d2129;
  font-size: 13px;
  line-height: 20px;
  white-space: pre-wrap;
  word-break: break-word;
}
.ghd-error {
  color: #f53f3f;
}
.ghd-params {
  display: flex;
  flex: 1;
  flex-wrap: wrap;
  gap: 6px;
}
.ghd-chip {
  padding: 0 6px;
  border-radius: 3px;
  background: #f2f3f5;
  color: #4e5969;
  font-size: 12px;
  line-height: 20px;
}
.ghd-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  color: #86909c;
  font-size: 12px;
}
</style>
