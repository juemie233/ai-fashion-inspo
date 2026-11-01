/** 触觉反馈封装（P2-7）。
 *
 * 三条纪律：
 * 1. **不能影响主流程**——振动失败（模拟器、系统关闭触觉、设备不支持）一律吞掉；
 * 2. 只在「用户主动操作成功」时振，不要在列表滚动/自动刷新时振；
 * 3. 全部走本文件，测试里只 mock 这一个模块。
 */

import * as Haptics from 'expo-haptics'

/** 安全触发：同步异常与异步拒绝都吞掉 */
function fire(run: () => Promise<unknown>): void {
  try {
    void run().catch(() => {})
  } catch {
    // expo-haptics 在部分环境直接抛（无振动马达），忽略
  }
}

/** 轻触反馈：收藏、多选、切换 Tab */
export function selectionFeedback(): void {
  fire(() => Haptics.selectionAsync())
}

/** 成功反馈：上传完成、批量操作成功、标签保存成功 */
export function successFeedback(): void {
  fire(() => Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success))
}

/** 失败/警示反馈：批量操作失败、上传失败 */
export function warningFeedback(): void {
  fire(() => Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning))
}
