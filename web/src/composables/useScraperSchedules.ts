/** 定时采集计划域：计划列表、创建、启停、立即执行与删除。 */

import { ref, computed, onMounted } from 'vue'
import { useMessage } from 'naive-ui'
import apiClient from '@/api/client'
import { formatDate } from '@/utils/format'
import { extractHistoryKeywords } from '@/utils/scraperKeywords'
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

/** 排序方式显示文案 */
export const SORT_MODE_LABELS: Record<string, string> = {
  general: '综合', latest: '最新', popular: '最热',
}

/** 定时采集页签状态与操作，由 ScraperScheduleTab 消费。 */
export function useScraperSchedules() {
  const message = useMessage()

  const schedules = ref<ScraperSchedule[]>([])
  const loading = ref(false)
  const creating = ref(false)
  const updatingId = ref<number | null>(null)
  const togglingId = ref<number | null>(null)
  const runningId = ref<number | null>(null)
  const deletingId = ref<number | null>(null)

  // 新建计划表单
  const formPlatform = ref('xiaohongshu')
  /** 轮换关键词（多选）：每次执行轮流使用其中一个，可选历史关键词也可手动输入 */
  const formKeywords = ref<string[]>([])
  const formMaxCount = ref(20)
  const formSortMode = ref('general')
  const formInterval = ref(1440)
  const formEnabled = ref(true)

  // 历史关键词：来自已完成采集任务（最近使用优先去重），供轮换关键词选择
  const historyKeywords = ref<string[]>([])
  const keywordOptions = computed(() =>
    historyKeywords.value.map((k) => ({ label: k, value: k })),
  )

  /** 手动输入创建关键词：支持逗号/顿号分隔一次创建多个 */
  function onCreateKeyword(label: string) {
    const parts = label.split(/[,，、]/).map((s) => s.trim()).filter(Boolean)
    return parts.length > 0 ? parts : null
  }

  /** 加载历史关键词：拉取最近 200 条已完成任务提取去重关键词 */
  async function loadHistoryKeywords() {
    try {
      const { data } = await apiClient.get('/scraper/tasks', {
        params: { sort: 'newest', size: 200 },
      })
      historyKeywords.value = extractHistoryKeywords(data.items || [])
    } catch {
      // 历史关键词加载失败不影响手动输入，静默降级
    }
  }

  onMounted(loadHistoryKeywords)

  async function loadSchedules() {
    loading.value = true
    try {
      const r = await apiClient.get('/scraper/schedules')
      schedules.value = r.data
    } catch (e: any) {
      const detail = e.response?.data?.detail
      message.error(typeof detail === 'string' ? detail : '加载定时计划失败')
    } finally { loading.value = false }
  }

  async function createSchedule() {
    try {
      creating.value = true
      const payload: any = {
        platform: formPlatform.value,
        // 轮换关键词：多选为数组，手动输入的条目再按逗号/顿号拆分
        keywords: formKeywords.value.flatMap((k) =>
          k.split(/[,，、]/).map((s) => s.trim()).filter(Boolean),
        ),
        max_count: formMaxCount.value,
        interval_minutes: formInterval.value,
        enabled: formEnabled.value,
      }
      if (formPlatform.value === 'xiaohongshu' && formSortMode.value !== 'general') payload.sort_mode = formSortMode.value
      await apiClient.post('/scraper/schedules', payload)
      message.success('定时计划已创建')
      formKeywords.value = []
      loadSchedules()
    } catch (e: any) {
      message.error(typeof e.response?.data?.detail === 'string' ? e.response.data.detail : '创建失败')
    } finally { creating.value = false }
  }

  /** 更新计划（轮换关键词/数量/排序/间隔），成功返回 true 供调用方关闭弹窗 */
  async function updateSchedule(
    id: number,
    payload: { keywords: string[]; max_count: number; sort_mode: string | null; interval_minutes: number },
  ): Promise<boolean> {
    try {
      updatingId.value = id
      const body: any = {
        keywords: payload.keywords.flatMap((k) =>
          k.split(/[,，、]/).map((s) => s.trim()).filter(Boolean),
        ),
        max_count: payload.max_count,
        interval_minutes: payload.interval_minutes,
        sort_mode: payload.sort_mode,
      }
      await apiClient.patch(`/scraper/schedules/${id}`, body)
      message.success('定时计划已更新')
      loadSchedules()
      return true
    } catch (e: any) {
      message.error(typeof e.response?.data?.detail === 'string' ? e.response.data.detail : '更新失败')
      return false
    } finally { updatingId.value = null }
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

  return {
    schedules, loading, creating, updatingId, togglingId, runningId, deletingId,
    formPlatform, formKeywords, formMaxCount, formSortMode, formInterval, formEnabled,
    historyKeywords, keywordOptions, onCreateKeyword,
    loadSchedules, createSchedule, updateSchedule, toggleSchedule, runNow, deleteSchedule, formatDate,
  }
}
