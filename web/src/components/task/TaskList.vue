<script setup lang="ts">
/** 任务列表：统一展示任务队列与采集任务。 */

import { h } from 'vue'
import { NTag, NProgress, NSpin, NButton, NPopconfirm, NText, NIcon } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import type { UnifiedTask } from '@/types/task'
import {
  TASK_STATUS_LABELS,
  TASK_TYPE_ICONS,
  taskStatusType,
  taskTypeTagColor,
  predictEta,
} from '@/utils/taskLabel'
import { formatDate } from '@/utils/format'

defineProps<{ tasks: UnifiedTask[]; loading: boolean }>()
const emit = defineEmits<{
  cancel: [t: UnifiedTask]
  delete: [t: UnifiedTask]
}>()

/** 进度列：队列任务显示百分比进度条，采集任务运行中显示加载态 */
function renderProgress(row: UnifiedTask) {
  if (row.progress >= 0) {
    return h(NProgress, {
      type: 'line',
      percentage: row.progress,
      height: 8,
      style: 'width:140px',
    })
  }
  if (row.status === 'running') {
    return h('div', { style: 'display:flex;align-items:center;gap:6px' }, [
      h(NSpin, { size: 14 }),
      h(NText, { depth: 3 }, { default: () => '运行中' }),
    ])
  }
  return h(NText, { depth: 3 }, { default: () => '—' })
}

/** 完成数 / 总数列 */
function renderCount(row: UnifiedTask) {
  const text = row.total > 0 ? `${row.done} / ${row.total}` : row.done > 0 ? `${row.done}` : '—'
  return h(NText, { depth: 2 }, { default: () => text })
}

const columns: DataTableColumns<UnifiedTask> = [
  {
    title: '任务ID',
    key: 'id',
    width: 80,
    render: (row) => h(NText, { depth: 2 }, { default: () => String(row.id) }),
  },
  {
    title: '任务',
    key: 'title',
    render: (row) => {
      // 类型标签：中文名 + 区分图标（未知类型回退为无图标纯文本）
      const typeIcon = TASK_TYPE_ICONS[row.type]
      return h('div', { style: 'line-height:1.4' }, [
        h(
          NTag,
          { size: 'small', type: taskTypeTagColor(row.type) },
          {
            ...(typeIcon ? { icon: () => h(NIcon, { component: typeIcon }) } : {}),
            default: () => row.title,
          },
        ),
        row.detail
          ? h(
              NText,
              { depth: 3, style: 'font-size:12px;display:block;margin-top:6px' },
              { default: () => row.detail },
            )
          : null,
      ])
    },
  },
  {
    title: '状态',
    key: 'status',
    width: 90,
    render: (row) =>
      h(
        NTag,
        { type: taskStatusType(row.status), size: 'small' },
        { default: () => TASK_STATUS_LABELS[row.status] || row.status },
      ),
  },
  { title: '进度', key: 'progress', width: 160, render: renderProgress },
  { title: '完成', key: 'count', width: 100, render: renderCount },
  {
    title: '预计剩余',
    key: 'eta',
    width: 110,
    render: (row) => {
      const eta = predictEta(row)
      return eta
        ? h(NText, { depth: 2 }, { default: () => eta })
        : h(NText, { depth: 3 }, { default: () => '—' })
    },
  },
  {
    title: '创建时间',
    key: 'created_at',
    width: 170,
    render: (row) => h(NText, { depth: 2 }, { default: () => formatDate(row.created_at) }),
  },
  {
    title: '操作',
    key: 'actions',
    width: 140,
    render: (row) =>
      h('div', { style: 'display:flex;gap:8px' }, [
        row.status === 'pending'
          ? h(
              NButton,
              {
                size: 'small',
                quaternary: true,
                type: 'warning',
                onClick: () => emit('cancel', row),
              },
              { default: () => '取消' },
            )
          : null,
        row.source === 'scraper'
          ? h(
              NPopconfirm,
              { onPositiveClick: () => emit('delete', row) },
              {
                trigger: () =>
                  h(
                    NButton,
                    { size: 'small', quaternary: true, type: 'error' },
                    { default: () => '删除' },
                  ),
                default: () => '确定删除该采集任务？',
              },
            )
          : null,
      ]),
  },
]
</script>

<template>
  <n-data-table
    :columns="columns"
    :data="tasks"
    :loading="loading"
    :bordered="false"
    :single-line="false"
  />
</template>
