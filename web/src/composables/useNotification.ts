/** 浏览器桌面通知 composable：请求权限、发送通知、失败告警。 */

import { ref } from 'vue'

/** 通知权限状态 */
const permission = ref<NotificationPermission>('default')

/** 请求通知权限（首次调用时浏览器会弹出授权对话框） */
async function requestPermission(): Promise<boolean> {
  if (!('Notification' in window)) {
    console.warn('浏览器不支持 Notification API')
    return false
  }
  if (permission.value === 'granted') return true
  const result = await Notification.requestPermission()
  permission.value = result
  return result === 'granted'
}

/** 发送桌面通知 */
function notify(title: string, options?: NotificationOptions) {
  if (!('Notification' in window)) return
  if (permission.value !== 'granted') return
  new Notification(title, {
    icon: '/favicon.ico',
    ...options,
  })
}

/** 请求权限并发送通知（首次调用时会弹出授权框） */
async function requestAndNotify(title: string, options?: NotificationOptions) {
  const granted = await requestPermission()
  if (granted) {
    notify(title, options)
  }
}

/** 失败率告警：当队列失败数超过阈值时发送 */
function checkFailureAlert(
  failed: number,
  total: number,
  threshold: number = 0.3,
) {
  if (total === 0) return
  const rate = failed / total
  if (rate >= threshold) {
    notify('⚠️ AI 分析失败率告警', {
      body: `当前失败率 ${(rate * 100).toFixed(0)}%（${failed}/${total}），建议检查模型状态`,
      tag: 'failure-alert',
    })
  }
}

export function useNotification() {
  return {
    permission,
    requestPermission,
    notify,
    requestAndNotify,
    checkFailureAlert,
  }
}
