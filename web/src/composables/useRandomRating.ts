/** 随机评分页的数据与动作：随机抽图片素材、保存 1-5 星评分、删除。
 *
 * 素材库「随机评分」入口使用；只抽 `media_type=image` 的未删除素材。
 * **优先抽未评分图片**（rating 为空或 0），全部评完后回退到已评分图片；
 * 抽到当前这张或最近 12 张时重抽，避免连点「换一张」反复看到同一张。
 */

import { ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import {
  fetchInspirations,
  moveToTrash,
  updateRating,
  type InspirationOut,
  type TrashReason,
} from '@/api/inspirations'
import { getApiErrorMessage } from '@/utils/apiError'

/** 最近抽到的 id：只用于本页去重，离开页面即丢弃 */
const RECENT_LIMIT = 12

/** 一次随机抽题结果：未评分池空了会自动回退到全部图片 */
interface RandomPick {
  item: InspirationOut | null
  total: number
  unrated: boolean
}

export function useRandomRating() {
  const item = ref<InspirationOut | null>(null)
  const total = ref(0)
  /** 当前这张是否来自「未评分」池；false 表示未评分已评完，回退到全部图片 */
  const showingUnrated = ref(true)
  const loading = ref(false)
  const saving = ref(false)
  const deleting = ref(false)
  const ratedCount = ref(0)
  const error = ref<string | null>(null)
  const recentIds: string[] = []

  async function fetchRandomPage(unrated: boolean) {
    return fetchInspirations({
      page: 1,
      size: 1,
      sort: 'random',
      media_type: 'image',
      ...(unrated ? { unrated: true } : {}),
    })
  }

  /** 先抽未评分池；池子为空才回退到全部图片，避免已评分图挤掉待评图 */
  async function pickOne(): Promise<RandomPick> {
    const unratedPage = await fetchRandomPage(true)
    if (unratedPage.items.length > 0) {
      return { item: unratedPage.items[0], total: unratedPage.total, unrated: true }
    }
    const allPage = await fetchRandomPage(false)
    return { item: allPage.items[0] ?? null, total: allPage.total, unrated: false }
  }

  async function loadRandom(): Promise<void> {
    if (loading.value) return
    loading.value = true
    error.value = null
    try {
      let picked = await pickOne()
      // 最多重抽 4 次：图片很少时允许最终返回重复项，不能死循环
      for (
        let i = 0;
        i < 3 &&
        picked.item &&
        (picked.item.id === item.value?.id || recentIds.includes(picked.item.id));
        i += 1
      ) {
        picked = await pickOne()
      }
      item.value = picked.item
      total.value = picked.total
      showingUnrated.value = picked.unrated
      if (picked.item) {
        recentIds.push(picked.item.id)
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

  /** 把指定图片（缺省为当前图）移入垃圾桶；返回是否成功 */
  async function removeCurrent(targetId?: string, reason?: TrashReason): Promise<boolean> {
    const id = targetId ?? item.value?.id
    if (!id || deleting.value) return false
    deleting.value = true
    try {
      await moveToTrash(id, reason)
      Message.success('已移入垃圾桶')
      return true
    } catch (e) {
      Message.error(getApiErrorMessage(e, '删除失败'))
      return false
    } finally {
      deleting.value = false
    }
  }

  return {
    item,
    total,
    showingUnrated,
    loading,
    saving,
    deleting,
    ratedCount,
    error,
    loadRandom,
    rate,
    removeCurrent,
  }
}
