/** 标签治理的数据层：颜色剥离 / 新标签观察名单 / 别名建议 / 标签洞察。
 *
 * 为什么单独一层：这四个入口共用同一批接口，且都要处理「loading / 逐条失败 / 操作后刷新」；
 * 放进组件里会让每个面板重复一遍状态机。组件只渲染，动作与错误提示都收敛到这里。
 */

import { computed, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import {
  batchDeleteTags,
  createAlias,
  fetchTagTrend,
  fetchTagsGrouped,
  mergeTags,
} from '@/api/tags'
import type { TagItem } from '@/api/tags'
import {
  fetchAliasSuggestions,
  fetchTagCooccurrence,
  fetchWatchList,
  keepWatchTags,
  stripColorApply,
  stripColorDryRun,
  type ColorStripPreview,
  type WatchTag,
} from '@/api/tagAdvanced'
import { getApiErrorMessage } from '@/utils/apiError'
import { MERGE_SIMILARITY, summarizeUsage, USAGE_BUCKETS } from '@/utils/tagGovernance'
import type { TagDuplicatePair } from '@/types/tag'

/** 颜色前缀剥离：先 preview 再 apply（apply 写历史，可回滚） */
export function useColorStrip() {
  const category = ref('item_type')
  const previewing = ref(false)
  const applying = ref(false)
  const preview = ref<ColorStripPreview | null>(null)

  /** 预览里「会动手」的数量（重命名 + 合并） */
  const actionable = computed(() =>
    preview.value ? preview.value.renamed + preview.value.merged : 0,
  )

  async function runPreview(): Promise<void> {
    previewing.value = true
    try {
      preview.value = await stripColorDryRun({ category: category.value })
      if (actionable.value === 0) {
        Message.info('这个类别里没有需要剥离颜色前缀的标签')
      }
    } catch (e) {
      Message.error(getApiErrorMessage(e, '预览失败'))
    } finally {
      previewing.value = false
    }
  }

  async function runApply(): Promise<boolean> {
    applying.value = true
    try {
      const result = await stripColorApply({ category: category.value })
      if (result.renamed + result.merged === 0) {
        Message.info('没有需要处理的标签（可能已被处理过）')
        return false
      }
      Message.success(
        `已剥离：重命名 ${result.renamed} · 合并 ${result.merged} · ` +
          `重指向关联 ${result.links_repointed} · 补建颜色关联 ${result.color_links_added}`,
      )
      preview.value = null
      return true
    } catch (e) {
      Message.error(getApiErrorMessage(e, '执行失败'))
      return false
    } finally {
      applying.value = false
    }
  }

  return { category, previewing, applying, preview, actionable, runPreview, runApply }
}

/** 新标签观察名单：列出可删可保留的 AI 新标签 */
export function useWatchList() {
  const days = ref(7)
  const maxUsage = ref(1)
  const loading = ref(false)
  const busy = ref(false)
  const items = ref<WatchTag[]>([])
  const selected = ref<number[]>([])
  const cutoff = ref('')
  /** 真实命中数（列表按 limit 截断，items.length 只是本次返回条数） */
  const total = ref(0)

  async function load(): Promise<void> {
    loading.value = true
    try {
      const data = await fetchWatchList({ days: days.value, max_usage: maxUsage.value })
      items.value = data.items
      cutoff.value = data.cutoff
      total.value = data.total ?? data.items.length
      selected.value = []
    } catch (e) {
      Message.error(getApiErrorMessage(e, '加载观察名单失败'))
    } finally {
      loading.value = false
    }
  }

  function onSelectionChange(keys: (string | number)[]): void {
    selected.value = keys.map(Number)
  }

  /** 确认保留：source 改 manual，之后不再进名单 */
  async function doKeep(): Promise<boolean> {
    if (selected.value.length === 0) return false
    busy.value = true
    try {
      const result = await keepWatchTags(selected.value)
      Message.success(`已确认保留 ${result.kept} 个（改为手动标签，不再进观察名单）`)
      await load()
      return true
    } catch (e) {
      Message.error(getApiErrorMessage(e, '确认保留失败'))
      return false
    } finally {
      busy.value = false
    }
  }

  async function doDelete(): Promise<boolean> {
    if (selected.value.length === 0) return false
    busy.value = true
    try {
      await batchDeleteTags(selected.value)
      Message.success(`已删除 ${selected.value.length} 个标签`)
      await load()
      return true
    } catch (e) {
      Message.error(getApiErrorMessage(e, '删除失败'))
      return false
    } finally {
      busy.value = false
    }
  }

  return {
    days,
    maxUsage,
    loading,
    busy,
    items,
    selected,
    cutoff,
    total,
    load,
    onSelectionChange,
    doKeep,
    doDelete,
  }
}

/** 别名建议：只列 0.60~0.75 的同义变体（≥0.75 的重复对走「重复标签」面板） */
export function useAliasSuggestions() {
  /** 两侧都存在的配对（后端类型允许 null，实际只会返回两侧都有的） */
  type Pair = TagDuplicatePair & {
    tag_a: NonNullable<TagDuplicatePair['tag_a']>
    tag_b: NonNullable<TagDuplicatePair['tag_b']>
  }

  const loading = ref(false)
  const busy = ref(false)
  const pairs = ref<Pair[]>([])
  const dismissed = ref<Set<string>>(new Set())

  function pairKey(pair: Pair): string {
    return `${pair.tag_a.id}-${pair.tag_b.id}`
  }

  const suggestions = computed(() =>
    pairs.value
      .filter((pair) => pair.similarity < MERGE_SIMILARITY)
      .filter((pair) => !dismissed.value.has(pairKey(pair))),
  )

  async function load(): Promise<void> {
    loading.value = true
    try {
      // 专门的区间接口：0.60~0.75 的「同义变体」（区间过滤在后端截断之前，
      // 复用 /tags/duplicates 会拿到全 ≥0.75 的重复对、过滤后为空）
      const data = await fetchAliasSuggestions({ min_similarity: 0.6, max_similarity: 0.75 })
      const raw = data.pairs ?? []
      pairs.value = raw.filter((pair): pair is Pair => Boolean(pair.tag_a && pair.tag_b))
    } catch (e) {
      Message.error(getApiErrorMessage(e, '扫描失败'))
    } finally {
      loading.value = false
    }
  }

  function ignore(pair: Pair): void {
    dismissed.value = new Set([...dismissed.value, pairKey(pair)])
  }

  /** 把 tag_b 登记为 tag_a 的别名（只影响以后打标的归一化，不动已有素材关联） */
  async function makeAlias(pair: Pair): Promise<boolean> {
    busy.value = true
    try {
      await createAlias(pair.tag_a.id, pair.tag_b.name)
      Message.success(`已把「${pair.tag_b.name}」登记为「${pair.tag_a.name}」的别名`)
      ignore(pair)
      return true
    } catch (e) {
      Message.error(getApiErrorMessage(e, '设别名失败'))
      return false
    } finally {
      busy.value = false
    }
  }

  /** 直接把 tag_b 合并进 tag_a（素材关联一并指过去） */
  async function doMerge(pair: Pair): Promise<boolean> {
    busy.value = true
    try {
      await mergeTags(pair.tag_b.id, pair.tag_a.id)
      Message.success(`已把「${pair.tag_b.name}」合并到「${pair.tag_a.name}」`)
      ignore(pair)
      return true
    } catch (e) {
      Message.error(getApiErrorMessage(e, '合并失败'))
      return false
    } finally {
      busy.value = false
    }
  }

  return { loading, busy, suggestions, pairKey, load, ignore, makeAlias, doMerge }
}

/** 标签洞察：使用趋势（月）+ 常一起出现的标签 */
export function useTagInsight() {
  const loading = ref(false)
  const trend = ref<Array<{ bucket: string; count: number }>>([])
  const cooccurrence = ref<Array<{ id: number; name: string; category: string; count: number }>>([])
  const materials = ref(0)
  const tagId = ref<number | null>(null)

  /** 只画最近 6 个月，避免长尾把柱子压平 */
  const recentTrend = computed(() => trend.value.slice(-6))
  /** 柱子高度基准（相对最大值） */
  const maxCount = computed(() => Math.max(1, ...recentTrend.value.map((p) => p.count)))

  async function load(id: number): Promise<void> {
    tagId.value = id
    loading.value = true
    try {
      const [trendData, cooc] = await Promise.all([
        fetchTagTrend(id, 'month'),
        fetchTagCooccurrence(id, 8),
      ])
      trend.value = trendData.trend ?? []
      cooccurrence.value = cooc.items ?? []
      materials.value = cooc.materials ?? 0
    } catch (e) {
      // 小卡是辅助信息：失败不打断主流程
      Message.error(getApiErrorMessage(e, '加载标签洞察失败'))
      trend.value = []
      cooccurrence.value = []
    } finally {
      loading.value = false
    }
  }

  /** 跟随选中标签自动加载（组件里直接传 tag ref） */
  function follow(tag: import('vue').Ref<TagItem | null>): void {
    watch(
      () => tag.value?.id,
      (id) => {
        if (id) {
          void load(id)
        } else {
          trend.value = []
          cooccurrence.value = []
        }
      },
      { immediate: true },
    )
  }

  return { loading, trend, cooccurrence, materials, recentTrend, maxCount, load, follow }
}

/** 长尾标签：按用量分桶 + 批量合并/删除（数据一次性加载后在内存里分桶） */
export function useLongTailTags() {
  /** 只渲染前这么多行（22k 规模下全渲染会卡） */
  const PREVIEW_LIMIT = 200

  const allTags = ref<TagItem[]>([])
  const loading = ref(false)
  const busy = ref(false)
  const bucket = ref('one')
  const selected = ref<number[]>([])
  const targetKeyword = ref('')

  const buckets = computed(() => summarizeUsage(allTags.value))

  const filtered = computed(() => {
    const range = USAGE_BUCKETS.find((x) => x.key === bucket.value)
    if (!range) return []
    return allTags.value
      .filter((tag) => {
        const usage = tag.usage_count ?? 0
        return usage >= range.min && usage <= range.max
      })
      .sort((a, b) => (a.usage_count ?? 0) - (b.usage_count ?? 0) || a.name.localeCompare(b.name))
  })

  const visibleRows = computed(() => filtered.value.slice(0, PREVIEW_LIMIT))

  /** 合并目标：按名称精确匹配（避免一次渲染两万多个选项） */
  const targetTag = computed(() => {
    const keyword = targetKeyword.value.trim()
    if (!keyword) return null
    return allTags.value.find((tag) => tag.name === keyword) ?? null
  })

  const selectedRows = computed(() =>
    allTags.value.filter((tag) => selected.value.includes(tag.id)),
  )

  async function load(): Promise<void> {
    loading.value = true
    try {
      const groups = await fetchTagsGrouped()
      allTags.value = groups.flatMap((group) => group.tags)
      selected.value = []
    } catch (e) {
      Message.error(getApiErrorMessage(e, '加载标签失败'))
    } finally {
      loading.value = false
    }
  }

  function onBucketChange(value: string | number | boolean): void {
    bucket.value = String(value)
    selected.value = []
  }

  function onSelectionChange(keys: (string | number)[]): void {
    selected.value = keys.map(Number)
  }

  async function doMerge(): Promise<boolean> {
    const target = targetTag.value
    if (!target) {
      Message.warning('请先把「合并到」填成某个已存在的标签名（精确匹配）')
      return false
    }
    const sources = selectedRows.value.filter((tag) => tag.id !== target.id)
    if (sources.length === 0) {
      Message.warning('没有可合并的标签')
      return false
    }
    busy.value = true
    let ok = 0
    let failed = 0
    try {
      // 后端 /tags/merge 是单对合并：顺序执行，单个失败不影响其余
      for (const source of sources) {
        try {
          await mergeTags(source.id, target.id)
          ok += 1
        } catch {
          failed += 1
        }
      }
      Message.success(`已合并 ${ok} 个到「${target.name}」` + (failed ? `，${failed} 个失败` : ''))
      await load()
      return ok > 0
    } finally {
      busy.value = false
    }
  }

  async function doDelete(): Promise<boolean> {
    if (selected.value.length === 0) return false
    busy.value = true
    try {
      await batchDeleteTags(selected.value)
      Message.success(`已删除 ${selected.value.length} 个标签`)
      await load()
      return true
    } catch (e) {
      Message.error(getApiErrorMessage(e, '删除失败'))
      return false
    } finally {
      busy.value = false
    }
  }

  return {
    PREVIEW_LIMIT,
    allTags,
    loading,
    busy,
    bucket,
    selected,
    targetKeyword,
    buckets,
    filtered,
    visibleRows,
    targetTag,
    selectedRows,
    load,
    onBucketChange,
    onSelectionChange,
    doMerge,
    doDelete,
  }
}
