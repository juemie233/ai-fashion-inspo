/** 定时采集计划域：计划列表、创建、启停、立即执行与删除。 */

import { ref } from 'vue'
import { useMessage } from 'naive-ui'
import apiClient from '@/api/client'
import type { ScraperSchedule } from '@/types/scraper'

/** 计划间隔选项（分钟） */
export const INTERVAL_OPTIONS = [
  { label: '每 1 小时', value: 60 },
  { label: '每 6 小时', value: 360 },
  { label: '每 12 小时', value: 720 },
  { label: '每天', value: 1440 },
  { label: '每 3 天', value: 4320 },
  { label: '每周', value: 10080 },
]

/** 间隔分钟数转中文文案 */
export function intervalLabel(minutes: number): string {
  return INTERVAL_OPTIONS.find(o => o.value === minutes)?.label || `${minutes} 分钟`
}

/** 定时采集页签状态与操作，由 ScraperScheduleTab 消费。 */
export function useScraperSchedules() {
  const message = useMessage()

  const schedules = ref<ScraperSchedule[]>([])
  const loading = ref(false)
  const creating = ref(false)
  const togglingId = ref<number | null>(null)
  const runningId = ref<number | null>(null)
  const deletingId = ref<number | null>(null)

  // 新建计划表单
  const formPlatform = ref('xiaohongshu')
  const formKeywords = ref('')
  const formMaxCount = ref(20)
  const formSortMode = ref('general')
  const formInterval = ref(1440)
  const formEnabled = ref(true)

  async function loadSchedules() {
    loading.value = true
    try {
      const r = await apiClient.get('/scraper/schedules')
      schedules.value = r.data
    } catch { message.error('加载定时计划失败') }
    finally { loading.value = false }
  }

  async function createSchedule() {
    try {
      creating.value = true
      const payload: any = {
        platform: formPlatform.value,
        keywords: formKeywords.value.split(',').map((k: string) => k.trim()).filter(Boolean),
        max_count: formMaxCount.value,
        interval_minutes: formInterval.value,
        enabled: formEnabled.value,
      }
      if (formPlatform.value === 'xiaohongshu' && formSortMode.value !== 'general') payload.sort_mode = formSortMode.value
      await apiClient.post('/scraper/schedules', payload)
      message.success('定时计划已创建')
      formKeywords.value = ''
      loadSchedules()
    } catch (e: any) {
      message.error(typeof e.response?.data?.detail === 'string' ? e.response.data.detail : '创建失败')
    } finally { creating.value = false }
  }

  /** 启用/停用计划 */
  async function toggleSchedule(s: ScraperSchedule) {
    try {
      togglingId.value = s.id
      await apiClient.patch(`/scraper/schedules/${s.id}`, { enabled: !s.enabled })
      message.success(s.enabled ? '已停用' : '已启用')
      loadSchedules()
    } catch (e: any) {
      message.error(e.response?.data?.detail || '操作失败')
    } finally { togglingId.value = null }
  }

  /** 立即执行一次计划 */
  async function runNow(s: ScraperSchedule) {
    try {
      runningId.value = s.id
      const r = await apiClient.post(`/scraper/schedules/${s.id}/run`)
      message.success(`已触发采集任务 #${r.data.task_id}`)
      loadSchedules()
    } catch (e: any) {
      message.error(e.response?.data?.detail || '触发失败')
    } finally { runningId.value = null }
  }

  async function deleteSchedule(s: ScraperSchedule) {
    try {
      deletingId.value = s.id
      await apiClient.delete(`/scraper/schedules/${s.id}`)
      message.success('已删除')
      loadSchedules()
    } catch (e: any) {
      message.error(e.response?.data?.detail || '删除失败')
    } finally { deletingId.value = null }
  }

  /** ISO 时间转本地显示 */
  function formatDate(d: string | null) {
    if (!d) return '-'
    try {
      const dt = new Date(d)
      return isNaN(dt.getTime()) ? '-' : dt.toLocaleString('zh-CN')
    } catch { return '-' }
  }

  return {
    schedules, loading, creating, togglingId, runningId, deletingId,
    formPlatform, formKeywords, formMaxCount, formSortMode, formInterval, formEnabled,
    loadSchedules, createSchedule, toggleSchedule, runNow, deleteSchedule, formatDate,
  }
}
