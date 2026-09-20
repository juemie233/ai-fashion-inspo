/** 素材库筛选域：筛选状态、选项配置、定位模式与快捷入口。
 *
 * 为什么单独抽出：首页（HomeView）把「筛选状态 + 选项 + setter」和「分页 /
 * 数据加载 / 合集 / 垃圾桶 / 质量审核 / 批量选择」混在一个 1027 行的视图里。
 * 这里只搬筛选这一条链路（含定位模式与主色调选项的加载），分页与数据加载仍留在
 * 视图——它们是「查询 → 拉数据 + 滚动」的编排，与 store 绑得更紧。
 *
 * 跨域依赖以回调注入（与 useAnalysisQueue / useF2Import 等 composable 的既有做法一致）：
 *   - reloadFirstPage：任一筛选/排序变更后退出定位、回第一页、重新加载并同步 URL
 *   - loadPage：退出定位模式时按指定页加载
 */

import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { fetchDominantColors, type DominantColorItem } from '@/api/inspirations'
import { useTagsStore } from '@/stores/tags'
import { buildSourceOptions } from '@/utils/sourceLabel'
import { hasActiveFilters, type BrowseFilterState } from '@/utils/collectionQuery'
import { parseFocusIds, storedBrowseSort } from '@/utils/browseQuery'

export type SourceFilter =
  'all' | 'manual_upload' | 'scraper' | 'xiaohongshu' | 'douyin' | 'browser_extension'
export type MediaFilter = 'all' | 'image' | 'video'
export type StatusFilter = 'all' | 'done' | 'pending' | 'untagged' | 'favorites'
export type QualityFilter = 'all' | 'pending' | 'approved' | 'rejected' | 'ai'
export type SortMode =
  'newest' | 'oldest' | 'updated' | 'largest' | 'tag_count' | 'random' | 'rating' | 'rating_asc'

export interface UseInspirationFiltersOptions {
  /** 任一筛选/排序变更后：退出定位模式、回第一页、重新加载并同步 URL */
  reloadFirstPage: () => void
  /** 退出定位模式时按指定页加载 */
  loadPage: (page: number) => void
}

export function useInspirationFilters(options: UseInspirationFiltersOptions) {
  const route = useRoute()
  const tagsStore = useTagsStore()

  // ── 筛选状态（从 URL query 初始化）──
  const sourceFilter = ref<SourceFilter>((route.query.source as SourceFilter) || 'all')
  const mediaFilter = ref<MediaFilter>((route.query.media as MediaFilter) || 'all')
  const statusFilter = ref<StatusFilter>((route.query.status as StatusFilter) || 'all')
  const qualityFilter = ref<QualityFilter>((route.query.quality as QualityFilter) || 'all')
  const sortMode = ref<SortMode>(
    (route.query.sort as SortMode) || (storedBrowseSort() as SortMode) || 'newest',
  )

  // 持久化浏览模式（排序，含「随机」）：刷新或再次进入素材库时保持上次的选择
  watch(sortMode, (v) => {
    localStorage.setItem('masonry-sort', v)
  })

  // ── 定位模式（裁剪跳过素材跳转）：/ ?focus=id1,id2 ──
  // 从 URL query 恢复待定位素材 ID；定位期间列表仅展示这些素材并高亮，
  // 修改任何筛选/排序会退出定位模式，回到完整列表。
  const focusedIds = ref<string[]>(parseFocusIds(route.query))

  /** 定位模式入口：重置筛选状态，仅按 ID 精确展示被定位的素材 */
  function resetFiltersForFocus() {
    sourceFilter.value = 'all'
    mediaFilter.value = 'all'
    statusFilter.value = 'all'
    qualityFilter.value = 'all'
    selectedTags.value = []
    colorFilter.value = ''
    ratingMin.value = ''
    sortMode.value = 'newest'
  }

  /** 清除定位，回到完整列表 */
  function clearFocus() {
    if (focusedIds.value.length === 0) return
    focusedIds.value = []
    options.loadPage(1)
  }

  // 同路由内 focus 参数变化（如从其他页面再次跳转定位）：重新进入定位模式
  watch(
    () => route.query.focus,
    (v) => {
      const ids = typeof v === 'string' ? parseFocusIds({ focus: v }) : []
      if (ids.join(',') === focusedIds.value.join(',')) return
      focusedIds.value = ids
      if (ids.length > 0) {
        resetFiltersForFocus()
        options.loadPage(1)
      } else {
        options.loadPage(1)
      }
    },
  )

  // ── 标签筛选 ──
  // 从 URL query 恢复（逗号分隔），刷新/详情返回时保持
  const selectedTags = ref<string[]>((route.query.tags as string)?.split(',').filter(Boolean) || [])
  // 标签下拉：按类别分组，支持搜索与多选
  const tagFilterOptions = computed(() =>
    tagsStore.groups.map((g) => ({
      type: 'group' as const,
      label: tagsStore.getCategoryLabel(g.category),
      key: g.category,
      children: g.tags.map((t) => ({ label: t.name, value: t.name })),
    })),
  )
  /** 全部已有标签名（供批量加标签候选，避免重复录入） */
  const allTagNames = computed(() => tagsStore.groups.flatMap((g) => g.tags.map((t) => t.name)))

  // ── 颜色筛选 ──
  // 从 URL query 恢复选中的主色调（hex），刷新/详情返回时保持
  const colorFilter = ref<string>((route.query.color as string) || '')
  /** 库内实际出现的主色调（数据驱动，避免硬编码可能不存在的色板） */
  const dominantColors = ref<DominantColorItem[]>([])

  // ── 评分筛选 ──
  // 从 URL query 恢复（rating >= 指定值），刷新/详情返回时保持
  const ratingMin = ref<string>((route.query.rating_min as string) || '')

  async function loadDominantColors() {
    try {
      dominantColors.value = await fetchDominantColors(30)
    } catch {
      dominantColors.value = []
    }
  }

  // ── 筛选选项配置 ──
  // 来源选项由 sourceLabel.ts 统一生成（新增来源类型只改一处）
  const sourceOptions = buildSourceOptions('all').map((o) => ({
    ...o,
    value: o.value as SourceFilter,
  }))

  const mediaOptions: { label: string; value: MediaFilter }[] = [
    { label: '全部', value: 'all' },
    { label: '图片', value: 'image' },
    { label: '视频', value: 'video' },
  ]

  const statusOptions: { label: string; value: StatusFilter }[] = [
    { label: '全部状态', value: 'all' },
    { label: '已分析', value: 'done' },
    { label: '未分析', value: 'pending' },
    { label: '无标签', value: 'untagged' },
    { label: '仅收藏', value: 'favorites' },
  ]

  const qualityOptions: { label: string; value: QualityFilter }[] = [
    { label: '全部审核', value: 'all' },
    { label: '待审核', value: 'pending' },
    { label: '已通过', value: 'approved' },
    { label: '已拒绝', value: 'rejected' },
    { label: '疑似 AI', value: 'ai' },
  ]

  const sortOptions: { label: string; value: SortMode }[] = [
    { label: '最新在前', value: 'newest' },
    { label: '最旧在前', value: 'oldest' },
    { label: '最近更新', value: 'updated' },
    { label: '评分最高', value: 'rating' },
    { label: '评分最低', value: 'rating_asc' },
    { label: '文件最大', value: 'largest' },
    { label: '标签最多', value: 'tag_count' },
    { label: '随机', value: 'random' },
  ]

  /** 评分筛选选项（rating >= 指定值） */
  const ratingOptions: { label: string; value: string }[] = [
    { label: '全部评分', value: '' },
    { label: '★ 1 分及以上', value: '1' },
    { label: '★ 2 分及以上', value: '2' },
    { label: '★ 3 分及以上', value: '3' },
    { label: '★ 4 分及以上', value: '4' },
    { label: '★ 5 分', value: '5' },
  ]

  // ── 筛选/排序快捷入口 ──
  // 模板事件统一走函数：内联多语句表达式（a = b; fn()）会被格式化工具拆成
  // 换行形式导致 Vue 模板编译失败，故全部收敛为具名函数。

  function setSourceFilter(v: SourceFilter) {
    sourceFilter.value = v
    options.reloadFirstPage()
  }

  function setMediaFilter(v: MediaFilter) {
    mediaFilter.value = v
    options.reloadFirstPage()
  }

  function setStatusFilter(v: StatusFilter) {
    statusFilter.value = v
    options.reloadFirstPage()
  }

  function setQualityFilter(v: QualityFilter) {
    qualityFilter.value = v
    options.reloadFirstPage()
  }

  function setColorFilter(v: string) {
    colorFilter.value = v
    options.reloadFirstPage()
  }

  function setSortMode(v: SortMode) {
    sortMode.value = v
    options.reloadFirstPage()
  }

  /** 移除单个标签筛选 */
  function removeTagFilter(tag: string) {
    selectedTags.value = selectedTags.value.filter((t) => t !== tag)
    options.reloadFirstPage()
  }

  /** 清除全部筛选（不含定位模式） */
  function clearAllFilters() {
    sourceFilter.value = 'all'
    mediaFilter.value = 'all'
    statusFilter.value = 'all'
    qualityFilter.value = 'all'
    selectedTags.value = []
    colorFilter.value = ''
    sortMode.value = 'newest'
    options.reloadFirstPage()
  }

  /** 当前筛选是否含实质条件（无条件下保存=动态全库合集，需二次确认） */
  const filtersActive = computed(() =>
    hasActiveFilters({
      source: sourceFilter.value,
      media: mediaFilter.value,
      status: statusFilter.value,
      quality: qualityFilter.value,
      tags: selectedTags.value,
      color: colorFilter.value,
      ratingMin: ratingMin.value,
      keyword: '',
    } satisfies BrowseFilterState),
  )

  return {
    sourceFilter,
    mediaFilter,
    statusFilter,
    qualityFilter,
    colorFilter,
    sortMode,
    ratingMin,
    selectedTags,
    focusedIds,
    dominantColors,
    tagFilterOptions,
    allTagNames,
    sourceOptions,
    mediaOptions,
    statusOptions,
    qualityOptions,
    sortOptions,
    ratingOptions,
    filtersActive,
    loadDominantColors,
    resetFiltersForFocus,
    clearFocus,
    setSourceFilter,
    setMediaFilter,
    setStatusFilter,
    setQualityFilter,
    setColorFilter,
    setSortMode,
    removeTagFilter,
    clearAllFilters,
  }
}
