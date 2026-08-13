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
  active: number
}
const qualityReviewStats = ref<QualityReviewStats | null>(null)
const qualityReviewLoading = ref(false)
const qualityReviewActive = ref<string[]>([])
const qualityChecking = ref(false)
const rechecking = ref(false)

async function loadQualityReview() {
  qualityReviewLoading.value = true
  try {
    const { data } = await apiClient.get<QualityReviewStats>('/ai/quality-stats')
    qualityReviewStats.value = data
    const active = await apiClient.get<{ active: string[]; count: number }>('/ai/quality-active')
    qualityReviewActive.value = active.data.active || []
  } catch { /* 静默 */ }
  finally { qualityReviewLoading.value = false }
}

async function triggerQualityCheck() {
  qualityChecking.value = true
  try {
    const { data } = await apiClient.post<{ message: string; count: number }>(
      '/ai/quality-check',
      null,
      { params: { limit: 200 } },
    )
    message.success(`已提交 ${data.count} 个素材进行审核`)
    loadQualityReview()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '审核提交失败')
  } finally {
    qualityChecking.value = false
  }
}

async function recheckQuality() {
  rechecking.value = true
  try {
    const { data } = await apiClient.post<{ message: string; count: number }>(
      '/ai/quality-recheck',
    )
    message.success(data.message)
    loadQualityReview()
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
  pollTimer = setInterval(loadQualityReview, 10000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
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

      <!-- 正在审核提示 -->
      <n-alert v-if="qualityReviewActive.length > 0" type="info" style="margin-bottom:16px">
        <template #header>正在审核 {{ qualityReviewActive.length }} 个素材...</template>
        <div style="font-size:12px;color:#666">
          {{ qualityReviewActive.map((id) => id.slice(0, 8) + '...').join('、') }}
        </div>
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
