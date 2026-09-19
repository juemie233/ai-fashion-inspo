/** 采集 ROI 漏斗域：按关键词 / 博主拉取「采集量 → 入库量 → 合格率」聚合。 */

import { computed, ref } from 'vue'
import apiClient from '@/api/client'

/** 关键词维度的一行（CDP 搜索采集，任务级口径） */
export interface RoiKeywordRow {
  keyword: string
  tasks: number
  found: number
  added: number
  /** 入库率（%）；发现数为 0 时为 null */
  add_rate: number | null
  /** 可归属到质量审核的素材数（0 表示暂无样本） */
  attributed: number
  approved: number
  rejected: number
  pending: number
  /** 合格率 = approved / (approved + rejected)；样本为 0 时为 null */
  approved_rate: number | null
}

/** 博主维度的一行：CDP 有任务级口径，f2 只有素材级口径（相关字段为 null） */
export interface RoiAuthorRow {
  name: string
  channel: 'cdp' | 'f2'
  platform: string | null
  tasks: number | null
  found: number | null
  added: number | null
  add_rate: number | null
  imported: number
  approved: number
  rejected: number
  pending: number
  approved_rate: number | null
}

/** 口径说明（哪部分没算进来、为什么） */
export interface RoiCoverage {
  tasks_scanned: number
  multi_keyword_tasks: number
  keyword_attributed: number
  f2_materials: number
  notes: string[]
}

/** 采集 ROI 聚合结果 */
export interface CollectionRoi {
  days: number
  by_keyword: RoiKeywordRow[]
  by_author: RoiAuthorRow[]
  coverage: RoiCoverage
}

/** 看板可选窗口（天） */
export const ROI_RANGES = [
  { label: '近 30 天', value: 30 },
  { label: '近 90 天', value: 90 },
  { label: '近 180 天', value: 180 },
  { label: '近 1 年', value: 365 },
] as const

/** 通道中文名 */
export const ROI_CHANNEL_LABELS: Record<string, string> = {
  cdp: '按博主采集',
  f2: '抖音 f2',
}

/** 百分比展示：null → 「—」（样本为 0 时不拿 0% 冒充结论） */
export function formatRate(rate: number | null): string {
  return rate === null || rate === undefined ? '—' : `${rate}%`
}

/** 计数展示：null → 「—」（该通道没有这个口径） */
export function formatCount(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : String(value)
}

/** 采集 ROI 状态与加载，由 ScraperRoiPanel 消费。 */
export function useScraperRoi() {
  const roi = ref<CollectionRoi | null>(null)
  const loading = ref(false)
  const days = ref(180)
  /** 当前维度：关键词 / 博主 */
  const dimension = ref<'keyword' | 'author'>('keyword')

  async function loadRoi() {
    loading.value = true
    try {
      const r = await apiClient.get<CollectionRoi>('/scraper/collection-roi', {
        params: { days: days.value },
      })
      roi.value = r.data
    } catch {
      roi.value = null
    } finally {
      loading.value = false
    }
  }

  /** 有内容可展示（两个维度都空时提示「窗口内暂无采集数据」） */
  const hasRows = computed(
    () => (roi.value?.by_keyword.length ?? 0) > 0 || (roi.value?.by_author.length ?? 0) > 0,
  )
  const notes = computed(() => roi.value?.coverage.notes ?? [])

  return { roi, loading, days, dimension, loadRoi, hasRows, notes }
}
