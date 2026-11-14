/** 随机评分页的数据与动作：随机抽图片素材、保存 1-5 星评分。
 *
 * 素材库「随机评分」入口使用；只抽 `media_type=image` 的未删除素材。
 * 抽到当前这张或最近 12 张时重抽，避免连点「换一张」反复看到同一张。
 */

import { ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { fetchInspirations, updateRating, type InspirationOut } from '@/api/inspirations'
import { getApiErrorMessage } from '@/utils/apiError'

/** 最近抽到的 id：只用于本页去重，离开页面即丢弃 */
const RECENT_LIMIT = 12

export function useRandomRating() {
  const item = ref<InspirationOut | null>(null)
  const total = ref(0)
  const loading = ref(false)
  const saving = ref(false)
  const ratedCount = ref(0)
  const error = ref<string | null>(null)
  const recentIds: string[] = []

  async function loadRandom(): Promise<void> {
    if (loading.value) return
    loading.value = true
    error.value = null
    try {
      // 最多重抽 4 次：图片很少时允许最终返回重复项，不能死循环
      let candidate: InspirationOut | null = null
      for (let i = 0; i < 4; i += 1) {
        const data = await fetchInspirations({
          page: 1,
          size: 1,
          sort: 'random',
          media_type: 'image',
        })
        total.value = data.total
        const next = data.items[0] ?? null
        candidate = next
        if (!next) break
        if (next.id !== item.value?.id && !recentIds.includes(next.id)) break
      }
      item.value = candidate
      if (candidate) {
        recentIds.push(candidate.id)
        if (recentIds.length > RECENT_LIMIT) recentIds.shift()
      }
    } catch (e) {
      error.value = getApiErrorMessage(e, '加载随机素材失败')
    } finally {
      loading.value = false
    }
  }

  /** 保存评分；返回是否成功（成功后方可自动下一张） */
  async function rate(value: number): Promise<boolean> {
    if (!item.value || saving.value) return false
    saving.value = true
    try {
      const updated = await updateRating(item.value.id, value)
      item.value = { ...item.value, ...updated }
      ratedCount.value += 1
      Message.success(value > 0 ? `已评 ${value} 星` : '已清除评分')
      return true
    } catch (e) {
      Message.error(getApiErrorMessage(e, '保存评分失败'))
      return false
    } finally {
      saving.value = false
    }
  }

  return {
    item,
    total,
    loading,
    saving,
    ratedCount,
    error,
    loadRandom,
    rate,
  }
}
