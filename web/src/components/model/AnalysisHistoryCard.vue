<script setup lang="ts">
/** 分析历史卡片：筛选、批量操作、历史表格与分页。 */

import { h, computed, ref, onBeforeUnmount } from 'vue'
import { NTag, NButton, NIcon, NPopconfirm, NPopover, useMessage, type DataTableColumns } from 'naive-ui'
import { GitCompareOutline, RefreshOutline, TrashBinOutline, TrashOutline } from '@vicons/ionicons5'
import { getFileUrl } from '@/api/inspirations'
import { formatMs, formatDate } from '@/utils/format'
import { copyToClipboard } from '@/utils/clipboard'
import type { HistoryItem } from '@/types/analysis'

const message = useMessage()

const props = defineProps<{
  history: HistoryItem[]
  historyTotal: number
  historyPage: number
  historyPageSize: number
  historyFilter: string | null
  historyModelFilter: string | null
  historySearchId: string
  historyStartDate: number | null
  historyEndDate: number | null
  historySortBy: string | null
  historyLoading: boolean
  selectedHistoryIds: Set<number>
  historyModelNames: string[]
  clearingFailed: boolean
  retryingAll: boolean
  queueFailedCount: number
}>()

const emit = defineEmits<{
  (e: 'update:historyFilter', value: string | null): void
  (e: 'filterHistory', value: string | null): void
  (e: 'update:historyModelFilter', value: string | null): void
  (e: 'filterByModel', value: string | null): void
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
  (e: 'viewDetail', logId: number): void
  (e: 'viewCompare', inspirationId: string): void
  (e: 'previewImage', imagePath: string): void
  (e: 'retryAnalysis', inspirationId: string): void
  (e: 'deleteLog', logId: number): void
  (e: 'deleteInspiration', inspirationId: string): void
  (e: 'updatePage', page: number): void
  (e: 'retryAllFailed'): void
  (e: 'deleteAllFailed'): void
  (e: 'refresh'): void
}>()

/** 复制文本到剪贴板（复用 utils/clipboard 实现） */
async function copyText(text: string) {
  const ok = await copyToClipboard(text)
  if (ok) {
    message.success('已复制到剪贴板')
  } else {
    message.error('复制失败')
  }
}

/** 状态筛选变化：同步 v-model 并触发加载 */
function onFilterUpdate(value: string | null) {
  emit('update:historyFilter', value)
  emit('filterHistory', value)
}

/** 模型筛选变化：同步 v-model 并触发加载 */
function onModelFilterUpdate(value: string | null) {
  emit('update:historyModelFilter', value)
  emit('filterByModel', value)
}

/** 搜索关键词变化：仅同步 v-model */
function onSearchUpdate(value: string) {
  emit('update:historySearchId', value)
}

/** 耗时排序选项 */
const sortOptions = [
  { label: '耗时升序', value: 'time_asc' },
  { label: '耗时降序', value: 'time_desc' },
]

/** 日期范围变化：同步父组件并触发加载 */
function onDateRangeChange(value: [number, number] | null) {
  if (value) emit('filterByDate', value[0], value[1])
  else emit('filterByDate', null, null)
}

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

/** 历史表格列定义 */
const columns = computed<DataTableColumns<HistoryItem>>(() => [
  {
    title: () => h('input', {
      type: 'checkbox',
      checked: props.selectedHistoryIds.size === props.history.length && props.history.length > 0,
      onClick: () => emit('selectAll'),
    }),
    key: '_check',
    width: 36,
    render: (row: HistoryItem) => h('input', {
      type: 'checkbox',
      checked: props.selectedHistoryIds.has(row.id),
      onClick: () => emit('toggleSelect', row.id),
    }),
  },
  {
    title: '预览',
    key: 'thumbnail',
    width: 70,
    render: (row: HistoryItem) => {
      const thumb = row.thumbnail_path || row.file_path
      if (!thumb) return '-'
      const full = previewImagePath(row)
      // 悬停快速预览：固定居中浮层（不再用 popover 跟随缩略图，避免大图超出屏幕）；
      // 点击缩略图打开全屏灯箱动态浏览
      return h('img', {
        src: getFileUrl(thumb),
        title: '悬停快速预览，点击全屏浏览',
        style: 'width:48px;height:72px;object-fit:cover;border-radius:4px;cursor:zoom-in;display:block',
        onMouseenter: () => startHoverPreview(full),
        onMouseleave: clearHoverPreview,
        onClick: () => {
          if (full) emit('previewImage', full)
        },
      })
    },
  },
  {
    title: '模型',
    key: 'model_name',
    width: 64,
    render: (row: HistoryItem) => h(NPopover, {
      trigger: 'hover',
      placement: 'top-start',
      style: { maxWidth: '360px' },
    }, {
      // 单元格内最多显示约 3 个字符，其余省略；完整模型名悬停气泡阅读
      trigger: () => h('span', {
        style: 'display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:3.6em;cursor:help',
      }, row.model_name),
      default: () => h('span', { style: 'font-size:12px;word-break:break-all' }, row.model_name),
    }),
  },
  {
    title: '状态',
    key: 'status',
    width: 70,
    render: (row: HistoryItem) => h(NTag, { type: row.status === 'success' ? 'success' : 'error', size: 'small' }, row.status === 'success' ? '成功' : '失败'),
  },
  {
    title: '提取标签',
    key: 'tags',
    width: 180,
    render: (row: HistoryItem) => {
      const tags = row.tags || []
      if (tags.length === 0) return '-'
      const shown = tags.slice(0, 4)
      const more = tags.length > 4 ? ` +${tags.length - 4}` : ''
      return h('span', { style: 'display:flex;flex-wrap:wrap;gap:2px' }, [
        ...shown.map(t => h(NTag, { key: t.name, size: 'tiny', bordered: false }, t.name)),
        more ? h('span', { style: 'font-size:11px;color:#999' }, more) : null,
      ])
    },
  },
  {
    title: '失败原因',
    key: 'error',
    width: 76,
    render: (row: HistoryItem) => {
      const err = row.error
      return err
        ? h(NPopover, {
          trigger: 'hover',
          placement: 'top-start',
          style: { maxWidth: '480px' },
        }, {
          // 单元格内最多显示 3 个字符，其余省略；完整内容悬停气泡阅读
          trigger: () => h('span', {
            style: 'font-size:12px;color:#ef4444;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:3.6em;cursor:help',
          }, err),
          default: () => h('div', { style: 'display:flex;flex-direction:column;gap:6px' }, [
            h('div', {
              style: 'font-size:12px;line-height:1.6;white-space:pre-wrap;word-break:break-all;max-height:280px;overflow:auto',
            }, err),
            h('div', { style: 'display:flex;justify-content:flex-end' }, [
              h(NButton, { size: 'tiny', quaternary: true, onClick: () => copyText(err) }, '复制'),
            ]),
          ]),
        })
        : h('span', { style: 'font-size:12px;color:#999' }, '-')
    },
  },
  {
    title: '耗时',
    key: 'time',
    width: 80,
    render: (row: HistoryItem) => formatMs(row.processing_time_ms),
  },
  {
    title: '时间',
    key: 'created_at',
    width: 160,
    render: (row: HistoryItem) => formatDate(row.created_at),
  },
  {
    title: '操作',
    key: 'actions',
    width: 200,
    render: (row: HistoryItem) => h('span', { style: 'display:flex;gap:4px;align-items:center' }, [
      h(NButton, { size: 'tiny', onClick: () => emit('viewDetail', row.id) }, row.status === 'success' ? '详情' : '原始输出'),
      row.status === 'error'
        ? h(NButton, {
          size: 'tiny',
          secondary: true,
          title: '重试分析',
          onClick: () => emit('retryAnalysis', row.inspiration_id),
        }, { icon: () => h(NIcon, { component: RefreshOutline }) })
        : null,
      h(NButton, {
        size: 'tiny',
        secondary: true,
        title: '对比分析结果',
        onClick: () => emit('viewCompare', row.inspiration_id),
      }, { icon: () => h(NIcon, { component: GitCompareOutline }) }),
      h(NPopconfirm, { onPositiveClick: () => emit('deleteLog', row.id) },
        {
          trigger: () => h(NButton, {
            size: 'tiny',
            type: 'warning',
            secondary: true,
            title: '删除分析记录',
          }, { icon: () => h(NIcon, { component: TrashOutline }) }),
          default: () => '确定删除此分析记录？此操作不可恢复。',
        },
      ),
      h(NPopconfirm, { onPositiveClick: () => emit('deleteInspiration', row.inspiration_id) },
        {
          trigger: () => h(NButton, {
            size: 'tiny',
            type: 'error',
            title: '删除素材（含图片文件，不可恢复）',
          }, { icon: () => h(NIcon, { component: TrashBinOutline }) }),
          default: () => '删除该素材？将同时删除图片文件与全部分析记录，此操作不可恢复！',
        },
      ),
    ]),
  },
])
</script>

<template>
  <n-card title="分析历史" size="small">
    <template #header-extra>
      <n-space :size="8">
        <n-button size="small" type="warning" secondary :loading="retryingAll" @click="emit('retryAllFailed')">
          一键重试失败 {{ queueFailedCount > 0 ? `(${queueFailedCount})` : '' }}
        </n-button>
        <n-popconfirm @positive-click="emit('deleteAllFailed')">
          <template #trigger>
            <n-button size="small" type="error" secondary :loading="clearingFailed">删除所有失败记录</n-button>
          </template>
          确定要删除所有失败记录吗？此操作不可恢复。
        </n-popconfirm>
        <n-button size="small" secondary @click="emit('exportCsv')">导出 CSV</n-button>
        <n-button size="small" @click="emit('refresh')" :loading="historyLoading">刷新</n-button>
      </n-space>
    </template>

    <!-- 筛选栏 -->
    <div class="history-filters">
      <n-radio-group :value="historyFilter" @update:value="onFilterUpdate" size="small">
        <n-radio-button :value="null">全部</n-radio-button>
        <n-radio-button value="success">成功</n-radio-button>
        <n-radio-button value="error">失败</n-radio-button>
      </n-radio-group>
      <n-select
        v-if="historyModelNames.length"
        :value="historyModelFilter"
        :options="[{ label: '全部模型', value: null }, ...historyModelNames.map(m => ({ label: m, value: m }))]"
        size="small"
        style="width:160px"
        @update:value="onModelFilterUpdate"
        placeholder="按模型筛选"
      />
      <n-input
        :value="historySearchId"
        size="small"
        placeholder="搜索素材 ID..."
        style="width:200px"
        clearable
        @update:value="onSearchUpdate"
        @keyup.enter="emit('searchById')"
        @clear="emit('searchById')"
      >
        <template #suffix>
          <n-button size="tiny" @click="emit('searchById')">🔍</n-button>
        </template>
      </n-input>
      <n-date-picker
        :value="historyStartDate && historyEndDate ? [historyStartDate, historyEndDate] : null"
        type="daterange"
        size="small"
        style="width:250px"
        clearable
        @update:value="onDateRangeChange"
      />
      <n-select
        :value="historySortBy"
        :options="sortOptions"
        size="small"
        style="width:130px"
        placeholder="按耗时排序"
        clearable
        @update:value="(v: string | null) => emit('sortByTime', v)"
      />
    </div>

    <!-- 批量操作栏 -->
    <div v-if="selectedHistoryIds.size > 0" class="batch-bar">
      <span>已选 {{ selectedHistoryIds.size }} 条</span>
      <n-button size="tiny" type="primary" ghost @click="emit('batchRetry')">重新分析</n-button>
      <n-popconfirm @positive-click="emit('batchDelete')">
        <template #trigger>
          <n-button size="tiny" type="error" ghost>批量删除</n-button>
        </template>
        确定删除选中的 {{ selectedHistoryIds.size }} 条记录？
      </n-popconfirm>
      <n-button size="tiny" @click="emit('clearSelection')">取消选择</n-button>
    </div>

    <n-data-table
      v-if="history.length"
      :columns="columns"
      :data="history"
      :bordered="false"
      size="small"
      :loading="historyLoading"
    />
    <n-empty v-else description="暂无分析记录" size="small" />

    <!-- 分页 -->
    <div v-if="historyTotal > historyPageSize" style="display:flex;justify-content:center;margin-top:16px">
      <n-pagination
        :page="historyPage"
        :page-size="historyPageSize"
        :item-count="historyTotal"
        @update:page="onPageChange"
        size="small"
      />
    </div>

    <!-- 悬停快速预览：fixed 居中浮层，永不超出视口；整层指针穿透，不遮挡表格点击 -->
    <Teleport to="body">
      <div v-if="hoverPreviewPath" class="hover-preview-layer">
        <div class="hover-preview-panel">
          <img :src="getFileUrl(hoverPreviewPath)" alt="悬停快速预览" />
        </div>
      </div>
    </Teleport>
  </n-card>
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

/* 悬停快速预览：固定定位 + flex 居中，图片限制在视口内，任何屏幕尺寸都不会越界 */
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
