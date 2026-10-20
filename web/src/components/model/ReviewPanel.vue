<script setup lang="ts">
/** 质量审核面板：待审核/已通过/已拒绝统计 + 未通过素材管理。 */

import { getApiErrorMessage } from '@/utils/apiError'
import { openInspiration } from '@/utils/openInspiration'
import { QUALITY_BATCH_MAX } from '@/utils/qualityBatch'
import { QUALITY_REASON_OPTIONS, qualityReasonLabel } from '@/utils/qualityReason'
import { submitQualityCorrection } from '@/api/aiQuality'
import { ref, watch, onMounted, onUnmounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import apiClient from '@/api/client'
import { deleteRejectedInspirations, batchUpdateInspirations, batchTrash } from '@/api/inspirations'
import QualityLearnerCard from '@/components/model/QualityLearnerCard.vue'
import StatCardGrid from '@/components/common/StatCardGrid.vue'
import QualityInsightsPanel from '@/components/model/QualityInsightsPanel.vue'
import InspirationGridBrowser, {
  type GridBrowserItem,
} from '@/components/inspiration/InspirationGridBrowser.vue'
import { useQualityReviewPanel } from '@/composables/useQualityReviewPanel'

const router = useRouter()

const {
  qualityReviewStats,
  qualityReviewLoading,
  qualityChecking,
  rechecking,
  randomReviewCount,
  randomChecking,
  batchOverflowHint,
  batchCostHint,
  batchButtonLabel,
  autoApproveEnabled,
  autoApproveSaving,
  reviewTask,
  reviewTaskActive,
  loadQualityReview,
  loadAutoApprove,
  toggleAutoApprove,
  resumeReviewTask,
  triggerQualityCheck,
  recheckQuality,
  randomQualityCheck,
  stopReviewPolling,
} = useQualityReviewPanel({ loadRejectedItems })

// 未通过素材列表
interface RejectedItem {
  quality_status?: string | null
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

/** 按原因筛选未通过素材（空串=全部） */
const rejectedReasonFilter = ref('')

/** 原因筛选下拉：标签里带数量，直接看出哪类拒绝最多 */
const rejectedReasonOptions = computed(() => {
  const counts = qualityReviewStats.value?.reason_counts ?? {}
  return QUALITY_REASON_OPTIONS.map((o) => ({
    label: o.value && counts[o.value] ? `${o.label}（${counts[o.value]}）` : o.label,
    value: o.value,
  }))
})

async function loadRejectedItems(reset = true) {
  if (reset) rejectedPage.value = 1
  const page = rejectedPage.value
  rejectedLoading.value = true
  const seq = ++rejectedSeq
  try {
    const { data } = await apiClient.get<{ items: RejectedItem[]; total: number }>(
      '/inspirations',
      {
        params: {
          quality_status: 'rejected',
          size: rejectedPageSize,
          page,
          sort: 'newest',
          // 按原因筛选（后端 quality_reason_code）；空串表示全部
          ...(rejectedReasonFilter.value
            ? { quality_reason_code: rejectedReasonFilter.value }
            : {}),
        },
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

/** 纠错可选的原因：排除系统态码（`prefilter_reject` / `unknown` 不是「人工认为错在哪」） */
const correctionReasonOptions = computed(() =>
  QUALITY_REASON_OPTIONS.filter(
    (o) => o.value && o.value !== 'prefilter_reject' && o.value !== 'unknown',
  ),
)

/** 误判反馈弹窗状态：默认把判定「翻转」过来（rejected → approved），减少一次点击 */
const correctionOpen = ref(false)
const correctionItem = ref<RejectedItem | GridBrowserItem | null>(null)
const correctionStatus = ref<'approved' | 'rejected'>('approved')
const correctionReason = ref('')
const correctionNote = ref('')
const correctionSubmitting = ref(false)

function openCorrection(item: RejectedItem | GridBrowserItem) {
  correctionItem.value = item
  correctionStatus.value = item.quality_status === 'approved' ? 'rejected' : 'approved'
  correctionReason.value = ''
  correctionNote.value = ''
  correctionOpen.value = true
}

async function submitCorrection() {
  const item = correctionItem.value
  if (!item) return
  correctionSubmitting.value = true
  try {
    await submitQualityCorrection({
      inspiration_id: item.id,
      corrected_status: correctionStatus.value,
      reason_code: correctionReason.value || undefined,
      note: correctionNote.value.trim() || undefined,
    })
    Message.success('已记录误判反馈，素材判定已按你的选择改正')
    correctionOpen.value = false
    await Promise.all([loadRejectedItems(true), loadQualityReview()])
  } catch (e) {
    Message.error(getApiErrorMessage(e, '提交失败'))
  } finally {
    correctionSubmitting.value = false
  }
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

      <!-- 质量洞察：按来源通过率 / 灰区复审 / prompt A/B -->
      <QualityInsightsPanel />

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
        <a-tooltip :content="batchOverflowHint || batchCostHint">
          <a-popconfirm
            v-if="batchOverflowHint"
            :content="`${batchOverflowHint}${batchCostHint}。提交后可在任务管理页暂停/取消。确定继续？`"
            @ok="triggerQualityCheck"
          >
            <a-button
              type="primary"
              :loading="qualityChecking"
              :disabled="qualityReviewStats.pending === 0 || reviewTaskActive"
            >
              {{ batchButtonLabel }}
            </a-button>
          </a-popconfirm>
          <a-button
            v-else
            type="primary"
            :loading="qualityChecking"
            :disabled="qualityReviewStats.pending === 0 || reviewTaskActive"
            @click="triggerQualityCheck"
          >
            {{ batchButtonLabel }}
          </a-button>
        </a-tooltip>
        <a-space :size="6" align="center">
          <a-input-number
            v-model="randomReviewCount"
            :min="1"
            :max="QUALITY_BATCH_MAX"
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
          <a-select
            v-model="rejectedReasonFilter"
            :options="rejectedReasonOptions"
            size="small"
            style="width: 150px; margin-right: 6px"
            @change="loadRejectedItems(true)"
          />
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
          @open-detail="(item: GridBrowserItem) => openInspiration(router, item.id)"
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
            <a-button
              size="mini"
              type="outline"
              style="margin-left: 6px"
              @click="openCorrection(item)"
              >判错了</a-button
            >
          </template>

          <!-- 卡片附加：审核原因 -->
          <template #card-extra="{ item }">
            <div class="rejected-reason" :title="String(item.quality_reason || '')">
              <a-tag
                v-if="item.quality_reason_code"
                size="small"
                color="orange"
                style="margin-right: 4px"
                >{{ qualityReasonLabel(item.quality_reason_code) }}</a-tag
              >
              {{ item.quality_reason || '未说明原因' }}
            </div>
          </template>
        </InspirationGridBrowser>
      </a-card>

      <!-- 误判反馈弹窗：选正确判定 + 错在哪类 + 备注；提交后状态即时改正 -->
      <a-modal
        v-model:visible="correctionOpen"
        title="AI 判错了"
        :ok-loading="correctionSubmitting"
        ok-text="提交反馈"
        cancel-text="取消"
        @ok="submitCorrection"
      >
        <div class="correction-form">
          <div class="correction-row">
            <div class="correction-label">应该判为</div>
            <a-radio-group v-model="correctionStatus">
              <a-radio value="approved">通过（其实是穿搭）</a-radio>
              <a-radio value="rejected">拒绝（确实不该通过）</a-radio>
            </a-radio-group>
          </div>
          <div class="correction-row">
            <div class="correction-label">错在哪类（可选）</div>
            <a-select
              v-model="correctionReason"
              :options="correctionReasonOptions"
              allow-clear
              style="width: 100%"
            />
          </div>
          <div class="correction-row">
            <div class="correction-label">补充说明（可选）</div>
            <a-textarea
              v-model="correctionNote"
              :max-length="200"
              show-word-limit
              placeholder="例如：只有单品特写，但确实是穿搭展示"
            />
          </div>
        </div>
      </a-modal>

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
/* 误判反馈弹窗：三行「标签 + 控件」，不需要 Form 组件（也避开其类型约束） */
.correction-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.correction-label {
  font-size: 13px;
  color: var(--color-text-2);
  margin-bottom: 6px;
}

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
