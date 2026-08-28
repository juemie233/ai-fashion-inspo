<script setup lang="ts">
/** 任务列表：统一展示任务队列与采集任务。 */

import { h } from 'vue'
import {
  Tag,
  Progress,
  Spin,
  Button,
  Popconfirm,
  TypographyText,
  type TableColumnData,
} from '@arco-design/web-vue'
import StatusTag from '@/components/common/StatusTag.vue'
import type { UnifiedTask } from '@/types/task'
import { TASK_TYPE_ICONS, taskTypeTagColor, predictEta } from '@/utils/taskLabel'
import { formatDate, renderTimeCell } from '@/utils/format'

defineProps<{ tasks: UnifiedTask[]; loading: boolean }>()
const emit = defineEmits<{
  cancel: [t: UnifiedTask]
  delete: [t: UnifiedTask]
  pause: [t: UnifiedTask]
  resume: [t: UnifiedTask]
}>()

/** 进度列：队列任务显示百分比进度条，采集任务运行中显示加载态 */
function renderProgress(row: UnifiedTask) {
  if (row.progress >= 0) {
    return h(Progress, {
      type: 'line',
      percent: row.progress / 100,
      strokeWidth: 8,
      style: 'width:140px',
    })
  }
  if (row.status === 'running') {
    return h('div', { style: 'display:flex;align-items:center;gap:6px' }, [
      h(Spin, { size: 14 }),
      h(TypographyText, { type: 'secondary' }, { default: () => '运行中' }),
    ])
  }
  return h(TypographyText, { type: 'secondary' }, { default: () => '—' })
}

/** 完成数 / 总数列 */
function renderCount(row: UnifiedTask) {
  const text = row.total > 0 ? `${row.done} / ${row.total}` : row.done > 0 ? `${row.done}` : '—'
  return h(TypographyText, { type: 'secondary' }, { default: () => text })
}

const columns: TableColumnData[] = [
  {
    title: '任务ID',
    dataIndex: 'id',
    width: 80,
    render: ({ record }) => {
      const row = record as UnifiedTask
      return h(TypographyText, { type: 'secondary' }, { default: () => String(row.id) })
    },
  },
  {
    title: '任务',
    dataIndex: 'title',
    render: ({ record }) => {
      const row = record as UnifiedTask
      // 类型标签：中文名 + 区分图标（未知类型回退为无图标纯文本）
      const typeIcon = TASK_TYPE_ICONS[row.type]
      return h('div', { style: 'line-height:1.4' }, [
        h(
          Tag,
          { size: 'small', color: taskTypeTagColor(row.type) },
          {
            default: () =>
              h('span', { style: 'display:inline-flex;align-items:center;gap:4px' }, [
                typeIcon ? h(typeIcon, { size: 14 }) : null,
                row.title,
              ]),
          },
        ),
        row.detail
          ? h(
              TypographyText,
              { type: 'secondary', style: 'font-size:12px;display:block;margin-top:6px' },
              { default: () => row.detail },
            )
          : null,
      ])
    },
  },
  {
    title: '状态',
    dataIndex: 'status',
    width: 90,
    render: ({ record }) => h(StatusTag, { status: (record as UnifiedTask).status }),
  },
  {
    title: '进度',
    dataIndex: 'progress',
    width: 160,
    render: ({ record }) => renderProgress(record as UnifiedTask),
  },
  {
    title: '完成',
    dataIndex: 'count',
    width: 100,
    render: ({ record }) => renderCount(record as UnifiedTask),
  },
  {
    title: '预计剩余',
    dataIndex: 'eta',
    width: 110,
    render: ({ record }) => {
      const row = record as UnifiedTask
      const eta = predictEta(row)
      return eta
        ? h(TypographyText, { type: 'secondary' }, { default: () => eta })
        : h(TypographyText, { type: 'secondary' }, { default: () => '—' })
    },
  },
  {
    title: '创建时间',
    dataIndex: 'created_at',
    width: 170,
    render: ({ record }) => {
      const row = record as UnifiedTask
      // 用 renderTimeCell 保证单行；secondary 灰色等价 TypographyText type="secondary"
      return renderTimeCell(formatDate(row.created_at), { style: 'color: var(--color-text-2)' })
    },
  },
  {
    title: '操作',
    dataIndex: 'actions',
    width: 180,
    render: ({ record }) => {
      const row = record as UnifiedTask
      return h('div', { style: 'display:flex;gap:8px;flex-wrap:wrap' }, [
        // 标签网络分析任务：运行中可暂停、已暂停可恢复（后端断点续算）
        row.type === 'tag_network_analyze' && row.status === 'running'
          ? h(
              Button,
              {
                size: 'small',
                type: 'outline',
                status: 'warning',
                onClick: () => emit('pause', row),
              },
              { default: () => '暂停' },
            )
          : null,
        row.type === 'tag_network_analyze' && row.status === 'paused'
          ? h(
              Button,
              {
                size: 'small',
                type: 'outline',
                status: 'success',
                onClick: () => emit('resume', row),
              },
              { default: () => '恢复' },
            )
          : null,
        row.status === 'pending'
          ? h(
              Popconfirm,
              {
                // 队列任务 pending 取消 = 物理删除不可恢复；采集任务取消仅标记 cancelled
                content:
                  row.source === 'queue'
                    ? '该任务将被删除且不可恢复，确定删除？'
                    : '确定取消该采集任务？',
                onOk: () => emit('cancel', row),
              },
              {
                default: () =>
                  h(
                    Button,
                    {
                      size: 'small',
                      type: 'text',
                      status: row.source === 'queue' ? 'danger' : 'warning',
                    },
                    { default: () => (row.source === 'queue' ? '删除' : '取消') },
                  ),
              },
            )
          : null,
        row.source === 'scraper'
          ? h(
              Popconfirm,
              { content: '确定删除该采集任务？', onOk: () => emit('delete', row) },
              {
                default: () =>
                  h(
                    Button,
                    { size: 'small', type: 'text', status: 'danger' },
                    { default: () => '删除' },
                  ),
              },
            )
          : null,
        // 队列任务记录清理：终态（cancelled/success/failed）可删；运行中任务
        // 仅当执行进程已停止（心跳超时，如停电/崩溃遗留的僵尸任务）可删——
        // 由后端以心跳判定裁决，正常执行中的任务会返回 400 拒绝
        row.source === 'queue' &&
        (row.status === 'running' ||
          row.status === 'cancelled' ||
          row.status === 'success' ||
          row.status === 'failed')
          ? h(
              Popconfirm,
              {
                content:
                  row.status === 'running'
                    ? '任务处于运行中：仅当执行进程已停止（心跳超时）时可删除，正在执行的任务将被拒绝，确定删除？'
                    : '确定删除该任务历史记录？',
                onOk: () => emit('delete', row),
              },
              {
                default: () =>
                  h(
                    Button,
                    { size: 'small', type: 'text', status: 'danger' },
                    { default: () => '删除任务' },
                  ),
              },
            )
          : null,
      ])
    },
  },
]
</script>

<template>
  <a-table
    :columns="columns"
    :data="tasks"
    :loading="loading"
    :bordered="false"
    :pagination="false"
  />
</template>
