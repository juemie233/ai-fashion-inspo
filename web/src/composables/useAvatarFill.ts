/** 人物头像批量补全：提交任务 → 轮询进度（WS 推送 + 兜底轮询）→ 完成后刷新列表。
 *
 * 为什么走后台任务而不是同步接口：一位要读素材 + 送人脸子服务检测 + 裁剪写盘（约 1 秒），
 * 四百多位就是几分钟，同步 HTTP 请求必然超时；任务还能在任务中心看到进度、可取消。
 *
 * 头像来源是**博主自己的素材**（裁真实人像），所以只覆盖「有素材」的博主；
 * 默认只补空缺（不覆盖手动挑过的头像），需要覆盖时传 overwrite。
 */

import { computed, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { getApiErrorMessage } from '@/utils/apiError'
import { fillMissingAvatars } from '@/api/persons'
import { useTaskPolling } from '@/composables/useTaskPolling'
import { isTaskTerminalStatus } from '@/types/task'

/** 头像补全任务结果（后端 result 里的计数） */
interface AvatarFillResult {
  updated?: number
  skipped?: number
  failed?: number
}

export function useAvatarFill(onFinished: () => void | Promise<void>) {
  const { task, pollTask, stopPolling } = useTaskPolling()
  /** 正在提交（创建任务）的瞬间：避免连点建出多个任务 */
  const submitting = ref(false)

  /** 任务进行中（提交中或已有非终态任务）：按钮转圈禁用 */
  const running = computed(
    () => submitting.value || (task.value != null && !isTaskTerminalStatus(task.value.status)),
  )

  /** 按钮上的进度文案：「补全 12/386」；没有任务时为空串 */
  const progressText = computed(() => {
    const current = task.value
    if (!current || isTaskTerminalStatus(current.status)) return ''
    return `${current.done ?? 0}/${current.total ?? 0}`
  })

  /** 批量补全头像：overwrite=true 时连已有头像的博主一起覆盖 */
  async function fillAvatars(overwrite = false) {
    if (running.value) return
    submitting.value = true
    try {
      const data = await fillMissingAvatars(undefined, overwrite)
      Message.success(data.message)
      pollTask(data.task_id, (result) => {
        const counts = (result || {}) as AvatarFillResult
        const updated = counts.updated ?? 0
        const failed = counts.failed ?? 0
        const skipped = counts.skipped ?? 0
        if (updated > 0) {
          Message.success(
            `头像补全完成：成功 ${updated} 位` +
              (skipped > 0 ? ` · 跳过 ${skipped}` : '') +
              (failed > 0 ? ` · 失败 ${failed}` : ''),
          )
        } else {
          Message.warning(
            `没有补上头像（跳过 ${skipped} 位、失败 ${failed} 位）：` +
              'TA 的素材里没检出明确的人脸，可手动挑一张更清楚的',
          )
        }
        void onFinished()
      })
    } catch (e) {
      // 400 = 没有可补的博主（后端给了人话原因），单独提示更清楚
      Message.error(getApiErrorMessage(e, '创建头像补全任务失败'))
    } finally {
      submitting.value = false
    }
  }

  /** 组件卸载/关闭时停止轮询（避免回调打到已销毁的界面） */
  function stop() {
    stopPolling()
  }

  return { task, running, progressText, fillAvatars, stop }
}
