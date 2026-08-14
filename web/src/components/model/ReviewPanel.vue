<script setup lang="ts">
/** 质量审核面板：待审核/已通过/已拒绝统计 + 未通过素材管理。 */

import { ref, onMounted, onUnmounted } from 'vue'
import { useMessage } from 'naive-ui'
import apiClient from '@/api/client'
import { getFileUrl, deleteRejectedInspirations } from '@/api/inspirations'
import { formatBytes } from '@/utils/format'

const message = useMessage()

interface QualityReviewStats {
  total: number
  pending: number
  approved: number
  rejected: number
  pass_rate: number
}
const qualityReviewStats = ref<QualityReviewStats | null>(null)
const qualityReviewLoading = ref(false)
const qualityChecking = ref(false)
const rechecking = ref(false)

// 质量审核任务（数据库驱动任务队列，轮询进度）
interface ReviewTask {
  id: number
  type: string
  status: string
  progress: number
  total: number
  done: number
  result: { approved?: number; rejected?: number; pending?: number } | null
  error: string | null
  retry_count: number
  max_retries: number
  next_retry_at: string | null
  created_at: string
  updated_at: string
}
const reviewTask = ref<ReviewTask | null>(null)
let reviewPollTimer: ReturnType<typeof setTimeout> | null = null
let reviewPollSeq = 0  // 轮询代际号：stop/重启时自增，使在途请求返回后不再续排

async function loadQualityReview() {
  qualityReviewLoading.value = true
  try {
    const { data } = await apiClient.get<QualityReviewStats>('/ai/quality-stats')
    qualityReviewStats.value = data
  } catch { /* 静默 */ }
  finally { qualityReviewLoading.value = false }
}

/** 轮询审核任务状态（约 1 秒一次），完成后刷新统计与未通过列表 */
function startReviewPolling(taskId: number) {
  stopReviewPolling()
  const seq = reviewPollSeq  // 当前代际：stopReviewPolling 已自增，旧链的 seq 与之不符即失效
  let consecutiveFailures = 0  // 连续失败次数，失败时有限次重试而非直接停止
  const poll = async () => {
    if (seq !== reviewPollSeq) return  // 已被 stop/新轮询取代，不再调度
    try {
      const { data } = await apiClient.get<ReviewTask>(`/tasks/${taskId}`)
      if (seq !== reviewPollSeq) return  // 在途请求返回前已被停止，丢弃结果
      consecutiveFailures = 0
      reviewTask.value = data
      if (data.status === 'success' || data.status === 'failed' || data.status === 'cancelled') {
        stopReviewPolling()
        if (data.status === 'success') {
          const r = data.result
          message.success(`审核完成：通过 ${r?.approved ?? 0}，拒绝 ${r?.rejected ?? 0}，未判定 ${r?.pending ?? 0}`)
        } else if (data.status === 'failed') {
          message.error(`审核失败：${data.error || '未知错误'}`)
        } else {
          message.info('审核任务已取消')
        }
        loadQualityReview()
        loadRejectedItems()
        return
      }
      reviewPollTimer = setTimeout(poll, 1000)
    } catch {
      if (seq !== reviewPollSeq) return
      consecutiveFailures += 1
      if (consecutiveFailures >= 5) {
        // 连续多次失败才停止，避免后端重启/网络抖动导致任务进度卡死
        stopReviewPolling()
        message.error('获取审核任务状态多次失败，已停止轮询，请稍后手动刷新')
        return
      }
      // 有限次重试：间隔放大到 3 秒，继续续排轮询链
      reviewPollTimer = setTimeout(poll, 3000)
    }
  }
  poll()
}

function stopReviewPolling() {
  reviewPollSeq += 1  // 自增代际号，使当前轮询链失效，防止在途请求返回后重新调度
  if (reviewPollTimer) { clearTimeout(reviewPollTimer); reviewPollTimer = null }
}

/** 恢复进行中的审核任务：刷新页面后查询是否有 pending/running 的审核任务并继续轮询 */
async function resumeReviewTask() {
  try {
    const { data } = await apiClient.get<{ items: ReviewTask[] }>('/tasks', {
      params: { type: 'quality_check', size: 20 },
    })
    const active = data.items.find((t) => t.status === 'pending' || t.status === 'running')
    if (active) {
      reviewTask.value = active
      startReviewPolling(active.id)
    }
  } catch { /* 静默 */ }
}

async function triggerQualityCheck() {
  qualityChecking.value = true
  try {
    const { data } = await apiClient.post<{ message: string; count: number; task_id: number }>(
      '/ai/quality-check',
      null,
      { params: { limit: 200 } },
    )
    reviewTask.value = { id: data.task_id, type: 'quality_check', status: 'pending', progress: 0, total: data.count, done: 0, result: null, error: null, retry_count: 0, max_retries: 2, next_retry_at: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString() }
    message.success(`已提交 ${data.count} 个素材进行审核（任务 #${data.task_id}）`)
    startReviewPolling(data.task_id)
  } catch (e: any) {
    message.error(e.response?.data?.detail || '审核提交失败')
  } finally {
    qualityChecking.value = false
  }
}

async function recheckQuality() {
  rechecking.value = true
  try {
    const { data } = await apiClient.post<{ message: string; count: number; task_id: number }>(
      '/ai/quality-recheck',
    )
    reviewTask.value = { id: data.task_id, type: 'quality_check', status: 'pending', progress: 0, total: data.count, done: 0, result: null, error: null, retry_count: 0, max_retries: 2, next_retry_at: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString() }
    message.success(`已提交重新审核任务 #${data.task_id}，共 ${data.count} 个素材`)
    startReviewPolling(data.task_id)
  } catch (e: any) {
    message.error(e.response?.data?.detail || '重新审核提交失败')
  } finally {
    rechecking.value = false
  }
}

// 未通过素材列表
interface RejectedItem {
  id: string
  thumbnail_path?: string | null
  file_path: string
  quality_reason?: string | null
  source_type: string
}
const rejectedItems = ref<RejectedItem[]>([])
const rejectedTotal = ref(0)
const rejectedLoading = ref(false)
const rejectedPage = ref(1)
const rejectedPageSize = 100
let rejectedSeq = 0  // 请求序号，防止陈旧响应覆盖
const approvingRejectedIds = ref<Set<string>>(new Set())

async function loadRejectedItems(reset = true) {
  if (reset) rejectedPage.value = 1
  const page = rejectedPage.value
  rejectedLoading.value = true
  const seq = ++rejectedSeq
  try {
    const { data } = await apiClient.get<{ items: RejectedItem[]; total: number }>('/inspirations', {
      params: { quality_status: 'rejected', size: rejectedPageSize, page, sort: 'newest' },
    })
    if (seq !== rejectedSeq) return  // 忽略过期响应
    rejectedItems.value = reset ? data.items : [...rejectedItems.value, ...data.items]
    rejectedTotal.value = data.total
  } catch { /* 静默 */ }
  finally {
    if (seq === rejectedSeq) rejectedLoading.value = false
  }
}

function loadMoreRejected() {
  if (rejectedLoading.value) return  // 防止加载中重复点击导致跳页
  rejectedPage.value += 1
  loadRejectedItems(false)
}

async function approveItem(id: string) {
  if (approvingRejectedIds.value.has(id)) return
  approvingRejectedIds.value = new Set(approvingRejectedIds.value).add(id)
  try {
    await apiClient.patch(`/inspirations/${id}`, { quality_status: 'approved' })
    rejectedItems.value = rejectedItems.value.filter((i) => i.id !== id)
    rejectedTotal.value = Math.max(0, rejectedTotal.value - 1)
    message.success('已标记为通过')
    loadQualityReview()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '操作失败')
  } finally {
    const next = new Set(approvingRejectedIds.value)
    next.delete(id)
    approvingRejectedIds.value = next
  }
}

const deletingRejected = ref(false)

async function deleteRejected() {
  deletingRejected.value = true
  try {
    const r = await deleteRejectedInspirations()
    message.success(`已删除 ${r.deleted} 个已拒绝素材，释放 ${formatBytes(r.freed_bytes)}`)
    rejectedItems.value = []
    rejectedTotal.value = 0
    loadQualityReview()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '删除失败')
  } finally {
    deletingRejected.value = false
  }
}

let pollTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  loadQualityReview()
  loadRejectedItems()
  resumeReviewTask()
  pollTimer = setInterval(loadQualityReview, 10000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
  stopReviewPolling()
})
</script>

<template>
  <n-spin :show="qualityReviewLoading">
    <template v-if="qualityReviewStats">
      <!-- 统计卡片 -->
      <n-grid :cols="4" :x-gap="12" style="margin-bottom:16px">
        <n-gi><n-card size="small"><n-statistic label="待审核" :value="qualityReviewStats.pending" /></n-card></n-gi>
        <n-gi><n-card size="small"><n-statistic label="已通过" :value="qualityReviewStats.approved" /></n-card></n-gi>
        <n-gi><n-card size="small"><n-statistic label="已拒绝" :value="qualityReviewStats.rejected" /></n-card></n-gi>
        <n-gi><n-card size="small"><n-statistic label="通过率" :value="`${qualityReviewStats.pass_rate}%`" /></n-card></n-gi>
      </n-grid>

      <!-- 进度条 + 审核操作 -->
      <div style="display:flex;align-items:center;gap:16px;margin-bottom:20px">
        <n-progress
          v-if="qualityReviewStats.total > 0"
          type="line"
          :percentage="Math.round((qualityReviewStats.approved + qualityReviewStats.rejected) / qualityReviewStats.total * 100)"
          :height="24"
          style="flex:1"
        />
        <n-button
          type="primary"
          :loading="qualityChecking"
          :disabled="qualityReviewStats.pending === 0"
          @click="triggerQualityCheck"
        >
          {{ qualityReviewStats.pending > 0 ? `审核全部待审核 (${qualityReviewStats.pending})` : '全部已审核' }}
        </n-button>
        <n-popconfirm
          v-if="qualityReviewStats.approved > 0"
          @positive-click="recheckQuality"
        >
          <template #trigger>
            <n-button type="warning" secondary :loading="rechecking">
              重新审核已通过 ({{ qualityReviewStats.approved }})
            </n-button>
          </template>
          将已通过的 {{ qualityReviewStats.approved }} 个素材重置为待审核，并用最新标准重新判定（会重新调用 AI 模型，耗时较长）。确定继续？
        </n-popconfirm>
      </div>

      <!-- 审核任务进度 -->
      <n-alert v-if="reviewTask && (reviewTask.status === 'pending' || reviewTask.status === 'running')" type="info" style="margin-bottom:16px">
        <template #header>审核任务 #{{ reviewTask.id }} 进行中（{{ reviewTask.done }}/{{ reviewTask.total }}）</template>
        <n-progress type="line" :percentage="reviewTask.progress" style="margin-top:8px" />
      </n-alert>

      <!-- 未通过素材列表 -->
      <n-card size="small" title="未通过素材" style="margin-bottom:16px">
        <template #header-extra>
          <n-tag type="error" size="small">{{ rejectedTotal }} 个</n-tag>
          <n-button size="tiny" style="margin-left:6px" @click="loadRejectedItems(true)" :loading="rejectedLoading">刷新</n-button>
          <n-popconfirm v-if="rejectedTotal > 0" @positive-click="deleteRejected">
            <template #trigger>
              <n-button size="tiny" type="error" secondary style="margin-left:6px" :loading="deletingRejected">批量删除已拒绝</n-button>
            </template>
            确定删除全部 {{ rejectedTotal }} 个已拒绝素材？此操作会物理删除文件，不可恢复。
          </n-popconfirm>
        </template>
        <n-spin :show="rejectedLoading">
          <div v-if="rejectedItems.length" class="rejected-grid">
            <div v-for="item in rejectedItems" :key="item.id" class="rejected-card">
              <img :src="getFileUrl(item.thumbnail_path || item.file_path)" />
              <div class="rejected-reason" :title="item.quality_reason || ''">{{ item.quality_reason || '未说明原因' }}</div>
              <n-button size="tiny" type="success" ghost @click="approveItem(item.id)">✓ 翻案</n-button>
            </div>
          </div>
          <n-empty v-else description="暂无未通过素材" size="small" />
          <div v-if="rejectedTotal > rejectedItems.length" style="text-align:center;margin-top:12px">
            <n-button size="small" :disabled="rejectedLoading" @click="loadMoreRejected">加载更多（{{ rejectedItems.length }}/{{ rejectedTotal }}）</n-button>
          </div>
        </n-spin>
      </n-card>

      <!-- 说明 -->
      <n-alert type="info">
        <template #header>💡 待审核与已通过的素材</template>
        前往「素材库」页，用筛选栏的「待审核 / 已通过」查看对应素材。
      </n-alert>
    </template>
    <n-empty v-else-if="!qualityReviewLoading" description="点击加载审核数据" size="small">
      <template #extra><n-button size="small" @click="loadQualityReview">加载</n-button></template>
    </n-empty>
  </n-spin>
</template>

<style scoped>
.rejected-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 10px;
}
.rejected-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  overflow: hidden;
  background: #fff;
  padding-bottom: 6px;
}
.rejected-card img {
  width: 100%;
  aspect-ratio: 3/4;
  object-fit: cover;
  background: #f5f5f5;
}
.rejected-reason {
  font-size: 11px;
  color: #d03050;
  text-align: center;
  padding: 4px 6px;
  margin-bottom: 4px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-all;
}
</style>
