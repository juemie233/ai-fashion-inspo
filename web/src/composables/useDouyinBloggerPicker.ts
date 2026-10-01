/**
 * 采集页「按博主全量下载」的博主选择器：搜索已登记的抖音博主 → 拿到可直接点名的主页标识。
 *
 * 为什么需要它：f2 卡片原来只能**手粘主页链接**（用户得去抖音复制一次），而库里本来就有
 * 480+ 位抖音博主、名字与主页链接都是现成的。这里把「搜名字 → 选中 → 提交」接起来，
 * 并为缺主页标识的博主提供一键自动解析（复用后端按素材作品 ID 反查作者的能力）。
 *
 * 两处细节：
 * 1. **远程搜索 + 已选项缓存**：Arco 的远程 search 会整体替换 options，已选中的人会从
 *    列表里消失——所以选中时把整条记录存进 `selectedMap`，标签与提交都从缓存读。
 * 2. **竞态丢弃**：搜索是逐字的，先发的请求可能后回来；用自增 token 丢掉过期响应
 *    （否则输入「唐」时的结果会盖掉「唐思瑶」的结果）。
 */

import { computed, ref } from 'vue'
import { Message } from '@arco-design/web-vue'

import { bloggersApi } from '@/api/persons'
import { bloggerProfileKey } from '@/utils/bloggerWorks'
import type { Person } from '@shared/types/person'

/** 选择器候选项（只用到人物列表里这几个字段） */
export type PickerBlogger = Person

/** 默认候选：按素材数倒序取前 N 位（最常用的博主排在最前，打开就能选） */
const DEFAULT_SIZE = 40

export function useDouyinBloggerPicker() {
  /** 当前候选项（随搜索变化） */
  const options = ref<PickerBlogger[]>([])
  const loading = ref(false)
  /** 已选 ID（v-model 给 a-select） */
  const selectedIds = ref<number[]>([])
  /** 已选记录缓存：远程搜索换掉 options 后，标签与提交仍要有完整信息 */
  const selectedMap = ref<Record<number, PickerBlogger>>({})
  /** 正在自动解析主页标识（每人一次联网反查，约 20~40 秒） */
  const resolving = ref(false)
  /** 解析进度（串行逐个；界面据此显示「解析中 2/3」，避免看起来像卡死） */
  const progress = ref<{ done: number; total: number }>({ done: 0, total: 0 })

  /** 竞态 token：只有最后一次搜索的响应会被采用 */
  let searchToken = 0

  const selected = computed(() =>
    selectedIds.value.map((id) => selectedMap.value[id]).filter(Boolean),
  )

  /** a-select 的 options：缺主页标识的博主在标签里点明，避免选中后才发现提交不了 */
  const selectOptions = computed(() =>
    options.value.map((person) => ({
      value: person.id,
      label:
        person.name +
        (person.inspiration_count != null ? `（${person.inspiration_count} 条素材）` : '') +
        (bloggerProfileKey(person) ? '' : ' · 缺主页链接'),
    })),
  )

  /** 已选但还没有主页标识的人：提交前必须先解析（否则后端只会跳过她们） */
  const missing = computed(() => selected.value.filter((p) => !bloggerProfileKey(p)))

  /** 已选博主的可提交标识（主页链接优先，退回 sec_user_id） */
  const selectedKeys = computed(() =>
    selected.value.map(bloggerProfileKey).filter((key) => Boolean(key)),
  )

  /** 搜索/加载候选：空关键字 = 取素材数最多的前 N 位 */
  async function search(keyword = ''): Promise<void> {
    const token = ++searchToken
    loading.value = true
    try {
      const result = await bloggersApi.fetchList({
        platform: 'douyin',
        search: keyword.trim() || undefined,
        size: DEFAULT_SIZE,
        sort: 'count',
        // 平铺：选择器按账号点名，人物组折叠会把组内账号藏进 group_members
        grouped: false,
      })
      if (token !== searchToken) return
      options.value = result.items
      // 顺手刷新已选项的资料：刚解析出来的主页链接要立刻能提交
      for (const item of result.items) {
        if (selectedMap.value[item.id]) selectedMap.value[item.id] = item
      }
    } catch (e) {
      if (token !== searchToken) return
      Message.error(e instanceof Error ? e.message : '加载抖音博主列表失败')
    } finally {
      if (token === searchToken) loading.value = false
    }
  }

  /**
   * 选中变化：把 options 里能拿到的记录存进缓存（选过的仍留在缓存里）。
   *
   * 入参声明为 `unknown` 是刻意的：Arco `a-select` 的 change 事件值是宽泛联合类型
   * （单值 / 数组 / 字符串都可能有），在模板里强转只会把类型错误挪个地方。这里就地
   * 归一成数字 ID 数组，同时写回 `selectedIds`——界面上 `v-model` 会写同一个值
   * （幂等），但**不能**依赖它：程序化调用（测试、以后从别处回填选择）时本函数要自成闭环。
   */
  function onChange(value: unknown): void {
    const ids = (Array.isArray(value) ? value : [value])
      .map((v) => Number(v))
      .filter((n) => Number.isFinite(n))
    selectedIds.value = ids
    const next: Record<number, PickerBlogger> = {}
    for (const id of ids) {
      const cached = selectedMap.value[id]
      const found = options.value.find((o) => o.id === id)
      const person = found ?? cached
      if (person) next[id] = person
    }
    selectedMap.value = next
  }

  /**
   * 对「已选但缺主页标识」的博主逐个自动解析（后端按她的素材作品 ID 反查作者）。
   *
   * **串行**（不并发）：每人一次 f2 运行、共用一个抖音 cookie 会话，并发容易触发风控；
   * 代价是选 3 个人最长约 2 分钟，所以用 `progress` 让界面显示「解析中 2/3」。
   *
   * Returns: 解析成功的人数（失败会逐条提示原因，不抛异常）。
   */
  async function resolveMissing(): Promise<number> {
    const targets = missing.value
    if (!targets.length) return 0
    resolving.value = true
    progress.value = { done: 0, total: targets.length }
    let ok = 0
    try {
      for (const person of targets) {
        try {
          const result = await bloggersApi.resolveDouyinProfile(person.id)
          if (!result.ok) {
            Message.warning(`「${person.name}」自动解析失败：${result.reason || '未知原因'}`)
            continue
          }
          const merged: PickerBlogger = {
            ...person,
            profile_url: result.profile_url || person.profile_url,
            platform_user_id: result.platform_user_id || person.platform_user_id,
            ip_location: result.ip_location || person.ip_location,
          }
          selectedMap.value = { ...selectedMap.value, [person.id]: merged }
          ok += 1
        } catch (e) {
          Message.error(
            `「${person.name}」自动解析失败：${e instanceof Error ? e.message : '请求出错'}`,
          )
        }
        progress.value = { done: progress.value.done + 1, total: targets.length }
      }
      if (ok) Message.success(`已解析 ${ok} 位博主的主页链接，可以开始下载了`)
    } finally {
      resolving.value = false
      progress.value = { done: 0, total: 0 }
    }
    return ok
  }

  /** 清空选择（提交成功后调用） */
  function clear(): void {
    selectedIds.value = []
    selectedMap.value = {}
  }

  return {
    options,
    selectOptions,
    loading,
    selectedIds,
    selected,
    selectedKeys,
    missing,
    resolving,
    progress,
    search,
    onChange,
    resolveMissing,
    clear,
  }
}
