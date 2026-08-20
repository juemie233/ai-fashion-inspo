<script setup lang="ts">
/** 质量审核面板：待审核/已通过/已拒绝统计 + 未通过素材管理。 */

import { getApiErrorMessage } from '@/utils/apiError'
import { ref, watch, onMounted, onUnmounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import apiClient from '@/api/client'
import { deleteRejectedInspirations, batchUpdateInspirations, batchTrash } from '@/api/inspirations'
import QualityLearnerCard from '@/components/model/QualityLearnerCard.vue'
import StatCardGrid from '@/components/common/StatCardGrid.vue'
import InspirationGridBrowser, {
  type GridBrowserItem,
} from '@/components/inspiration/InspirationGridBrowser.vue'

const router = useRouter()

interface QualityReviewStats {
  total: number
  pending: number
  approved: number
  rejected: number
  pass_rate: number
  ai_generated: number
}
const qualityReviewStats = ref<QualityReviewStats | null>(null)
const qualityReviewLoading = ref(false)
const qualityChecking = ref(false)
const rechecking = ref(false)
const randomReviewCount = ref(parseInt(localStorage.getItem('review-random-count') || '', 10) || 10) // 随机审核数量（可调）
const randomChecking = ref(false)

// 持久化随机审核数量：刷新或再次进入时保持上次设置
watch(randomReviewCount, (v) => {
  localStorage.setItem('review-random-count', String(v))
})

// 手动上传免审核配置
const autoApproveEnabled = ref(true)
const autoApproveSaving = ref(false)

// 质量审核任务（数据库驱动任务队列，轮询进度）
interface ReviewTask {
  id: number
  type: string
  status: string
  progress: number
  total: number
  done: number
  result: { approved?: number; rejected?: number; pending?: number; ai_generated?: number } | null
  error: string | null
  retry_count: number
  max_retries: number
  next_retry_at: string | null
  created_at: string
  updated_at: string
}
const reviewTask = ref<ReviewTask | null>(null)
let reviewPollTimer: ReturnType<typeof setTimeout> | null = null
let reviewPollSeq = 0 // 轮询代际号：stop/重启时自增，使在途请求返回后不再续排

/** 是否存在进行中的审核任务（pending/running），用于禁止重复提交审核 */
const reviewTaskActive = computed(() => {
  const s = reviewTask.value?.status
  return s === 'pending' || s === 'running'
})

async function loadQualityReview() {
  qualityReviewLoading.value = true
  try {
    const { data } = await apiClient.get<QualityReviewStats>('/ai/quality-stats')
    qualityReviewStats.value = data
  } catch {
    /* 静默 */
  } finally {
    qualityReviewLoading.value = false
  }
}

/** 读取「手动上传免审核」配置 */
async function loadAutoApprove() {
  try {
    const { data } = await apiClient.get<{ enabled: boolean }>('/ai/manual-upload-auto-approve')
    autoApproveEnabled.value = data.enabled
  } catch {
    /* 静默 */
  }
}

/** 切换「手动上传免审核」配置 */
async function toggleAutoApprove(val: boolean) {
  autoApproveSaving.value = true
  try {
    await apiClient.put('/ai/manual-upload-auto-approve', null, { params: { enabled: val } })
    Message.success('手动上传免审核已' + (val ? '开启' : '关闭'))
  } catch (e) {
    Message.error(getApiErrorMessage(e, '设置失败'))
    autoApproveEnabled.value = !val // 失败回滚
  } finally {
    autoApproveSaving.value = false
  }
}

/** 轮询审核任务状态（约 1 秒一次），完成后刷新统计与未通过列表 */
function startReviewPolling(taskId: number) {
  stopReviewPolling()
  const seq = reviewPollSeq // 当前代际：stopReviewPolling 已自增，旧链的 seq 与之不符即失效
  let consecutiveFailures = 0 // 连续失败次数，失败时有限次重试而非直接停止
  const poll = async () => {
    if (seq !== reviewPollSeq) return // 已被 stop/新轮询取代，不再调度
    try {
      const { data } = await apiClient.get<ReviewTask>(`/tasks/${taskId}`)
      if (seq !== reviewPollSeq) return // 在途请求返回前已被停止，丢弃结果
      consecutiveFailures = 0
      reviewTask.value = data
      if (data.status === 'success' || data.status === 'failed' || data.status === 'cancelled') {
        stopReviewPolling()
        if (data.status === 'success') {
          const r = data.result
          Message.success(
            `审核完成：通过 ${r?.approved ?? 0}，拒绝 ${r?.rejected ?? 0}，未判定 ${r?.pending ?? 0}，疑似 AI ${r?.ai_generated ?? 0}`,
          )
        } else if (data.status === 'failed') {
          Message.error(`审核失败：${data.error || '未知错误'}`)
        } else {
          Message.info('审核任务已取消')
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
        Message.error('获取审核任务状态多次失败，已停止轮询，请稍后手动刷新')
        return
      }
      // 有限次重试：间隔放大到 3 秒，继续续排轮询链
      reviewPollTimer = setTimeout(poll, 3000)
    }
  }
  poll()
}

function stopReviewPolling() {
  reviewPollSeq += 1 // 自增代际号，使当前轮询链失效，防止在途请求返回后重新调度
  if (reviewPollTimer) {
    clearTimeout(reviewPollTimer)
    reviewPollTimer = null
  }
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
  } catch {
    /* 静默 */
  }
}

async function triggerQualityCheck() {
  qualityChecking.value = true
  try {
    const { data } = await apiClient.post<{ message: string; count: number; task_id: number }>(
      '/ai/quality-check',
      null,
      { params: { limit: 200 } },
    )
    reviewTask.value = {
      id: data.task_id,
      type: 'quality_check',
      status: 'pending',
      progress: 0,
      total: data.count,
      done: 0,
      result: null,
      error: null,
      retry_count: 0,
      max_retries: 2,
      next_retry_at: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }
    Message.success(`已提交 ${data.count} 个素材进行审核（任务 #${data.task_id}）`)
    startReviewPolling(data.task_id)
  } catch (e) {
    Message.error(getApiErrorMessage(e, '审核提交失败'))
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
    reviewTask.value = {
      id: data.task_id,
      type: 'quality_check',
      status: 'pending',
      progress: 0,
      total: data.count,
      done: 0,
      result: null,
      error: null,
      retry_count: 0,
      max_retries: 2,
      next_retry_at: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }
    Message.success(`已提交重新审核任务 #${data.task_id}，共 ${data.count} 个素材`)
    startReviewPolling(data.task_id)
  } catch (e) {
    Message.error(getApiErrorMessage(e, '重新审核提交失败'))
  } finally {
    rechecking.value = false
  }
}

/** 随机抽取指定数量的待审核素材进行审核 */
async function randomQualityCheck() {
  const count = randomReviewCount.value
  if (!count || count < 1) {
    Message.warning('请输入有效的随机审核数量（1~200）')
    return
  }
  randomChecking.value = true
  try {
    const { data } = await apiClient.post<{ message: string; count: number; task_id?: number }>(
      '/ai/quality-check',
      null,
      { params: { limit: count, random: true } },
    )
    if (!data.task_id || data.count === 0) {
      Message.info(data.message || '没有待审核的素材')
      return
    }
    reviewTask.value = {
      id: data.task_id,
      type: 'quality_check',
      status: 'pending',
      progress: 0,
      total: data.count,
      done: 0,
      result: null,
      error: null,
      retry_count: 0,
      max_retries: 2,
      next_retry_at: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }
    Message.success(`已随机抽取 ${data.count} 个素材进行审核（任务 #${data.task_id}）`)
    startReviewPolling(data.task_id)
  } catch (e) {
    Message.error(getApiErrorMessage(e, '随机审核提交失败'))
  } finally {
    randomChecking.value = false
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
const rejectedDensity = ref<'compact' | 'standard'>(
  (localStorage.getItem('review-rejected-density') as 'compact' | 'standard') || 'compact',
)
let rejectedSeq = 0 // 请求序号，防止陈旧响应覆盖
const approvingRejectedIds = ref<Set<string>>(new Set())
const batchApproving = ref(false)
const batchTrashing = ref(false)
const rejectedGridRef = ref<InstanceType<typeof InspirationGridBrowser> | null>(null)

// 持久化未通过素材网格密度
watch(rejectedDensity, (v) => {
  localStorage.setItem('review-rejected-density', v)
})

/** 映射为通用网格条目（未通过素材均为图片，媒体类型固定 image） */
const rejectedGridItems = computed<GridBrowserItem[]>(() =>
  rejectedItems.value.map((i) => ({ ...i, media_type: 'image' })),
)

async function loadRejectedItems(reset = true) {
  if (reset) rejectedPage.value = 1
  const page = rejectedPage.value
  rejectedLoading.value = true
  const seq = ++rejectedSeq
  try {
    const { data } = await apiClient.get<{ items: RejectedItem[]; total: number }>(
      '/inspirations',
      {
        params: { quality_status: 'rejected', size: rejectedPageSize, page, sort: 'newest' },
      },
    )
    if (seq !== rejectedSeq) return // 忽略过期响应
    rejectedItems.value = reset ? data.items : [...rejectedItems.value, ...data.items]
    rejectedTotal.value = data.total
  } catch {
    /* 静默 */
  } finally {
    if (seq === rejectedSeq) rejectedLoading.value = false
  }
}

function loadMoreRejected() {
  if (rejectedLoading.value) return // 防止加载中重复点击导致跳页
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
    rejectedGridRef.value?.removeSelectedId(id) // 同步清除网格选中残留
    Message.success('已标记为通过')
    loadQualityReview()
  } catch (e) {
    Message.error(getApiErrorMessage(e, '操作失败'))
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
    Message.success(r.message || `已将 ${r.trashed} 个已拒绝素材移入垃圾桶`)
    rejectedItems.value = []
    rejectedTotal.value = 0
    loadQualityReview()
  } catch (e) {
    Message.error(getApiErrorMessage(e, '移入垃圾桶失败'))
  } finally {
    deletingRejected.value = false
  }
}

/** 批量通过：把选中的未通过素材翻案为已通过（复用批量编辑接口） */
async function batchApprove(ids: string[], clear: () => void) {
  if (ids.length === 0) return
  batchApproving.value = true
  try {
    const updated = await batchUpdateInspirations(ids, { quality_status: 'approved' })
    Message.success(`已将 ${updated} 个素材标记为通过`)
    clear()
    await loadRejectedItems(true)
    loadQualityReview()
  } catch (e) {
    Message.error(getApiErrorMessage(e, '批量通过失败'))
  } finally {
    batchApproving.value = false
  }
}

/** 批量移入垃圾桶：把选中的未通过素材移入垃圾桶（负样本学习仍会使用），来源标记为自动移动 */
async function batchTrashSelected(ids: string[], clear: () => void) {
  if (ids.length === 0) return
  batchTrashing.value = true
  try {
    const { trashed, skipped } = await batchTrash(ids, '质量差', 'auto')
    const parts = [`已将 ${trashed} 个素材移入垃圾桶`]
    if (skipped > 0) parts.push(`${skipped} 个跳过`)
    Message.success(parts.join('，'))
    clear()
    await loadRejectedItems(true)
    loadQualityReview()
  } catch (e) {
    Message.error(getApiErrorMessage(e, '移入垃圾桶失败'))
  } finally {
    batchTrashing.value = false
  }
}

let pollTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  loadQualityReview()
  loadRejectedItems()
  resumeReviewTask()
  loadAutoApprove()
  pollTimer = setInterval(loadQualityReview, 10000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
  stopReviewPolling()
})
</script>

<template>
  <a-spin :loading="qualityReviewLoading" style="display: block">
    <template v-if="qualityReviewStats">
      <!-- 统计卡片 -->
      <StatCardGrid
        :items="[
          { title: '待审核', value: qualityReviewStats.pending },
          { title: '已通过', value: qualityReviewStats.approved },
          { title: '已拒绝', value: qualityReviewStats.rejected },
          { title: '通过率', value: qualityReviewStats.pass_rate, suffix: '%' },
          { title: '疑似 AI', value: qualityReviewStats.ai_generated },
        ]"
      />

      <!-- 手动上传免审核配置 -->
      <a-card size="small" style="margin-bottom: 16px">
        <a-space align="center" style="display: flex; justify-content: space-between">
          <span style="font-size: 13px">手动上传默认免审核</span>
          <a-switch
            :model-value="autoApproveEnabled"
            :loading="autoApproveSaving"
            @change="(v: unknown) => toggleAutoApprove(v === true)"
          />
        </a-space>
        <p style="font-size: 12px; color: #999; margin: 8px 0 0">
          开启后，手动上传的素材会直接标记为「已通过」，不再进入待审核队列。
        </p>
      </a-card>

      <!-- 负样本初筛器（阶段 2：前置质量初筛） -->
      <QualityLearnerCard />

      <!-- 进度条 + 审核操作 -->
      <div style="display: flex; align-items: center; gap: 16px; margin-bottom: 20px">
        <a-progress
          v-if="qualityReviewStats.total > 0"
          type="line"
          :percent="
            Math.round(
              ((qualityReviewStats.approved + qualityReviewStats.rejected) /
                qualityReviewStats.total) *
                100,
            ) / 100
          "
          :stroke-width="24"
          style="flex: 1"
        />
        <a-button
          type="primary"
          :loading="qualityChecking"
          :disabled="qualityReviewStats.pending === 0 || reviewTaskActive"
          @click="triggerQualityCheck"
        >
          {{
            reviewTaskActive
              ? '审核进行中…'
              : qualityReviewStats.pending > 0
                ? `审核全部待审核 (${qualityReviewStats.pending})`
                : '全部已审核'
          }}
        </a-button>
        <a-space :size="6" align="center">
          <a-input-number
            v-model="randomReviewCount"
            :min="1"
            :max="200"
            size="small"
            style="width: 88px"
            placeholder="数量"
          />
          <a-button
            type="secondary"
            :loading="randomChecking"
            :disabled="qualityReviewStats.total === 0"
            @click="randomQualityCheck"
          >
            随机审核
          </a-button>
        </a-space>
        <a-popconfirm
          v-if="qualityReviewStats.approved > 0"
          :content="`将已通过的 ${qualityReviewStats.approved} 个素材重置为待审核，并用最新标准重新判定（会重新调用 AI 模型，耗时较长）。确定继续？`"
          @ok="recheckQuality"
        >
          <a-button type="secondary" status="warning" :loading="rechecking">
            重新审核已通过 ({{ qualityReviewStats.approved }})
          </a-button>
        </a-popconfirm>
      </div>

      <!-- 审核任务进度 -->
      <a-alert
        v-if="reviewTask && (reviewTask.status === 'pending' || reviewTask.status === 'running')"
        type="info"
        style="margin-bottom: 16px"
      >
        <template #title
          >审核任务 #{{ reviewTask.id }} 进行中（{{ reviewTask.done }}/{{
            reviewTask.total
          }}）</template
        >
        <a-progress type="line" :percent="reviewTask.progress / 100" style="margin-top: 8px" />
      </a-alert>

      <!-- 未通过素材列表 -->
      <a-card size="small" title="未通过素材" style="margin-bottom: 16px">
        <template #extra>
          <a-tag color="red" size="small">{{ rejectedTotal }} 个</a-tag>
          <a-button
            size="mini"
            style="margin-left: 6px"
            @click="loadRejectedItems(true)"
            :loading="rejectedLoading"
            >刷新</a-button
          >
          <a-popconfirm
            v-if="rejectedTotal > 0"
            :content="`确定将全部 ${rejectedTotal} 个已拒绝素材移入垃圾桶？可在「垃圾桶」中恢复，负样本学习仍会使用这些素材。`"
            @ok="deleteRejected"
          >
            <a-button
              size="mini"
              type="secondary"
              status="danger"
              style="margin-left: 6px"
              :loading="deletingRejected"
              >全部移入垃圾桶</a-button
            >
          </a-popconfirm>
        </template>

        <InspirationGridBrowser
          ref="rejectedGridRef"
          :items="rejectedGridItems"
          :total="rejectedTotal"
          :loading="rejectedLoading"
          v-model:density="rejectedDensity"
          empty-text="暂无未通过素材"
          @load-more="loadMoreRejected"
          @open-detail="
            (item: GridBrowserItem) => router.push({ name: 'detail', params: { id: item.id } })
          "
        >
          <!-- 批量操作栏：全选 + 批量通过 / 批量移入垃圾桶 -->
          <template #batch-actions="{ ids, count, clear, allSelected, toggleAll }">
            <a-checkbox
              :model-value="allSelected"
              :indeterminate="count > 0 && !allSelected"
              @change="toggleAll"
            />
            <span style="font-size: 13px">已选 {{ count }} 个</span>
            <a-popconfirm
              :content="`将选中的 ${count} 个素材标记为「已通过」？确定继续？`"
              @ok="batchApprove(ids, clear)"
            >
              <a-button size="mini" type="secondary" status="success" :loading="batchApproving"
                >批量通过</a-button
              >
            </a-popconfirm>
            <a-popconfirm
              :content="`将选中的 ${count} 个素材移入垃圾桶？可在「垃圾桶」中恢复，负样本学习仍会使用这些素材。`"
              @ok="batchTrashSelected(ids, clear)"
            >
              <a-button size="mini" type="secondary" status="danger" :loading="batchTrashing"
                >批量移入垃圾桶</a-button
              >
            </a-popconfirm>
            <a-button size="mini" @click="clear">取消选择</a-button>
          </template>

          <!-- 卡片悬停操作：翻案（大图按钮由通用组件内置） -->
          <template #card-actions="{ item }">
            <a-button
              size="mini"
              type="outline"
              status="success"
              :loading="approvingRejectedIds.has(item.id)"
              @click="approveItem(item.id)"
              >✓ 翻案</a-button
            >
          </template>

          <!-- 卡片附加：审核原因 -->
          <template #card-extra="{ item }">
            <div class="rejected-reason" :title="String(item.quality_reason || '')">
              {{ item.quality_reason || '未说明原因' }}
            </div>
          </template>
        </InspirationGridBrowser>
      </a-card>

      <!-- 说明 -->
      <a-alert type="info">
        <template #title>💡 待审核与已通过的素材</template>
        前往「素材库」页，用筛选栏的「待审核 / 已通过」查看对应素材。
      </a-alert>
    </template>
    <a-empty v-else-if="!qualityReviewLoading" description="点击加载审核数据">
      <a-button size="small" @click="loadQualityReview">加载</a-button>
    </a-empty>
  </a-spin>
</template>

<style scoped>
/* 审核原因：覆盖在图片顶部的半透明红条，不占用布局高度、不遮挡底部悬停按钮 */
.rejected-reason {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  font-size: 11px;
  color: #fff;
  background: rgba(208, 48, 80, 0.85);
  text-align: center;
  padding: 3px 6px;
  border-radius: 4px 4px 0 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-all;
  pointer-events: none;
  z-index: 2;
}
</style>
