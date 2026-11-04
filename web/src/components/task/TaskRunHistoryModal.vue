<script setup lang="ts">
/**
 * 任务运行记录弹窗：展示 ``task_run_logs`` 台账（2026-11-02 日志扫描 P1-4）。
 *
 * 每次执行尝试一行（含重试），字段：第几次、状态、开始/结束、耗时、处理量、
 * GPU 型号、错误摘要。此前台账只有表、没有读接口，排障只能手写 SQL；这里接
 * ``GET /api/tasks/{id}/runs``，任务管理页「运行记录」按钮打开。
 */

import { computed, h, ref, watch } from 'vue'
import { Message, Tag } from '@arco-design/web-vue'
import type { TableColumnData } from '@arco-design/web-vue'
import type { TaskRunOut, UnifiedTask } from '@/types/task'
import { fetchTaskRuns } from '@/api/tasks'
import { getApiErrorMessage } from '@/utils/apiError'
import { formatDate, renderTimeCell } from '@/utils/format'
import { TASK_TYPE_LABELS } from '@/utils/taskLabel'

const props = defineProps<{
  visible: boolean
  task: UnifiedTask | null
}>()
const emit = defineEmits<{ 'update:visible': [value: boolean] }>()

const loading = ref(false)
const runs = ref<TaskRunOut[]>([])

/** 状态中文文案与颜色（台账状态：running/success/retry/failed） */
const RUN_STATUS: Record<string, { label: string; color: string }> = {
  running: { label: '运行中', color: 'blue' },
  success: { label: '成功', color: 'green' },
  retry: { label: '待重试', color: 'orange' },
  failed: { label: '失败', color: 'red' },
}

/** 单次执行耗时（毫秒转可读文本；未结束返回「进行中」） */
function formatDuration(row: TaskRunOut): string {
  if (!row.finished_at) return row.status === 'running' ? '进行中' : '—'
  const ms = new Date(row.finished_at).getTime() - new Date(row.started_at).getTime()
  if (!Number.isFinite(ms) || ms < 0) return '—'
  if (ms < 1000) return `${ms} ms`
  const seconds = ms / 1000
  if (seconds < 60) return `${seconds.toFixed(1)} s`
  const minutes = Math.floor(seconds / 60)
  return `${minutes} 分 ${Math.round(seconds % 60)} 秒`
}

const columns: TableColumnData[] = [
  {
    title: '第几次',
    dataIndex: 'attempt',
    width: 80,
    render: ({ record }) => {
      const row = record as TaskRunOut
      return row.attempt === 0 ? '首次' : `第 ${row.attempt + 1} 次`
    },
  },
  {
    title: '状态',
    dataIndex: 'status',
    width: 90,
    render: ({ record }) => {
      const row = record as TaskRunOut
      const meta = RUN_STATUS[row.status] || { label: row.status, color: 'gray' }
      return h(Tag, { color: meta.color, size: 'small' }, () => meta.label)
    },
  },
  {
    title: '开始时间',
    dataIndex: 'started_at',
    width: 170,
    // 时间列必须单行（项目约定，渲染统一走 renderTimeCell）
    render: ({ record }) => renderTimeCell(formatDate((record as TaskRunOut).started_at)),
  },
  {
    title: '结束时间',
    dataIndex: 'finished_at',
    width: 170,
    render: ({ record }) => {
      const value = (record as TaskRunOut).finished_at
      return renderTimeCell(value ? formatDate(value) : '—')
    },
  },
  {
    title: '耗时',
    dataIndex: 'duration',
    width: 110,
    render: ({ record }) => formatDuration(record as TaskRunOut),
  },
  {
    title: '处理量',
    dataIndex: 'item_count',
    width: 100,
    render: ({ record }) => {
      const row = record as TaskRunOut
      // 统一展示「完成数 / 总数」；f2「我的喜欢/收藏」在开始时总数未知（total_count=0），
      // 结束后用 item_count 兜底，避免只显示一个 883 让用户以为没有总数。
      const total = row.total_count > 0 ? row.total_count : row.item_count
      return `${row.item_count} / ${total}`
    },
  },
  { title: '显卡', dataIndex: 'gpu_model', width: 150 },
  {
    title: '错误摘要',
    dataIndex: 'error',
    ellipsis: true,
    tooltip: true,
  },
]

const title = computed(() => {
  if (!props.task) return '运行记录'
  const typeLabel = TASK_TYPE_LABELS[props.task.type] || props.task.type
  return `运行记录 · ${typeLabel} #${props.task.id}`
})

async function loadRuns() {
  if (!props.task) return
  loading.value = true
  try {
    runs.value = await fetchTaskRuns(props.task.id)
  } catch (e) {
    runs.value = []
    Message.error(getApiErrorMessage(e, '加载运行记录失败'))
  } finally {
    loading.value = false
  }
}

// 打开弹窗或切换任务时拉取；关闭时不清空，避免弹窗收起动画期间闪空态
watch(
  () => [props.visible, props.task?.id] as const,
  ([visible]) => {
    if (visible) loadRuns()
  },
  { immediate: true },
)

function close() {
  emit('update:visible', false)
}
</script>

<template>
  <a-modal :visible="visible" :title="title" :footer="false" :width="960" @cancel="close">
    <a-table
      :columns="columns"
      :data="runs"
      :loading="loading"
      :bordered="false"
      :pagination="false"
    >
      <template #empty>
        <a-empty description="暂无执行记录（worker 重启后才有台账）" />
      </template>
    </a-table>
  </a-modal>
</template>
