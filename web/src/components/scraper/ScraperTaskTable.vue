<script setup lang="ts">
/** 采集任务历史：工具栏（统计/筛选/排序/重试/清空）+ 任务表格 + 空状态。
 *  底部保留 extra 插槽，供父级注入日志查看器/漏斗弹窗/结果预览面板（保持原卡片内布局）。 */

import type { ScraperTask } from '@/types/scraper'

defineProps<{
  tasks: ScraperTask[]
  columns: any[]
  expandedRowRender: any
  stats: { total: number; completed: number; failed: number; rate: number }
  hasFailed: boolean
  retrying: boolean
  clearing: boolean
  filterPlatform: string
  filterStatus: string
  sort: string
  page: number
  pageSize: number
  total: number
}>()

const emit = defineEmits<{
  (e: 'update:filterPlatform', v: string): void
  (e: 'update:filterStatus', v: string): void
  (e: 'update:sort', v: string): void
  (e: 'filter-change'): void
  (e: 'sort-change'): void
  (e: 'page-change', page: number): void
  (e: 'retry-failed'): void
  (e: 'clear-all'): void
}>()

const platformOptions = [
  { label: '全部平台', value: '' },
  { label: '小红书', value: 'xiaohongshu' },
  { label: '抖音', value: 'douyin' },
  { label: '浏览器插件', value: 'browser_extension' },
]

const filterOptions = [
  { label: '全部状态', value: '' },
  { label: '运行中', value: 'running' },
  { label: '成功', value: 'completed' },
  { label: '失败', value: 'failed' },
]

const sortOptions = [
  { label: '最新', value: 'newest' },
  { label: '最早', value: 'oldest' },
  { label: '发现最多', value: 'most_found' },
  { label: '新增最多', value: 'most_added' },
]

const guideSteps = ['在上方输入关键词，选择平台', '点击「开始采集」创建任务', '完成后可在素材库中查看结果']

function onFilterPlatformChange(v: string) {
  emit('update:filterPlatform', v)
  emit('filter-change')
}

function onFilterStatusChange(v: string) {
  emit('update:filterStatus', v)
  emit('filter-change')
}

function onSortChange(v: string) {
  emit('update:sort', v)
  emit('sort-change')
}

function onPageChange(page: number) {
  emit('page-change', page)
}
</script>

<template>
<n-card title="采集任务历史" size="small">
  <template #header-extra>
    <n-space align="center" size="small">
      <span v-if="stats.total>0" style="font-size:12px;color:#666">共 <b>{{ stats.total }}</b> · 成功 <b style="color:#18a058">{{ stats.completed }}</b> · 失败 <b style="color:#d03050">{{ stats.failed }}</b> · {{ stats.rate }}%</span>
      <n-select :value="filterPlatform" :options="platformOptions" size="tiny" style="width:110px" @update:value="onFilterPlatformChange" />
      <n-select :value="filterStatus" :options="filterOptions" size="tiny" style="width:100px" @update:value="onFilterStatusChange" />
      <n-select :value="sort" :options="sortOptions" size="tiny" style="width:100px" @update:value="onSortChange" />
      <n-button v-if="hasFailed" size="tiny" type="warning" ghost :loading="retrying" @click="emit('retry-failed')">重试失败</n-button>
      <n-popconfirm @positive-click="emit('clear-all')"><template #trigger><n-button size="tiny" :loading="clearing" type="error" ghost>清空</n-button></template>确定清空所有任务记录？</n-popconfirm>
    </n-space>
  </template>

  <n-data-table v-if="tasks.length" :columns="columns" :data="tasks" :bordered="false" :expanded-row-render="expandedRowRender" :row-key="(r: ScraperTask) => r.id" size="small" />

  <n-empty v-else description="暂无采集任务" size="medium">
    <template #extra>
      <div style="max-width:420px;margin:0 auto;text-align:left">
        <div v-for="(s, i) in guideSteps" :key="i" style="display:flex;align-items:center;gap:10px;padding:8px 0;color:#555;font-size:14px">
          <span style="width:24px;height:24px;border-radius:50%;background:#2080f0;color:#fff;font-size:12px;font-weight:bold;display:flex;align-items:center;justify-content:center;flex-shrink:0">{{ i + 1 }}</span>
          <span>{{ s }}</span>
        </div>
        <div style="margin-top:16px;padding:10px;background:#f0f9eb;border-radius:6px;color:#666;font-size:12px">💡 提示：小红书和抖音反爬严格，推荐使用<b>浏览器插件</b>一键抓取。</div>
      </div>
    </template>
  </n-empty>

  <n-pagination
    v-if="total > pageSize"
    style="margin-top:12px;justify-content:flex-end"
    :page="page"
    :page-size="pageSize"
    :item-count="total"
    :page-slot="7"
    @update:page="onPageChange"
  />

  <slot name="extra" />
</n-card>
</template>
