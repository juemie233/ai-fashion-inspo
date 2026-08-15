<script setup lang="ts">
/** 采集管理页：采集任务创建、日志查看、结果预览、源配置。 */

import { h, ref, watch, onMounted, onUnmounted } from 'vue'
import { NButton, NPopconfirm, NTag } from 'naive-ui'
import {
  useScraperTasks,
  STATUS_LABELS,
} from '@/composables/useScraperTasks'
import { useScraperLog } from '@/composables/useScraperLog'
import { useScraperFunnel } from '@/composables/useScraperFunnel'
import { useScraperResults } from '@/composables/useScraperResults'
import { useScraperConfig } from '@/composables/useScraperConfig'
import ScraperTaskForm from '@/components/scraper/ScraperTaskForm.vue'
import ScraperTaskTable from '@/components/scraper/ScraperTaskTable.vue'
import ScraperLogViewer from '@/components/scraper/ScraperLogViewer.vue'
import ScraperFunnelModal from '@/components/scraper/ScraperFunnelModal.vue'
import ScraperResultsPanel from '@/components/scraper/ScraperResultsPanel.vue'
import ScraperConfigTab from '@/components/scraper/ScraperConfigTab.vue'
import type { ScraperTask } from '@/types/scraper'

/** 当前激活的页签：tasks 采集任务 / config 源配置 */
const activeTab = ref<'tasks' | 'config'>((localStorage.getItem('scraper-active-tab') as 'tasks' | 'config') || 'tasks')
watch(activeTab, (v) => { localStorage.setItem('scraper-active-tab', v) })

// ===== 各业务域 composable =====
const {
  sources, tasks, tombstoneCount, cookieStatuses, defaultMaxCount,
  taskFilterStatus, taskSort, taskPage, deletingTask, clearing, retrying, retryingTask,
  taskStats, hasFailedTasks,
  loadAll, refreshTasks, onFilterChange,
  cancelTask, deleteSingleTask, clearAllTasks, retryFailedTasks, retrySingleTask,
  startPollIfNeeded, stopPoll, copyText,
  statusType, platformName, formatDate, parseKeywords, getTaskDuration,
} = useScraperTasks()

const {
  logTaskId, logContent, logLoading, viewLog, closeLog,
} = useScraperLog()

const {
  funnelTaskId, funnelData, funnelOpen, viewFunnel,
} = useScraperFunnel()

const {
  resultsTaskId, resultsItems, resultsTotal, resultsLoading, selectedIds, deletingResults,
  viewResults, toggleSelect, selectAll, deleteSelected,
} = useScraperResults({ refreshTasks })

const {
  showTombstone, showingCookieImport, cookiePlatform, cookieJsonInput, importCookie,
} = useScraperConfig()

/** 任务创建成功后：刷新列表并启动轮询 */
function onTaskCreated() {
  refreshTasks()
  startPollIfNeeded()
}

/** 点击 Cookie 导入：执行导入，成功则刷新全量数据（来源/任务/Cookie 状态） */
async function onCookieImport() {
  const ok = await importCookie()
  if (ok) loadAll()
}

// ── 表格列 ──
// 注意：必须用函数而非 computed，否则 render 闭包里的 ref 变化不会触发表格重渲染，
// 导致「结果/日志/漏斗」按钮文本与删除 loading 态不更新。
function getTableColumns() {
  return [
    { title: '平台', key: 'platform', width: 80, render: (r: ScraperTask) => platformName(r.platform) },
    { title: '关键词', key: 'config', width: 160, ellipsis: { tooltip: true }, render: (r: ScraperTask) => parseKeywords(r.config) },
    { title: '状态', key: 'status', width: 80, render: (r: ScraperTask) => h(NTag, { type: statusType(r.status), size: 'small' }, STATUS_LABELS[r.status] || r.status) },
    { title: '发现', key: 'items_found', width: 55 },
    { title: '新增', key: 'items_added', width: 55 },
    { title: '耗时', key: 'duration', width: 70, render: (r: ScraperTask) => getTaskDuration(r) },
    { title: '错误', key: 'error', width: 140, ellipsis: { tooltip: true }, render: (r: ScraperTask) => r.error ? h('span', { style: { color: '#d03050', cursor: 'pointer', textDecoration: 'underline', fontSize: '12px' }, title: r.error, onClick: () => copyText(r.error!) }, r.error.length > 25 ? r.error.slice(0, 25) + '…' : r.error) : '-' },
    { title: '时间', key: 'created_at', width: 150, render: (r: ScraperTask) => formatDate(r.created_at) },
    { title: '操作', key: 'actions', width: 210, render: (r: ScraperTask) => {
      const btns: any[] = []
      if (r.items_added > 0) btns.push(h(NButton, { size: 'tiny', type: resultsTaskId.value === r.id ? 'warning' : 'primary', ghost: true, onClick: () => viewResults(r.id) }, resultsTaskId.value === r.id ? '收起' : '结果'))
      btns.push(h(NButton, { size: 'tiny', onClick: () => viewLog(r.id) }, logTaskId.value === r.id ? '关闭日志' : '日志'))
      if (r.diagnostics) btns.push(h(NButton, { size: 'tiny', type: funnelTaskId.value === r.id ? 'info' : 'default', ghost: true, onClick: () => viewFunnel(r) }, funnelTaskId.value === r.id ? '关闭漏斗' : '漏斗'))
      if (r.status === 'pending' || r.status === 'running') btns.push(h(NButton, { size: 'tiny', type: 'warning', ghost: true, onClick: () => cancelTask(r.id) }, '取消'))
      if (r.status === 'failed') btns.push(h(NButton, { size: 'tiny', type: 'warning', ghost: true, loading: retryingTask.value === r.id, onClick: () => retrySingleTask(r.id) }, '续采'))
      btns.push(h(NPopconfirm, { onPositiveClick: () => deleteSingleTask(r.id) }, { trigger: () => h(NButton, { size: 'tiny', type: 'error', ghost: true, loading: deletingTask.value === r.id }, '删除'), default: () => '确定删除此记录？' }))
      return h('span', { style: { display: 'flex', gap: '4px', flexWrap: 'wrap' } }, btns)
    } },
  ]
}

function expandedRowRender(row: ScraperTask) {
  let configText = ''
  if (row.config) {
    try {
      configText = JSON.stringify(JSON.parse(row.config), null, 2)
    } catch {
      configText = row.config  // 历史脏数据无法解析时展示原文
    }
  }
  return h('div', { style: { padding: '12px 24px', maxWidth: '700px' } }, [
    configText ? h('div', { style: { marginBottom: '8px' } }, [h('span', { style: { color: '#999', fontSize: '12px' } }, '配置：'), h('pre', { style: { margin: '4px 0', fontSize: '12px', whiteSpace: 'pre-wrap' } }, configText)]) : null,
    row.error ? h('div', [h('span', { style: { color: '#d03050', fontSize: '12px' } }, '错误：'), h('pre', { style: { margin: '4px 0', fontSize: '12px', color: '#d03050', whiteSpace: 'pre-wrap', background: '#fef0f0', padding: '8px', borderRadius: '4px' } }, row.error)]) : null,
  ])
}

onMounted(() => { loadAll(); startPollIfNeeded() })
onUnmounted(() => { stopPoll() })
</script>

<template>
<div class="scraper-page">
<h2>采集管理</h2>
<p class="subtitle">自动化采集小红书和抖音的穿搭内容</p>

<n-tabs v-model:value="activeTab" type="line">
  <!-- 采集任务 Tab -->
  <n-tab-pane name="tasks" tab="采集任务">

    <ScraperTaskForm :default-max-count="defaultMaxCount" @created="onTaskCreated" />

    <ScraperTaskTable
      :tasks="tasks"
      :columns="getTableColumns()"
      :expanded-row-render="expandedRowRender"
      :stats="taskStats"
      :has-failed="hasFailedTasks"
      :retrying="retrying"
      :clearing="clearing"
      v-model:filter-status="taskFilterStatus"
      v-model:sort="taskSort"
      @filter-change="onFilterChange"
      @sort-change="() => { taskPage = 1; refreshTasks() }"
      @retry-failed="retryFailedTasks"
      @clear-all="clearAllTasks"
    >
      <template #extra>
        <!-- 日志查看器 -->
        <ScraperLogViewer v-if="logTaskId !== null" :task-id="logTaskId" :content="logContent" :loading="logLoading" @close="closeLog" />

        <!-- 漏斗视图弹窗 -->
        <ScraperFunnelModal v-model:show="funnelOpen" :task-id="funnelTaskId" :data="funnelData" />

        <!-- 结果预览 -->
        <ScraperResultsPanel
          v-if="resultsTaskId !== null"
          :items="resultsItems"
          :total="resultsTotal"
          :loading="resultsLoading"
          :selected-ids="selectedIds"
          :deleting="deletingResults"
          @select-all="selectAll"
          @toggle-select="toggleSelect"
          @delete-selected="deleteSelected"
        />
      </template>
    </ScraperTaskTable>

  </n-tab-pane>

  <!-- 源配置 Tab -->
  <n-tab-pane name="config" tab="源配置">
    <ScraperConfigTab
      :sources="sources"
      :tombstone-count="tombstoneCount"
      :cookie-statuses="cookieStatuses"
      v-model:show-tombstone="showTombstone"
      v-model:show-cookie-import="showingCookieImport"
      v-model:cookie-platform="cookiePlatform"
      v-model:cookie-json-input="cookieJsonInput"
      @import-cookie="onCookieImport"
    />
  </n-tab-pane>
</n-tabs>
</div>
</template>

<style scoped>
.scraper-page{max-width:1200px;margin:0 auto}
.subtitle{color:#999;margin-bottom:16px}
</style>
