<script setup lang="ts">
/** 标签分析面板：组合分析、分析队列、历史记录、分析详情与结果对比。 */

import { onMounted, onUnmounted, ref } from 'vue'
import AnalysisStatsCard from '@/components/model/AnalysisStatsCard.vue'
import AnalysisQueueOverview from '@/components/model/AnalysisQueueOverview.vue'
import MultiModelAnalyzeCard from '@/components/model/MultiModelAnalyzeCard.vue'
import AnalysisHistoryCard from '@/components/model/AnalysisHistoryCard.vue'
import AnalysisDetailModal from '@/components/model/AnalysisDetailModal.vue'
import AnalysisCompareModal from '@/components/model/AnalysisCompareModal.vue'
import AnalysisCompareBatchModal from '@/components/model/AnalysisCompareBatchModal.vue'
import ImageLightbox from '@/components/inspiration/ImageLightbox.vue'
import { useAnalysisQueue } from '@/composables/useAnalysisQueue'
import { useAnalysisHistory } from '@/composables/useAnalysisHistory'
import { useAnalysisDetail } from '@/composables/useAnalysisDetail'
import { useAnalysisCompare } from '@/composables/useAnalysisCompare'
import type { MultiAnalyzeParams } from '@/types/analysis'

// 队列与历史 composable 互相依赖（轮询 / 批量操作需刷新对方数据），
// 通过闭包回调相互注入，避免循环依赖。
let queueApi: ReturnType<typeof useAnalysisQueue>
let historyApi: ReturnType<typeof useAnalysisHistory>

historyApi = useAnalysisHistory({
  loadQueue: () => queueApi.loadQueue(),
  loadActiveAnalyses: () => queueApi.loadActiveAnalyses(),
})
queueApi = useAnalysisQueue({
  loadHistory: () => historyApi.loadHistory(),
})

const detailApi = useAnalysisDetail()
const compareApi = useAnalysisCompare()

// 解构出顶层 ref，便于模板自动解包
const {
  queueStats,
  activeAnalyses,
  batchAnalyzing,
  batchTask,
  pendingQueue,
  queuePaused,
  triggerBatchAnalyze,
  cancelBatchTask,
  retryAnalysis,
  togglePauseQueue,
  cancelQueueItem,
} = queueApi

const {
  history,
  historyTotal,
  historyPage,
  historyPageSize,
  historyFilter,
  historyModelFilter,
  historyPromptFilter,
  historySearchId,
  historyStartDate,
  historyEndDate,
  historySortBy,
  historyLoading,
  selectedHistoryIds,
  historyModelNames,
  historyPromptVersions,
  clearingFailed,
  retryingAll,
  loadHistory,
  filterHistory,
  filterByModel,
  filterByPrompt,
  searchById,
  filterByDate,
  sortByTime,
  exportHistoryCsv,
  onHistoryPageChange,
  batchDeleteHistory,
  batchRetryHistory,
  toggleSelectHistory,
  selectAllHistory,
  deleteLog,
  deleteInspirationFromHistory,
  retryAllFailed,
  deleteAllFailed,
  applyLogToMaterial,
  loadPromptVersions,
} = historyApi

const { detailVisible, detailLoading, currentDetail, viewDetail } = detailApi
const {
  compareVisible,
  compareLoading,
  compareData,
  viewCompare,
  compareBatchVisible,
  compareBatchLoading,
  compareBatchData,
  viewCompareBatch,
} = compareApi

/** 组合分析提交入口：转发给队列 composable（对象格式请求体 + 任务轮询） */
function submitMultiAnalyze(params: MultiAnalyzeParams) {
  queueApi.triggerBatchAnalyze(params).then(() => {
    // 任务创建后刷新历史（组合分析默认不合并标签，结果在分析历史中查看）
    historyApi.loadHistory()
  })
}

/** 批量对比勾选的记录（需同一素材，历史卡片内已校验） */
function compareSelected() {
  viewCompareBatch([...selectedHistoryIds.value])
}

/** 大图灯箱：点击历史缩略图打开，居中全屏动态浏览（缩放/拖动/Esc 关闭） */
const lightboxVisible = ref(false)
const lightboxImage = ref('')

function openLightbox(imagePath: string) {
  lightboxImage.value = imagePath
  lightboxVisible.value = true
}

onMounted(() => {
  queueApi.loadQueue()
  historyApi.loadHistory()
  historyApi.loadModelNames()
  historyApi.loadPromptVersions()
  queueApi.startPolling()
  queueApi.resumeBatchAnalyzeTask()
})

onUnmounted(() => {
  queueApi.stopPolling()
  queueApi.stopBatchPolling()
  historyApi.abortHistoryRequest()
})
</script>

<template>
  <div>
    <!-- 统计卡片 -->
    <AnalysisStatsCard :queue-stats="queueStats" />

    <!-- 进度条 / 批量任务 / 活动分析 / 排队素材 -->
    <AnalysisQueueOverview
      :queue-stats="queueStats"
      :batch-analyzing="batchAnalyzing"
      :batch-task="batchTask"
      :active-analyses="activeAnalyses"
      :pending-queue="pendingQueue"
      :queue-paused="queuePaused"
      @analyze-all="triggerBatchAnalyze"
      @cancel-batch-task="cancelBatchTask"
      @close-batch-task="batchTask = null"
      @toggle-pause="togglePauseQueue"
      @cancel-queue-item="cancelQueueItem"
    />

    <!-- 多模型 × 多提示词组合分析 -->
    <MultiModelAnalyzeCard :submitting="batchAnalyzing" @submit="submitMultiAnalyze" />

    <!-- 分析历史 -->
    <AnalysisHistoryCard
      v-model:history-filter="historyFilter"
      v-model:history-model-filter="historyModelFilter"
      v-model:history-prompt-filter="historyPromptFilter"
      v-model:history-search-id="historySearchId"
      :history="history"
      :history-total="historyTotal"
      :history-page="historyPage"
      :history-page-size="historyPageSize"
      :history-start-date="historyStartDate"
      :history-end-date="historyEndDate"
      :history-sort-by="historySortBy"
      :history-loading="historyLoading"
      :selected-history-ids="selectedHistoryIds"
      :history-model-names="historyModelNames"
      :history-prompt-versions="historyPromptVersions"
      :clearing-failed="clearingFailed"
      :retrying-all="retryingAll"
      :queue-failed-count="queueStats.failed"
      @filter-history="filterHistory"
      @filter-by-model="filterByModel"
      @filter-by-prompt="filterByPrompt"
      @search-by-id="searchById"
      @filter-by-date="filterByDate"
      @sort-by-time="sortByTime"
      @export-csv="exportHistoryCsv"
      @toggle-select="toggleSelectHistory"
      @select-all="selectAllHistory"
      @clear-selection="selectedHistoryIds = new Set()"
      @batch-delete="batchDeleteHistory"
      @batch-retry="batchRetryHistory"
      @compare-batch="compareSelected"
      @apply-log="applyLogToMaterial"
      @view-detail="viewDetail"
      @view-compare="viewCompare"
      @preview-image="openLightbox"
      @retry-analysis="retryAnalysis"
      @delete-log="deleteLog"
      @delete-inspiration="deleteInspirationFromHistory"
      @update-page="onHistoryPageChange"
      @retry-all-failed="retryAllFailed"
      @delete-all-failed="deleteAllFailed"
      @refresh="loadHistory"
    />

    <!-- 分析详情弹窗 -->
    <AnalysisDetailModal
      v-model:visible="detailVisible"
      :loading="detailLoading"
      :detail="currentDetail"
    />

    <!-- 分析结果对比弹窗（按素材全量） -->
    <AnalysisCompareModal
      v-model:visible="compareVisible"
      :loading="compareLoading"
      :data="compareData"
    />

    <!-- 分析记录批量对比弹窗（按勾选记录） -->
    <AnalysisCompareBatchModal
      v-model:visible="compareBatchVisible"
      :loading="compareBatchLoading"
      :data="compareBatchData"
    />

    <!-- 大图灯箱：点击历史缩略图全屏浏览（组件发出 close 事件，需显式监听关闭） -->
    <ImageLightbox
      :show="lightboxVisible"
      :image-path="lightboxImage"
      @close="lightboxVisible = false"
    />
  </div>
</template>
