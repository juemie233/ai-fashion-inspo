<script setup lang="ts">
/** 标签分析面板：分析队列、历史记录、分析详情与结果对比。 */

import { onMounted, onUnmounted, ref } from 'vue'
import AnalysisStatsCard from '@/components/model/AnalysisStatsCard.vue'
import AnalysisQueueOverview from '@/components/model/AnalysisQueueOverview.vue'
import AnalysisHistoryCard from '@/components/model/AnalysisHistoryCard.vue'
import AnalysisDetailModal from '@/components/model/AnalysisDetailModal.vue'
import AnalysisCompareModal from '@/components/model/AnalysisCompareModal.vue'
import ImageLightbox from '@/components/inspiration/ImageLightbox.vue'
import { useAnalysisQueue } from '@/composables/useAnalysisQueue'
import { useAnalysisHistory } from '@/composables/useAnalysisHistory'
import { useAnalysisDetail } from '@/composables/useAnalysisDetail'
import { useAnalysisCompare } from '@/composables/useAnalysisCompare'

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
  queueStats, activeAnalyses, batchAnalyzing, batchTask, pendingQueue, queuePaused,
  triggerBatchAnalyze, cancelBatchTask, retryAnalysis, togglePauseQueue, cancelQueueItem,
} = queueApi

const {
  history, historyTotal, historyPage, historyPageSize, historyFilter, historyModelFilter,
  historySearchId, historyLoading, selectedHistoryIds, historyModelNames,
  clearingFailed, retryingAll, loadHistory, filterHistory, filterByModel, searchById,
  onHistoryPageChange, batchDeleteHistory, batchRetryHistory, toggleSelectHistory, selectAllHistory,
  deleteLog, deleteInspirationFromHistory, retryAllFailed, deleteAllFailed,
} = historyApi

const { detailVisible, detailLoading, currentDetail, viewDetail } = detailApi
const { compareVisible, compareLoading, compareData, viewCompare } = compareApi

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

    <!-- 分析历史 -->
    <AnalysisHistoryCard
      v-model:history-filter="historyFilter"
      v-model:history-model-filter="historyModelFilter"
      v-model:history-search-id="historySearchId"
      :history="history"
      :history-total="historyTotal"
      :history-page="historyPage"
      :history-page-size="historyPageSize"
      :history-loading="historyLoading"
      :selected-history-ids="selectedHistoryIds"
      :history-model-names="historyModelNames"
      :clearing-failed="clearingFailed"
      :retrying-all="retryingAll"
      :queue-failed-count="queueStats.failed"
      @filter-history="filterHistory"
      @filter-by-model="filterByModel"
      @search-by-id="searchById"
      @toggle-select="toggleSelectHistory"
      @select-all="selectAllHistory"
      @clear-selection="selectedHistoryIds = new Set()"
      @batch-delete="batchDeleteHistory"
      @batch-retry="batchRetryHistory"
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
    <AnalysisDetailModal v-model:visible="detailVisible" :loading="detailLoading" :detail="currentDetail" />

    <!-- 分析结果对比弹窗 -->
    <AnalysisCompareModal v-model:visible="compareVisible" :loading="compareLoading" :data="compareData" />

    <!-- 大图灯箱：点击历史缩略图全屏浏览（组件发出 close 事件，需显式监听关闭） -->
    <ImageLightbox :show="lightboxVisible" :image-path="lightboxImage" @close="lightboxVisible = false" />
  </div>
</template>
