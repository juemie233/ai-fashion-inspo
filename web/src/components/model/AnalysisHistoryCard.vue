<script setup lang="ts">
/** 分析历史卡片：筛选、批量操作、历史表格与分页。 */

import { h, computed, ref, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import {
  Tag,
  Button,
  Popconfirm,
  Tooltip,
  Message,
  type TableColumnData,
} from '@arco-design/web-vue'
import { IconRefresh, IconDelete, IconSync, IconEye } from '@arco-design/web-vue/es/icon'
import { getFileUrl, TRASH_REASON_OPTIONS, type TrashReason } from '@/api/inspirations'
import { formatMs, formatDate, renderTimeCell } from '@/utils/format'
import { copyToClipboard } from '@/utils/clipboard'
import { sortAnalysisTags } from '@/utils/tagSort'
import type { HistoryItem } from '@/types/analysis'

const router = useRouter()

const props = defineProps<{
  history: HistoryItem[]
  historyTotal: number
  historyPage: number
  historyPageSize: number
  historyFilter: string | null
  historyModelFilter: string | null
  historyPromptFilter: string | null
  historySearchId: string
  historyStartDate: number | null
  historyEndDate: number | null
  historySortBy: string | null
  historyLoading: boolean
  selectedHistoryIds: Set<number>
  historyModelNames: string[]
  historyPromptVersions: Array<{ prompt_version: string; count: number }>
  clearingFailed: boolean
  retryingAll: boolean
  queueFailedCount: number
}>()

const emit = defineEmits<{
  (e: 'update:historyFilter', value: string | null): void
  (e: 'filterHistory', value: string | null): void
  (e: 'update:historyModelFilter', value: string | null): void
  (e: 'filterByModel', value: string | null): void
  (e: 'update:historyPromptFilter', value: string | null): void
  (e: 'filterByPrompt', value: string | null): void
  (e: 'update:historySearchId', value: string): void
  (e: 'searchById'): void
  (e: 'filterByDate', start: number | null, end: number | null): void
  (e: 'sortByTime', value: string | null): void
  (e: 'exportCsv'): void
  (e: 'toggleSelect', logId: number): void
  (e: 'selectAll'): void
  (e: 'clearSelection'): void
  (e: 'batchDelete'): void
  (e: 'batchRetry'): void
  (e: 'compareBatch'): void
  (e: 'applyLog', logId: number): void
  (e: 'viewDetail', logId: number): void
  (e: 'viewCompare', inspirationId: string): void
  (e: 'previewImage', imagePath: string): void
  (e: 'retryAnalysis', inspirationId: string): void
  (e: 'deleteLog', logId: number): void
  (e: 'deleteInspiration', inspirationId: string, reason: TrashReason): void
  (e: 'updatePage', page: number): void
  (e: 'retryAllFailed'): void
  (e: 'deleteAllFailed'): void
  (e: 'refresh'): void
}>()

/** 复制文本到剪贴板（复用 utils/clipboard 实现） */
async function copyText(text: string) {
  const ok = await copyToClipboard(text)
  if (ok) {
    Message.success('已复制到剪贴板')
  } else {
    Message.error('复制失败')
  }
}

/** 状态筛选变化：同步 v-model 并触发加载（空串 ↔ null 哨兵转换） */
function onFilterUpdate(v: unknown) {
  const value = (v as string) || null
  emit('update:historyFilter', value)
  emit('filterHistory', value)
}

/** 模型筛选变化：同步 v-model 并触发加载 */
function onModelFilterUpdate(v: unknown) {
  const value = (v as string) || null
  emit('update:historyModelFilter', value)
  emit('filterByModel', value)
}

/** 提示词版本筛选变化：同步 v-model 并触发加载 */
function onPromptFilterUpdate(v: unknown) {
  const value = (v as string) || null
  emit('update:historyPromptFilter', value)
  emit('filterByPrompt', value)
}

// ===== 批量对比（勾选同一素材的多条记录） =====
/** 当前勾选的记录行 */
const selectedRows = computed(() =>
  props.history.filter((row) => props.selectedHistoryIds.has(row.id)),
)

/** 勾选的记录是否满足对比条件：≥2 条且属于同一素材 */
const canCompareSelected = computed(() => {
  if (selectedRows.value.length < 2) return false
  return new Set(selectedRows.value.map((r) => r.inspiration_id)).size === 1
})

/** 对比选中记录：不满足条件时给出提示 */
function onCompareBatchClick() {
  if (selectedRows.value.length < 2) {
    Message.warning('请至少勾选 2 条记录')
    return
  }
  if (!canCompareSelected.value) {
    Message.warning('仅支持对比同一素材的分析记录，请重新勾选')
    return
  }
  emit('compareBatch')
}

/** 搜索关键词变化：仅同步 v-model */
function onSearchUpdate(v: string) {
  emit('update:historySearchId', v)
}

/** 耗时排序选项 */
const sortOptions = [
  { label: '耗时升序', value: 'time_asc' },
  { label: '耗时降序', value: 'time_desc' },
]

/**
 * 表格「提取标签」列展示的标签数量。
 * 默认显示 8 个，可通过表头数字输入框在 8~16 之间调节；超出部分折叠为 +N。
 */
const tagsVisibleCount = ref(8)

/** 日期范围变化：同步父组件并触发加载 */
function onDateRangeChange(value: unknown) {
  const arr = value as Array<string | number | Date> | undefined
  if (arr && arr.length === 2) {
    const [s, e] = arr.map((v) => (typeof v === 'number' ? v : new Date(String(v)).getTime()))
    emit('filterByDate', s, e)
  } else {
    emit('filterByDate', null, null)
  }
}

/** 日期范围绑定值（Arco date-picker 类型定义不含数组，断言收敛到单值类型） */
const dateRangeValue = computed(() =>
  props.historyStartDate && props.historyEndDate
    ? ([props.historyStartDate, props.historyEndDate] as unknown as string | number | Date)
    : undefined,
)

/** 分页变化 */
function onPageChange(page: number) {
  emit('updatePage', page)
}

// ===== 悬停快速预览（固定居中浮层，见模板尾部 Teleport） =====
/** 当前预览图片路径（null = 关闭） */
const hoverPreviewPath = ref<string | null>(null)
/** 悬停停留计时器：短暂停留才弹出预览，扫过表格时不闪烁 */
let hoverPreviewTimer: number | null = null

/** 预览用图片路径：视频素材回退到首帧缩略图，避免 <img> 加载 mp4 失败 */
function previewImagePath(row: HistoryItem): string {
  if (row.file_path && /\.(mp4|webm|mov|m4v)$/i.test(row.file_path)) {
    return row.thumbnail_path || ''
  }
  return row.file_path || row.thumbnail_path || ''
}

/** 鼠标进入缩略图：短暂停留后显示居中预览 */
function startHoverPreview(path: string) {
  clearHoverPreview()
  if (!path) return
  hoverPreviewTimer = window.setTimeout(() => {
    hoverPreviewPath.value = path
  }, 250)
}

/** 清除预览与计时器 */
function clearHoverPreview() {
  if (hoverPreviewTimer !== null) {
    window.clearTimeout(hoverPreviewTimer)
    hoverPreviewTimer = null
  }
  hoverPreviewPath.value = null
}

onBeforeUnmount(clearHoverPreview)

// ===== 删除素材（移入垃圾桶）原因弹窗 =====
/** 当前待删除的素材 ID（null = 未选择，弹窗关闭） */
const trashTargetId = ref<string | null>(null)
/** 删除原因弹窗是否打开 */
const trashModalOpen = ref(false)
/** 当前选中的删除原因（未选择时确认按钮禁用） */
const trashReason = ref<TrashReason | null>(null)

/** 打开删除素材原因弹窗（每次重新打开时重置原因选择） */
function openTrashModal(inspirationId: string) {
  trashTargetId.value = inspirationId
  trashReason.value = null
  trashModalOpen.value = true
}

/** 确认移入垃圾桶：携带所选原因触发父级删除 */
function confirmTrash() {
  if (!trashTargetId.value || !trashReason.value) return
  emit('deleteInspiration', trashTargetId.value, trashReason.value)
  trashModalOpen.value = false
}

/** 历史表格列定义 */
const columns = computed<TableColumnData[]>(() => [
  {
    title: () =>
      h('input', {
        type: 'checkbox',
        checked: props.selectedHistoryIds.size === props.history.length && props.history.length > 0,
        onClick: () => emit('selectAll'),
      }),
    dataIndex: '_check',
    width: 36,
    render: ({ record }) => {
      const row = record as HistoryItem
      return h('input', {
        type: 'checkbox',
        checked: props.selectedHistoryIds.has(row.id),
        onClick: () => emit('toggleSelect', row.id),
      })
    },
  },
  {
    title: '预览',
    dataIndex: 'thumbnail',
    width: 70,
    render: ({ record }) => {
      const row = record as HistoryItem
      const isVideo = !!row.file_path && /\.(mp4|webm|mov|m4v)$/i.test(row.file_path)
      // 视频素材只能用缩略图（file_path 是 mp4，<img> 加载必破图）；缺失时显示占位符
      const thumb = row.thumbnail_path || (isVideo ? null : row.file_path)
      if (!thumb) {
        return h(
          'div',
          {
            title: '视频素材（缩略图生成中或缺失）',
            style:
              'width:48px;height:72px;display:flex;align-items:center;justify-content:center;' +
              'border-radius:4px;background:#f2f3f5;color:#86909c;font-size:18px',
          },
          ['🎬'],
        )
      }
      const full = previewImagePath(row)
      // 缩略图：单击跳转素材详情页；悬停时右下角显示眼睛按钮，点击全屏预览
      return h(
        'div',
        {
          class: 'thumb-cell',
          style:
            'position:relative;width:48px;height:72px;cursor:pointer;border-radius:4px;overflow:hidden',
          onMouseenter: () => startHoverPreview(full),
          onMouseleave: clearHoverPreview,
          onClick: () => router.push({ name: 'detail', params: { id: row.inspiration_id } }),
        },
        [
          h('img', {
            src: getFileUrl(thumb),
            title: '点击查看素材详情',
            style: 'width:48px;height:72px;object-fit:cover;border-radius:4px;display:block',
          }),
          h(
            'span',
            {
              title: '全屏预览',
              class: 'thumb-preview-btn',
              // 点击眼睛按钮：阻止冒泡，不触发详情跳转，改为全屏预览
              onClick: (e: MouseEvent) => {
                e.stopPropagation()
                if (full) emit('previewImage', full)
              },
            },
            h(IconEye, { style: 'font-size:12px' }),
          ),
        ],
      )
    },
  },
  {
    title: '模型',
    dataIndex: 'model_name',
    width: 64,
    render: ({ record }) => {
      const row = record as HistoryItem
      return h(
        Tooltip,
        {
          position: 'tl',
          content: row.prompt_version
            ? `${row.model_name} · Prompt ${row.prompt_version}`
            : row.model_name,
        },
        {
          default: () =>
            h(
              'span',
              {
                style:
                  'display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:3.6em;cursor:help',
              },
              row.model_name,
            ),
        },
      )
    },
  },
  {
    title: '状态',
    dataIndex: 'status',
    width: 70,
    render: ({ record }) => {
      const row = record as HistoryItem
      return h(Tag, { color: row.status === 'success' ? 'green' : 'red', size: 'small' }, () =>
        row.status === 'success' ? '成功' : '失败',
      )
    },
  },
  {
    title: '提取标签',
    dataIndex: 'tags',
    width: 280,
    render: ({ record }) => {
      const row = record as HistoryItem
      // 按「风格 > 氛围 > 袜 > 鞋 > 模特表情 > 其余」优先级展示，
      // 同类内保持原有顺序；仅影响该表格的展示，不改动原始数据
      const tags = sortAnalysisTags(row.tags || [])
      if (tags.length === 0) return '-'
      const limit = tagsVisibleCount.value
      const shown = tags.slice(0, limit)
      const more = tags.length > limit ? ` +${tags.length - limit}` : ''
      return h('span', { style: 'display:flex;flex-wrap:wrap;gap:2px' }, [
        ...shown.map((t) => h(Tag, { key: t.name, size: 'small' }, () => t.name)),
        more ? h('span', { style: 'font-size:11px;color:#999' }, more) : null,
      ])
    },
  },
  {
    title: '失败原因',
    dataIndex: 'error',
    width: 76,
    render: ({ record }) => {
      const row = record as HistoryItem
      const err = row.error
      return err
        ? h(
            Tooltip,
            {
              position: 'tl',
            },
            {
              content: () =>
                h('div', { style: 'display:flex;flex-direction:column;gap:6px' }, [
                  h(
                    'div',
                    {
                      style:
                        'font-size:12px;line-height:1.6;white-space:pre-wrap;word-break:break-all;max-height:280px;overflow:auto',
                    },
                    err,
                  ),
                  h('div', { style: 'display:flex;justify-content:flex-end' }, [
                    h(
                      Button,
                      { size: 'mini', type: 'text', onClick: () => copyText(err) },
                      () => '复制',
                    ),
                  ]),
                ]),
              default: () =>
                h(
                  'span',
                  {
                    style:
                      'font-size:12px;color:#ef4444;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:3.6em;cursor:help',
                  },
                  err,
                ),
            },
          )
        : h('span', { style: 'font-size:12px;color:#999' }, '-')
    },
  },
  {
    title: '耗时',
    dataIndex: 'time',
    width: 80,
    render: ({ record }) => formatMs((record as HistoryItem).processing_time_ms),
  },
  {
    title: '时间',
    dataIndex: 'created_at',
    width: 160,
    render: ({ record }) => renderTimeCell(formatDate((record as HistoryItem).created_at)),
  },
  {
    title: '操作',
    dataIndex: 'actions',
    width: 232,
    render: ({ record }) => {
      const row = record as HistoryItem
      return h('span', { style: 'display:flex;gap:4px;align-items:center' }, [
        h(Button, { size: 'mini', onClick: () => emit('viewDetail', row.id) }, () =>
          row.status === 'success' ? '详情' : '原始输出',
        ),
        row.status === 'error'
          ? h(
              Button,
              {
                size: 'mini',
                type: 'secondary',
                title: '重试分析',
                onClick: () => emit('retryAnalysis', row.inspiration_id),
              },
              { icon: () => h(IconRefresh) },
            )
          : null,
        row.status === 'success'
          ? h(
              Button,
              {
                size: 'mini',
                type: 'secondary',
                title: '把本次分析提取的标签应用到素材（覆盖 AI 标签，保留手动标签）',
                onClick: () => emit('applyLog', row.id),
              },
              () => '应用',
            )
          : null,
        h(
          Button,
          {
            size: 'mini',
            type: 'secondary',
            title: '对比分析结果',
            onClick: () => emit('viewCompare', row.inspiration_id),
          },
          { icon: () => h(IconSync) },
        ),
        h(
          Popconfirm,
          {
            content: '确定删除此分析记录？此操作不可恢复。',
            onOk: () => emit('deleteLog', row.id),
          },
          {
            default: () =>
              h(
                Button,
                {
                  size: 'mini',
                  type: 'secondary',
                  status: 'warning',
                  title: '删除分析记录',
                },
                { icon: () => h(IconDelete) },
              ),
          },
        ),
        h(
          Button,
          {
            size: 'mini',
            status: 'danger',
            title: '删除素材（移入垃圾桶，可选择原因）',
            onClick: () => openTrashModal(row.inspiration_id),
          },
          { icon: () => h(IconDelete) },
        ),
      ])
    },
  },
])
</script>

<template>
  <a-card title="分析历史" size="small">
    <template #extra>
      <a-space :size="8">
        <a-button
          size="small"
          type="secondary"
          status="warning"
          :loading="retryingAll"
          @click="emit('retryAllFailed')"
        >
          一键重试失败 {{ queueFailedCount > 0 ? `(${queueFailedCount})` : '' }}
        </a-button>
        <a-popconfirm
          content="确定要删除所有失败记录吗？此操作不可恢复。"
          @ok="emit('deleteAllFailed')"
        >
          <a-button size="small" type="secondary" status="danger" :loading="clearingFailed"
            >删除所有失败记录</a-button
          >
        </a-popconfirm>
        <a-button size="small" type="secondary" @click="emit('exportCsv')">导出 CSV</a-button>
        <a-button size="small" @click="emit('refresh')" :loading="historyLoading">刷新</a-button>
      </a-space>
    </template>

    <!-- 筛选栏 -->
    <div class="history-filters">
      <a-radio-group
        :model-value="historyFilter ?? ''"
        type="button"
        size="small"
        @change="onFilterUpdate"
      >
        <a-radio value="">全部</a-radio>
        <a-radio value="success">成功</a-radio>
        <a-radio value="error">失败</a-radio>
      </a-radio-group>
      <a-select
        v-if="historyModelNames.length"
        :model-value="historyModelFilter ?? undefined"
        :options="historyModelNames.map((m) => ({ label: m, value: m }))"
        size="small"
        style="width: 160px"
        @change="onModelFilterUpdate"
        placeholder="按模型筛选"
        allow-clear
      />
      <a-select
        v-if="historyPromptVersions.length"
        :model-value="historyPromptFilter ?? undefined"
        :options="
          historyPromptVersions.map((v) => ({
            label: `Prompt ${v.prompt_version} (${v.count})`,
            value: v.prompt_version,
          }))
        "
        size="small"
        style="width: 190px"
        @change="onPromptFilterUpdate"
        placeholder="按提示词版本筛选"
        allow-clear
      />
      <a-input
        :model-value="historySearchId"
        size="small"
        placeholder="搜索素材 ID..."
        style="width: 200px"
        allow-clear
        @input="onSearchUpdate"
        @press-enter="emit('searchById')"
        @clear="emit('searchById')"
      >
        <template #suffix>
          <a-button size="mini" @click="emit('searchById')">🔍</a-button>
        </template>
      </a-input>
      <a-date-picker
        :model-value="dateRangeValue"
        range
        size="small"
        style="width: 250px"
        allow-clear
        @change="onDateRangeChange"
      />
      <a-select
        :model-value="historySortBy ?? undefined"
        :options="sortOptions"
        size="small"
        style="width: 130px"
        placeholder="按耗时排序"
        allow-clear
        @change="(v: unknown) => emit('sortByTime', (v as string) || null)"
      />
      <div class="tags-limit-control">
        <span class="tags-limit-label">显示标签</span>
        <a-input-number
          v-model="tagsVisibleCount"
          :min="8"
          :max="16"
          :step="1"
          size="small"
          mode="button"
          style="width: 92px"
        />
      </div>
    </div>

    <!-- 批量操作栏 -->
    <div v-if="selectedHistoryIds.size > 0" class="batch-bar">
      <span>已选 {{ selectedHistoryIds.size }} 条</span>
      <a-button
        size="mini"
        type="outline"
        :disabled="!canCompareSelected"
        @click="onCompareBatchClick"
      >
        对比选中（需同一素材）
      </a-button>
      <a-button size="mini" type="outline" @click="emit('batchRetry')">重新分析</a-button>
      <a-popconfirm
        :content="`确定删除选中的 ${selectedHistoryIds.size} 条记录？`"
        @ok="emit('batchDelete')"
      >
        <a-button size="mini" type="outline" status="danger">批量删除</a-button>
      </a-popconfirm>
      <a-button size="mini" @click="emit('clearSelection')">取消选择</a-button>
    </div>

    <a-table
      v-if="history.length"
      :columns="columns"
      :data="history"
      :bordered="false"
      size="small"
      :loading="historyLoading"
      :pagination="false"
    />
    <a-empty v-else description="暂无分析记录" />

    <!-- 分页 -->
    <div
      v-if="historyTotal > historyPageSize"
      style="display: flex; justify-content: center; margin-top: 16px"
    >
      <a-pagination
        :current="historyPage"
        :page-size="historyPageSize"
        :total="historyTotal"
        @change="onPageChange"
        size="small"
      />
    </div>

    <!-- 删除素材（移入垃圾桶）原因选择弹窗 -->
    <a-modal v-model:visible="trashModalOpen" title="删除素材" :width="420" :mask-closable="false">
      <p class="trash-reason-tip">
        请选择删除素材的原因，素材将移入垃圾桶（可在「素材管理 →
        垃圾桶」恢复），历史列表将自动刷新：
      </p>
      <a-radio-group
        :model-value="trashReason ?? undefined"
        class="trash-reason-group"
        @change="(v: unknown) => (trashReason = (v as TrashReason | undefined) ?? null)"
      >
        <a-space direction="vertical" :size="10">
          <a-radio v-for="opt in TRASH_REASON_OPTIONS" :key="opt.value" :value="opt.value">{{
            opt.label
          }}</a-radio>
        </a-space>
      </a-radio-group>
      <template #footer>
        <div class="trash-modal-footer">
          <a-button @click="trashModalOpen = false">取消</a-button>
          <a-button status="danger" :disabled="!trashReason" @click="confirmTrash">
            确认删除
          </a-button>
        </div>
      </template>
    </a-modal>

    <!-- 悬停快速预览：fixed 居中浮层，永不超出视口；整层指针穿透，不遮挡表格点击 -->
    <Teleport to="body">
      <div v-if="hoverPreviewPath" class="hover-preview-layer">
        <div class="hover-preview-panel">
          <img :src="getFileUrl(hoverPreviewPath)" alt="悬停快速预览" />
        </div>
      </div>
    </Teleport>
  </a-card>
</template>

<style scoped>
.history-filters {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.batch-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  margin-bottom: 8px;
  background: #f0f7ff;
  border: 1px solid #d0e3ff;
  border-radius: 6px;
  font-size: 13px;
}

/* 标签显示数量调节：数字输入框与说明文字同行 */
.tags-limit-control {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-left: auto;
  white-space: nowrap;
}
.tags-limit-label {
  font-size: 12px;
  color: #86909c;
}

/* 删除素材原因弹窗：说明文字与原因单选组 */
.trash-reason-tip {
  font-size: 13px;
  color: #4e5969;
  margin-bottom: 12px;
  line-height: 1.6;
}
.trash-reason-group {
  display: flex;
  flex-direction: column;
}
.trash-modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

/* 悬停快速预览：固定定位 + flex 居中，图片限制在视口内，任何屏幕尺寸都不会越界 */
.thumb-preview-btn {
  position: absolute;
  right: 2px;
  bottom: 2px;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  opacity: 0;
  transition: opacity 0.15s;
}
/* 悬停缩略图容器时显示全屏预览按钮 */
.thumb-preview-btn:hover,
.thumb-preview-btn:focus-visible {
  opacity: 1;
}
.thumb-cell:hover .thumb-preview-btn {
  opacity: 1;
}

.hover-preview-layer {
  position: fixed;
  inset: 0;
  z-index: 2500;
  display: flex;
  align-items: center;
  justify-content: center;
  /* 指针穿透：预览浮层不拦截任何鼠标事件，表格可正常点击/悬停 */
  pointer-events: none;
}

.hover-preview-panel {
  max-width: 90vw;
  max-height: 88vh;
  border-radius: 12px;
  overflow: hidden;
  background: #fff;
  box-shadow: 0 12px 48px rgba(0, 0, 0, 0.35);
  animation: hover-preview-in 0.15s ease;
}

.hover-preview-panel img {
  display: block;
  max-width: 90vw;
  max-height: 88vh;
  object-fit: contain;
}

@keyframes hover-preview-in {
  from {
    opacity: 0;
    transform: scale(0.97);
  }
  to {
    opacity: 1;
    transform: scale(1);
  }
}
</style>
