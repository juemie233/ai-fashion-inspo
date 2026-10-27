/** 人物素材「随机浏览」状态：每次抽一张、可回看上一张。
 *
 * 为什么不塞进 `usePersonDetail`：那是「素材列表 + 分页 + 筛选」的主状态，随机浏览只是
 * 叠在上面的一层浏览体验；单独一个 composable 才能被单测直接驱动（不依赖组件挂载）。
 *
 * 设计取舍：
 * - **历史是本次会话内的栈**：「上一张」回到刚刚看过的那张，而不是「随机倒抽一张」——
 *   随机倒退会让人彻底迷路（同一个视觉位置可能反复出现也可能永远回不去）；
 * - 抽到重复的那张不特殊处理：素材量小（1~4 条的那 315 位博主）时重复是自然结果，
 *   强行去重会让「换一张」变成「换不出来」。
 */

import { computed, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { getApiErrorMessage } from '@/utils/apiError'
import type { PersonInspiration, PersonInspirationFilters } from '@/api/persons'

type PersonApi = {
  fetchRandom: (
    id: number,
    size?: number,
    filters?: PersonInspirationFilters,
  ) => Promise<{ items: PersonInspiration[]; total: number }>
}

export function usePersonRandomBrowse(
  api: () => PersonApi,
  personId: () => number,
  filters: () => PersonInspirationFilters,
) {
  const visible = ref(false)
  const loading = ref(false)
  const item = ref<PersonInspiration | null>(null)
  /** 本次浏览的历史（「上一张」用） */
  const history = ref<PersonInspiration[]>([])
  /** 满足当前筛选的素材总数（弹窗里显示「从 N 张里随机」） */
  const total = ref(0)

  const historyCount = computed(() => history.value.length)

  /** 换一张：把当前这张压进历史，再随机抽下一张 */
  async function next(): Promise<void> {
    if (loading.value) return
    loading.value = true
    try {
      const data = await api().fetchRandom(personId(), 1, filters())
      total.value = data.total ?? 0
      if (item.value) history.value = [...history.value, item.value]
      item.value = data.items?.[0] ?? null
    } catch (e) {
      Message.error(getApiErrorMessage(e, '随机抽取失败'))
    } finally {
      loading.value = false
    }
  }

  /** 上一张：从历史栈弹出（不发请求，纯本地回看） */
  function prev(): void {
    const last = history.value[history.value.length - 1]
    if (!last) return
    history.value = history.value.slice(0, -1)
    item.value = last
  }

  /** 打开随机浏览：首次进入才发请求；关闭不清历史（再打开能接着回看） */
  async function open(): Promise<void> {
    visible.value = true
    if (!item.value) await next()
  }

  function close(): void {
    visible.value = false
  }

  /** 换人物时清空：否则会先看到上一位的图再被替换 */
  function reset(): void {
    item.value = null
    history.value = []
    total.value = 0
    visible.value = false
  }

  return {
    visible,
    loading,
    item,
    total,
    historyCount,
    next,
    prev,
    open,
    close,
    reset,
  }
}
