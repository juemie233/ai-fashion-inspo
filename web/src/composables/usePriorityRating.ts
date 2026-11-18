/** 人物优先评分：按「已评占比」排队，逐位博主随机抽未评分图片来评。
 *
 * 与首页「随机评分」的区别：那边是全库随机抽一张；这里先按人物排队
 * （最缺评分的博主优先），当前博主的未评分图片评完后自动切下一位。
 *
 * 排序口径（与后端 `GET /api/bloggers/rating-priority` 一致，前端再做一次稳定排序，
 * 避免后端排序口径变化时页面顺序漂移）：
 * 已评占比（rated/image）升序 → 已评数升序 → 图片素材数升序 → blogger_id 升序。
 *
 * 无后台任务，不轮询；评分/删除直接调既有接口，失败只提示不假装成功。
 */

import { computed, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { moveToTrash, updateRating, type TrashReason } from '@/api/inspirations'
import { bloggersApi, type BloggerRatingPriorityItem, type PersonInspiration } from '@/api/persons'
import { getApiErrorMessage } from '@/utils/apiError'

/** 评分成功 → 换下一张的延迟（毫秒）：留出「已评 N 星」的反馈时间 */
const NEXT_DELAY_MS = 500

/** 已评占比：图片数为 0 时按 0 处理（正常队列里不会出现） */
function ratedRatio(item: BloggerRatingPriorityItem): number {
  return item.image_count > 0 ? item.rated_count / item.image_count : 0
}

/** 队列排序（纯函数，导出便于单测）：已评占比 → 已评数 → 图片数 → id */
export function sortPriorityQueue(
  items: readonly BloggerRatingPriorityItem[],
): BloggerRatingPriorityItem[] {
  return [...items].sort(
    (a, b) =>
      ratedRatio(a) - ratedRatio(b) ||
      a.rated_count - b.rated_count ||
      a.image_count - b.image_count ||
      a.blogger_id - b.blogger_id,
  )
}

export function usePriorityRating() {
  /** 完整优先队列（后端返回后前端再稳定排序一次） */
  const queue = ref<BloggerRatingPriorityItem[]>([])
  /** 后端符合条件的人物总数（可能大于 queue.length，因为 limit） */
  const queueTotal = ref(0)
  /** 当前处理到第几位博主（0-based） */
  const queueIndex = ref(0)
  /** 当前抽到的未评分图片 */
  const current = ref<PersonInspiration | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  const deleting = ref(false)
  /** 本次进入页面后已评的图片数 */
  const ratedCount = ref(0)
  const error = ref<string | null>(null)
  /** 本轮队列是否已全部走完（没有下一位博主） */
  const finished = ref(false)

  /** 当前博主（队列走完为 null） */
  const currentBlogger = computed<BloggerRatingPriorityItem | null>(
    () => queue.value[queueIndex.value] ?? null,
  )
  /** 队列位置「3/128」；没有博主时为空串 */
  const queuePosition = computed(() =>
    queue.value.length > 0
      ? `${Math.min(queueIndex.value + 1, queue.value.length)}/${queue.value.length}`
      : '',
  )
  /** 是否还有下一位博主可切 */
  const hasNextBlogger = computed(() => queueIndex.value + 1 < queue.value.length)
  /** 平均分展示：无样本显示 -- */
  const averageText = computed(() => {
    const blogger = currentBlogger.value
    if (!blogger) return '--'
    const average = blogger.rating_average
    return average == null || blogger.rated_count <= 0 ? '--' : average.toFixed(1)
  })

  /** 换一张 / 换一位博主的延迟计时器：换图或离开页面时清掉，避免重复请求 */
  let nextTimer: ReturnType<typeof setTimeout> | null = null

  function clearNextTimer(): void {
    if (nextTimer !== null) {
      clearTimeout(nextTimer)
      nextTimer = null
    }
  }

  /** 本地重新计算覆盖率与平均分（评分/删除后即时反馈，不必等刷新队列） */
  function refreshCoverage(blogger: BloggerRatingPriorityItem): void {
    blogger.coverage = blogger.image_count > 0 ? blogger.rated_count / blogger.image_count : 0
  }

  /** 评分成功后的本地统计：已评 +1、未评 -1、覆盖率/均分同步更新 */
  function applyRatingToStats(blogger: BloggerRatingPriorityItem, rating: number): void {
    const previousRated = blogger.rated_count
    const previousAverage = blogger.rating_average ?? 0
    blogger.rated_count = previousRated + 1
    blogger.unrated_count = Math.max(0, blogger.unrated_count - 1)
    blogger.rating_average =
      (previousAverage * previousRated + rating) / Math.max(1, blogger.rated_count)
    refreshCoverage(blogger)
  }

  /** 抽当前博主的一张未评分图片；抽不到（已评完）返回 false，由调用方决定是否切下一位 */
  async function loadCurrentImage(): Promise<boolean> {
    const blogger = currentBlogger.value
    if (!blogger) {
      current.value = null
      return false
    }
    loading.value = true
    error.value = null
    try {
      const data = await bloggersApi.fetchRandomUnratedImage(blogger.blogger_id)
      const picked = data.items?.[0] ?? null
      current.value = picked
      return picked !== null
    } catch (e) {
      // 失败要显式报错，不能静默当成「评完了」把用户带到下一位
      error.value = getApiErrorMessage(e, '加载待评分图片失败')
      current.value = null
      return false
    } finally {
      loading.value = false
    }
  }

  /** 切下一位还有未评分图片的博主；队列走完置 finished */
  async function advanceToNextBlogger(): Promise<void> {
    clearNextTimer()
    let nextIndex = queueIndex.value + 1
    // 跳过本地统计里已经评完的博主（评分/删除后 unrated_count 会变 0）
    while (nextIndex < queue.value.length && (queue.value[nextIndex]?.unrated_count ?? 0) <= 0) {
      nextIndex += 1
    }
    if (nextIndex >= queue.value.length) {
      queueIndex.value = queue.value.length
      current.value = null
      finished.value = true
      return
    }
    queueIndex.value = nextIndex
    finished.value = false
    const ok = await loadCurrentImage()
    // 抽不到图片且没有报错 = 这位博主的未评分图片其实已评完，继续往后找
    if (!ok && !error.value) await advanceToNextBlogger()
  }

  /** 评分自动换下一张：当前博主评完则切下一位 */
  async function afterRatedAdvance(): Promise<void> {
    clearNextTimer()
    const blogger = currentBlogger.value
    if (!blogger) return
    if (blogger.unrated_count <= 0) {
      await advanceToNextBlogger()
      return
    }
    const ok = await loadCurrentImage()
    if (!ok && !error.value) await advanceToNextBlogger()
  }

  function scheduleAdvance(): void {
    clearNextTimer()
    nextTimer = setTimeout(() => {
      nextTimer = null
      void afterRatedAdvance()
    }, NEXT_DELAY_MS)
  }

  /** 加载优先队列并抽第一张图 */
  async function loadQueue(): Promise<void> {
    if (loading.value) return
    loading.value = true
    error.value = null
    finished.value = false
    ratedCount.value = 0
    queueIndex.value = 0
    try {
      const data = await bloggersApi.fetchRatingPriority(1000)
      queue.value = sortPriorityQueue(data.items ?? [])
      queueTotal.value = data.total ?? queue.value.length
      if (queue.value.length === 0) {
        current.value = null
        finished.value = true
        return
      }
      const ok = await loadCurrentImage()
      if (!ok && !error.value) await advanceToNextBlogger()
    } catch (e) {
      // 队列加载失败：明确报错并保留空队列，不显示「已评完」误导用户
      error.value = getApiErrorMessage(e, '加载人物评分队列失败')
      queue.value = []
      current.value = null
    } finally {
      loading.value = false
    }
  }

  /** 换一张：同一博主再随机抽一张未评分图片 */
  async function nextImage(): Promise<void> {
    if (saving.value || deleting.value) return
    clearNextTimer()
    const ok = await loadCurrentImage()
    if (!ok && !error.value) await advanceToNextBlogger()
  }

  /** 换一位博主：跳过当前博主，直接处理下一位 */
  async function nextBlogger(): Promise<void> {
    if (saving.value || deleting.value) return
    await advanceToNextBlogger()
  }

  /** 保存评分；返回是否成功（成功后由调度器决定换图/切人） */
  async function rate(value: number): Promise<boolean> {
    const row = current.value
    if (!row || saving.value) return false
    saving.value = true
    try {
      const updated = await updateRating(row.inspiration_id, value)
      row.rating = updated?.rating ?? value
      const blogger = currentBlogger.value
      if (value > 0) {
        ratedCount.value += 1
        if (blogger) applyRatingToStats(blogger, value)
        Message.success(`已评 ${value} 星`)
        scheduleAdvance()
      } else {
        Message.info('已清除评分')
      }
      return true
    } catch (e) {
      // 评分失败不能增加本次已评数，也不能自动跳走（用户会以为评上了）
      Message.error(getApiErrorMessage(e, '保存评分失败'))
      return false
    } finally {
      saving.value = false
    }
  }

  /** 把当前图片移入垃圾桶（软删除）；成功后本地统计减一，并抽下一张/切下一位 */
  async function removeCurrent(targetId?: string, reason?: TrashReason): Promise<boolean> {
    const id = targetId ?? current.value?.inspiration_id
    if (!id || deleting.value) return false
    deleting.value = true
    clearNextTimer()
    try {
      await moveToTrash(id, reason)
      Message.success('已移入垃圾桶')
      const blogger = currentBlogger.value
      current.value = null
      if (blogger) {
        blogger.image_count = Math.max(0, blogger.image_count - 1)
        blogger.unrated_count = Math.max(0, blogger.unrated_count - 1)
        refreshCoverage(blogger)
      }
      if (blogger && blogger.unrated_count <= 0) {
        await advanceToNextBlogger()
      } else {
        const ok = await loadCurrentImage()
        if (!ok && !error.value) await advanceToNextBlogger()
      }
      return true
    } catch (e) {
      Message.error(getApiErrorMessage(e, '删除失败'))
      return false
    } finally {
      deleting.value = false
    }
  }

  /** 离开页面停止延迟换图（避免回调打到已销毁的界面） */
  function stop(): void {
    clearNextTimer()
  }

  return {
    queue,
    queueTotal,
    queueIndex,
    currentBlogger,
    current,
    loading,
    saving,
    deleting,
    ratedCount,
    error,
    finished,
    queuePosition,
    hasNextBlogger,
    averageText,
    loadQueue,
    nextImage,
    nextBlogger,
    rate,
    removeCurrent,
    stop,
  }
}
