/** 采集任务漏斗视图域：漏斗弹窗状态与开关。 */

import { ref, computed } from 'vue'
import { Message } from '@arco-design/web-vue'
import type { ScraperTask, FunnelDiagnostics } from '@/types/scraper'

/** 任务漏斗视图状态与操作，由 ScraperView 消费。 */
export function useScraperFunnel() {
  
  const funnelTaskId = ref<number | null>(null)
  const funnelData = ref<FunnelDiagnostics | null>(null)

  /** 漏斗弹窗开关：绑定 n-modal 的 v-model:show，关闭时清空数据 */
  const funnelOpen = computed({
    get: () => funnelTaskId.value !== null,
    set: (v: boolean) => { if (!v) { funnelTaskId.value = null; funnelData.value = null } },
  })

  /** 打开/收起漏斗：再次点击同一任务则收起 */
  function viewFunnel(task: ScraperTask) {
    if (funnelTaskId.value === task.id) { funnelTaskId.value = null; funnelData.value = null; return }
    if (!task.diagnostics) { Message.warning('该任务无漏斗数据（旧版本采集的任务）'); return }
    try {
      funnelData.value = JSON.parse(task.diagnostics)
      funnelTaskId.value = task.id
    } catch { Message.error('漏斗数据解析失败') }
  }

  return { funnelTaskId, funnelData, funnelOpen, viewFunnel }
}
