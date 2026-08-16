/** 采集统计看板域：拉取近 N 天的采集任务聚合统计。 */

import { ref } from 'vue'
import apiClient from '@/api/client'

/** 平台分布项 */
export interface PlatformStat {
  platform: string
  tasks: number
  found: number
  added: number
  completed: number
}

/** 按日分布项 */
export interface DayStat {
  date: string
  tasks: number
  added: number
  failed: number
}

/** 采集统计聚合结果 */
export interface ScraperStats {
  days: number
  total_tasks: number
  completed: number
  failed: number
  success_rate: number
  total_found: number
  total_added: number
  by_platform: PlatformStat[]
  by_day: DayStat[]
}

/** 统计看板状态与加载，由 ScraperStatsPanel 消费。 */
export function useScraperStats() {
  const stats = ref<ScraperStats | null>(null)
  const loading = ref(false)

  async function loadStats(days = 30) {
    loading.value = true
    try {
      const r = await apiClient.get('/scraper/stats', { params: { days } })
      stats.value = r.data
    } catch { stats.value = null }
    finally { loading.value = false }
  }

  return { stats, loading, loadStats }
}
