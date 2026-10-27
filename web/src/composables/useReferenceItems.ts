/** 已选参考图的元数据（缩略图/原图路径）：按 id 拉取 + 防抖 + 丢弃过期响应。
 *
 * 为什么需要防抖：参考 ID 输入框是手输的，v-model 每敲一个字符就会抛一次选择变化；
 * 直接按变化发请求 = 打一行 id 发几十个 `/inspirations?ids=`（实测链路里每个字符都触发，
 * 既浪费又会因响应乱序让缩略图条闪来闪去）。这里合并 300ms 内的连续变化。
 *
 * 为什么需要序号守卫：防抖只解决「手输」，选择器一次确认 10 张、或用户快速改选时仍可能
 * 有多个请求在飞；迟到的旧响应绝不能覆盖新结果（否则缩略图对不上实际参考顺序）。
 */

import { onBeforeUnmount, ref } from 'vue'

/** 缩略图条需要的最小字段（素材本身可能被删，缺席即少一张） */
interface ReferenceItem {
  id: string
  file_path: string
  thumbnail_path?: string | null
}

/** 默认防抖间隔：手输 id 时约 3 个字符的停顿，足够合并一次粘贴/连续输入 */
const DEFAULT_DELAY_MS = 300

export function useReferenceItems(
  fetcher: (ids: string[]) => Promise<ReferenceItem[]>,
  delay: number = DEFAULT_DELAY_MS,
) {
  const items = ref<ReferenceItem[]>([])
  const loading = ref(false)

  let ids: string[] = []
  let timer: ReturnType<typeof setTimeout> | null = null
  /** 请求序号：只有「最新一次」的响应能落地 */
  let seq = 0

  function clearTimer(): void {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
  }

  async function run(): Promise<void> {
    timer = null
    const current = ids
    if (!current.length) {
      items.value = []
      return
    }
    const my = ++seq
    loading.value = true
    try {
      const next = await fetcher(current)
      if (my !== seq) return // 迟到的旧响应：丢弃
      items.value = next
    } catch {
      // 缩略图只是辅助：拉不到就不显示，不影响出图
      if (my === seq) items.value = []
    } finally {
      if (my === seq) loading.value = false
    }
  }

  /** 选择变化（去重后防抖拉取）；空选择立刻清空且不发请求 */
  function set(nextIds: string[]): void {
    const key = nextIds.join(',')
    if (key === ids.join(',')) return
    ids = nextIds
    clearTimer()
    if (!ids.length) {
      items.value = []
      loading.value = false
      return
    }
    timer = setTimeout(() => void run(), delay)
  }

  /** 组件卸载时取消待发请求（避免卸载后仍发一次无用请求） */
  function dispose(): void {
    clearTimer()
  }

  onBeforeUnmount(dispose)

  return { items, loading, set, dispose }
}
