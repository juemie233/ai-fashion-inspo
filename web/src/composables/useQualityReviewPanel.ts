/** 质量审核面板的「概览统计 + 自动通过开关 + 审核任务」逻辑（从 ReviewPanel.vue 抽出）。
 *
 * 为什么抽：ReviewPanel 831 行（超过拆分线），而这一半是**自成体系的状态 + 逻辑**——
 * 按 CLAUDE.md「前端文件拆分约定」应放 composables。视图只保留生命周期接线
 * （onMounted 拉概览 + 10 秒轮询、onUnmounted 停轮询）、未通过素材列表与模板。
 *
 * 依赖注入：审核任务结束后要刷新「未通过素材」列表，而那份状态属于视图，
 * 所以 `loadRejectedItems` 由调用方传入，避免两个模块互相持有。
 *
 * 关键口径（改之前先看）：
 * - `batchSubmitCount` 受 `QUALITY_BATCH_MAX` 限制，超限时 `batchOverflowHint` 提示；
 * - `randomReviewCount` 持久化在 localStorage（`review-random-count`），刷新不丢；
 * - 审核任务是**异步长任务**：`startReviewPolling` 用代际号（reviewPollSeq）防止
 *   旧轮询链在新任务开始后继续写状态，`stopReviewPolling` 会自增代际。
 */

import { computed, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'

import apiClient from '@/api/client'
import { getApiErrorMessage } from '@/utils/apiError'
import { QUALITY_BATCH_MAX, qualityBatchHint } from '@/utils/qualityBatch'

interface QualityReviewStats {
  total: number
  pending: number
  approved: number
  rejected: number
  pass_rate: number
  ai_generated: number
  /** 实测平均审核耗时（秒）：0 表示暂无成功样本，前端退回兜底常量 */
  avg_seconds?: number
  /** 被拒素材按结构化原因码分组（后端 quality_reason_code） */
  reason_counts?: Record<string, number>
}

export interface QualityReviewPanelDeps {
  /** 刷新「未通过素材」列表（该状态归视图所有，注入避免循环依赖） */
  loadRejectedItems: (reset?: boolean) => Promise<void>
}

export function useQualityReviewPanel(opts: QualityReviewPanelDeps) {
  const { loadRejectedItems } = opts

  const qualityReviewStats = ref<QualityReviewStats | null>(null)
  const qualityReviewLoading = ref(false)
  const qualityChecking = ref(false)
  const rechecking = ref(false)
  const randomReviewCount = ref(
    parseInt(localStorage.getItem('review-random-count') || '', 10) || 10,
  ) // 随机审核数量（可调）
  const randomChecking = ref(false)

  /** 本轮批量审核实际提交的数量（= min(待审核数, 单次上限)）：按钮文案承诺「审核全部」，
   *  过去的实现却写死 200 条，导致上万条待审核时要反复点几十次 */
  const batchSubmitCount = computed(() =>
    Math.min(qualityReviewStats.value?.pending ?? 0, QUALITY_BATCH_MAX),
  )
  /** 超出单次上限时提示本轮只处理上限条（剩余可再点一次） */
  const batchOverflowHint = computed(() => {
    const pending = qualityReviewStats.value?.pending ?? 0
    return pending > QUALITY_BATCH_MAX
      ? `本轮处理 ${QUALITY_BATCH_MAX} 条（单次上限），剩余 ${pending - QUALITY_BATCH_MAX} 条可再点一次。`
      : ''
  })
  /** 提交前的耗时提示：优先用后端实测均值（avg_seconds），没有数据时才用兜底常量 */
  const batchCostHint = computed(() =>
    qualityBatchHint(batchSubmitCount.value, 1, qualityReviewStats.value?.avg_seconds),
  )
  /** 批量审核按钮文案（两处分支共用，避免重复三份） */
  const batchButtonLabel = computed(() => {
    if (reviewTaskActive.value) return '审核进行中…'
    const pending = qualityReviewStats.value?.pending ?? 0
    return pending > 0 ? `审核全部待审核 (${pending})` : '全部已审核'
  })

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
        { params: { limit: QUALITY_BATCH_MAX } },
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
      Message.success(
        `已提交 ${data.count} 个素材进行审核（任务 #${data.task_id}）；可在任务管理页暂停/取消`,
      )
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
      Message.warning(`请输入有效的随机审核数量（1~${QUALITY_BATCH_MAX}）`)
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

  return {
    qualityReviewStats,
    qualityReviewLoading,
    qualityChecking,
    rechecking,
    randomReviewCount,
    randomChecking,
    batchSubmitCount,
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
  }
}
