/** 博主未评分素材「补全评分」：提交后台任务 → 轮询进度 → 完成后刷新人物列表。
 *
 * 为什么走后台任务：一次要跨几百位博主扫描「未评分」素材并逐条写评分，
 * 同步请求必然超时，且任务还能进任务中心看进度/取消。
 *
 * 后端口径（契约）：只补未评分素材，已评分不覆盖；优先素材数 6+ 的博主、
 * 按素材数从少到多处理，让尽量多的博主先有均分。
 */

import { computed, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { getApiErrorMessage } from '@/utils/apiError'
import { bloggersApi } from '@/api/persons'
import { useTaskPolling } from '@/composables/useTaskPolling'
import { isTaskTerminalStatus } from '@/types/task'

/** 后端任务 result 里的补全计数（字段缺失时按 0 处理） */
interface RatingBackfillResult {
  /** 实际写入评分的素材数（后端正式字段为 updated_materials；updated/rated 兼容旧任务） */
  updated_materials?: number
  updated?: number
  rated?: number
  skipped?: number
  failed?: number
}

/** 校验星级：必须是 1~5 的整数（0 只用于批量评分的「清除」，补全不允许） */
function isValidRating(rating: number): boolean {
  return Number.isInteger(rating) && rating >= 1 && rating <= 5
}

/**
 * @param onFinished 任务成功结束后的刷新回调（父级刷新列表，让均分立刻反映出来）
 */
export function useRatingBackfill(onFinished: () => void | Promise<void>) {
  const { task, pollTask, stopPolling } = useTaskPolling()
  /** 正在提交（创建任务）的瞬间：避免连点建出多个任务 */
  const submitting = ref(false)

  /** 任务进行中（提交中或已有非终态任务）：按钮转圈禁用、弹窗不可重复提交 */
  const running = computed(
    () => submitting.value || (task.value != null && !isTaskTerminalStatus(task.value.status)),
  )

  /** 按钮上的进度文案：「12/386」；没有任务/已终态时为空串 */
  const progressText = computed(() => {
    const current = task.value
    if (!current || isTaskTerminalStatus(current.status)) return ''
    return `${current.done ?? 0}/${current.total ?? 0}`
  })

  /**
   * 提交补全任务并开始轮询。
   *
   * @param rating 统一评分（1~5）
   * @param bloggerIds 只处理这些博主（缺省/空 = 全部博主，后端负责优先级排序）
   */
  async function start(rating: number, bloggerIds?: number[]) {
    if (running.value) return
    if (!isValidRating(rating)) {
      Message.warning('请选择 1~5 星评分')
      return
    }
    submitting.value = true
    try {
      const data = await bloggersApi.backfillRatings(rating, bloggerIds)
      Message.success(data.message)
      // 立刻放一个「刚提交」的快照：running 依赖 task 状态，而第一次轮询要等 ~1 秒，
      // 空窗期内按钮不转圈、可被再点一次（会建出两个任务）。先占位再交给轮询覆盖
      task.value = {
        id: data.task_id,
        type: 'blogger_rating_backfill',
        status: 'pending',
        progress: 0,
        total: data.total,
        done: 0,
      } as unknown as typeof task.value
      pollTask(data.task_id, (result) => {
        const counts = (result || {}) as RatingBackfillResult
        const updated = Number(counts.updated_materials ?? counts.updated ?? counts.rated ?? 0) || 0
        const skipped = Number(counts.skipped ?? 0) || 0
        const failed = Number(counts.failed ?? 0) || 0
        if (updated > 0) {
          Message.success(
            `评分补全完成：已补 ${updated} 个素材` +
              (skipped > 0 ? ` · 跳过 ${skipped} 位博主` : '') +
              (failed > 0 ? ` · 失败 ${failed} 位博主` : ''),
          )
        } else {
          Message.warning(
            `没有补上评分（跳过 ${skipped} 位博主、失败 ${failed} 位博主）：` +
              '这些博主的素材可能都已经评过分',
          )
        }
        void onFinished()
      })
    } catch (e) {
      Message.error(getApiErrorMessage(e, '创建评分补全任务失败'))
    } finally {
      submitting.value = false
    }
  }

  /** 组件卸载/关闭时停止轮询（避免回调打到已销毁的界面） */
  function stop() {
    stopPolling()
  }

  return { task, running, progressText, start, stop }
}
