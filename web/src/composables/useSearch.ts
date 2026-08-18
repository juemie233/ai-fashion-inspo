/** 高级搜索页的核心状态与逻辑：关键词、多维筛选、排序、向量搜索、搜索历史。 */

import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useMessage } from 'naive-ui'
import { useTagsStore } from '@/stores/tags'
import { useInspirationsStore } from '@/stores/inspirations'
import {
  searchInspirations,
  vectorSearchText,
  vectorSearchImage,
  type SearchQuery,
  type VectorSearchItem,
} from '@/api/search'
import type { InspirationOut } from '@/api/inspirations'
import { toggleFavorite as toggleFavoriteApi, updateRating as updateRatingApi } from '@/api/inspirations'

/** 排序选项（搜索结果排序） */
export const SEARCH_SORT_OPTIONS = [
  { label: '最新在前', value: 'newest' },
  { label: '最旧在前', value: 'oldest' },
  { label: '标签最多', value: 'tag_count' },
  { label: '匹配优先', value: 'match_score' },
  { label: '评分最高', value: 'rating' },
  { label: '评分最低', value: 'rating_asc' },
]

/**
 * 高级搜索页核心逻辑：将关键词、多维筛选、排序、向量搜索、搜索历史整合在一起。
 * 由 SearchView 调用，负责全部状态、请求、URL 同步与全局快捷键。
 */
export function useSearch() {
  const router = useRouter()
  const route = useRoute()
  const message = useMessage()
  const tagsStore = useTagsStore()
  const inspStore = useInspirationsStore()

  /** 搜索栏组件引用，供全局快捷键聚焦 */
  const searchBarRef = ref<{ focus: () => void } | null>(null)

  // ── 响应式状态 ──

  const results = ref<InspirationOut[]>([])
  const total = ref(0)
  const searching = ref(false)
  let searchSeq = 0  // 请求序号：普通/语义/以图搜图共用，防止陈旧响应乱序覆盖新结果
  const filterVisible = ref(localStorage.getItem('search-filter-visible') !== 'false')

  // 搜索参数
  const keyword = ref((route.query.q as string) || '')
  const currentPage = ref(1)
  const pageSize = ref(parseInt(localStorage.getItem('search-page-size') || '', 10) || 50)
  const sortMode = ref((route.query.sort as string) || 'newest')
  const sourceFilter = ref((route.query.source as string) || '')
  const mediaFilter = ref((route.query.media as string) || '')
  const analysisFilter = ref((route.query.analysis as string) || '')
  const dateFrom = ref((route.query.from as string) || '')
  const dateTo = ref((route.query.to as string) || '')
  /** 评分筛选（rating >= 指定值，空串表示不限） */
  const ratingMin = ref((route.query.rating_min as string) || '')

  // 密度
  const density = ref<'compact' | 'standard' | 'comfortable'>(
    (localStorage.getItem('search-density') as 'compact' | 'standard' | 'comfortable') || 'compact'
  )

  // ── 持久化分页大小 / 筛选面板可见性 / 密度：刷新或再次进入时保持上次选择 ──
  watch(pageSize, (v) => { localStorage.setItem('search-page-size', String(v)) })
  watch(filterVisible, (v) => { localStorage.setItem('search-filter-visible', String(v)) })
  watch(density, (v) => { localStorage.setItem('search-density', v) })

  // ── 向量搜索（语义搜索 / 以图搜图） ──

  /** 向量搜索模式：none 普通搜索 / semantic 语义搜索 / image 以图搜图 */
  const vectorMode = ref<'none' | 'semantic' | 'image'>('none')
  /** 语义搜索输入文本 */
  const semanticText = ref('')
  /** 语义搜索执行中 */
  const vectorLoading = ref(false)
  /** 向量搜索结果原始数据（含相似度分数） */
  const vectorItems = ref<VectorSearchItem[]>([])
  /** 向量搜索的查询描述（用于横幅展示） */
  const vectorQueryLabel = ref('')
  /** 以图搜图上传图片的本地预览 URL */
  const imagePreviewUrl = ref<string | null>(null)

  /** 向量结果卡片角标（相似度百分比） */
  const vectorBadges = computed<Record<string, string>>(() => {
    const map: Record<string, string> = {}
    for (const item of vectorItems.value) {
      map[item.inspiration.id] = `${Math.round(item.score * 100)}% 相似`
    }
    return map
  })

  // 搜索历史
  /** 读取 localStorage 中的搜索历史，解析失败时回退空数组（参考 ScraperView 的 try/catch 范式） */
  function loadSearchHistory(): string[] {
    try {
      const raw = localStorage.getItem('search-history')
      if (!raw) return []
      const parsed = JSON.parse(raw)
      return Array.isArray(parsed) ? parsed : []
    } catch {
      return []
    }
  }
  const searchHistory = ref<string[]>(loadSearchHistory())

  const totalPages = computed(() => Math.ceil(total.value / pageSize.value))

  // ── URL 同步 ──

  function syncUrl() {
    const query: Record<string, string> = {}
    if (keyword.value) query.q = keyword.value
    if (sortMode.value !== 'newest') query.sort = sortMode.value
    if (sourceFilter.value) query.source = sourceFilter.value
    if (mediaFilter.value) query.media = mediaFilter.value
    if (analysisFilter.value) query.analysis = analysisFilter.value
    if (dateFrom.value) query.from = dateFrom.value
    if (dateTo.value) query.to = dateTo.value
    if (ratingMin.value) query.rating_min = ratingMin.value
    return router.replace({ query })
  }

  /** 复制当前搜索链接（含筛选条件）到剪贴板 */
  async function copySearchLink() {
    await syncUrl()  // 等待 URL 同步完成后再读取 location.href，确保复制到最新筛选条件
    try {
      await navigator.clipboard.writeText(location.href)
      message.success('已复制搜索链接')
    } catch {
      message.error('复制失败')
    }
  }

  // ── 搜索执行 ──

  function buildQuery(page: number): SearchQuery {
    const query: SearchQuery = {
      combine: tagsStore.combineMode,
      page,
      size: pageSize.value,
      sort: sortMode.value,
    }

    const includedTags = [...tagsStore.selectedTags]
    if (includedTags.length > 0) query.include_tags = includedTags.join(',')
    if (tagsStore.excludedTags.size > 0) query.exclude_tags = [...tagsStore.excludedTags].join(',')
    if (keyword.value) query.keyword = keyword.value
    if (sourceFilter.value) query.source_type = sourceFilter.value
    if (mediaFilter.value) query.media_type = mediaFilter.value
    if (analysisFilter.value) query.analysis_status = analysisFilter.value
    if (dateFrom.value) query.date_from = dateFrom.value
    if (dateTo.value) query.date_to = dateTo.value
    if (ratingMin.value) query.rating_min = Number(ratingMin.value)

    return query
  }

  async function doSearch(page: number = 1) {
    resetVectorState()
    searching.value = true
    currentPage.value = page
    const seq = ++searchSeq
    try {
      const data = await searchInspirations(buildQuery(page))
      if (seq !== searchSeq) return  // 已有更新的搜索请求，丢弃过期响应
      results.value = data.items
      total.value = data.total
      syncUrl()
      window.scrollTo({ top: 0, behavior: 'smooth' })
    } catch {
      if (seq === searchSeq) message.error('搜索失败')
    } finally {
      if (seq === searchSeq) searching.value = false
    }
  }

  // ── 搜索栏回调 ──

  function handleSearchBar(val: string) {
    // 颜色代码同样直接作为关键词处理：先设置好最终状态再发一次搜索，避免触发两次并发请求
    keyword.value = val
    addToHistory(val)
    doSearch(1)
  }

  function addToHistory(val: string) {
    const h = searchHistory.value.filter(h => h !== val)
    h.unshift(val)
    searchHistory.value = h.slice(0, 10)
    localStorage.setItem('search-history', JSON.stringify(searchHistory.value))
  }

  // ── 向量搜索（语义搜索 / 以图搜图） ──

  /** 触发语义搜索 */
  async function doSemanticSearch() {
    const text = semanticText.value.trim()
    if (!text) {
      message.warning('请输入要搜索的语义描述')
      return
    }
    vectorLoading.value = true
    const seq = ++searchSeq  // 纳入同一序号体系，防止与普通搜索乱序覆盖
    try {
      const data = await vectorSearchText(text, 50)
      if (seq !== searchSeq) return  // 已有更新的搜索请求，丢弃过期响应
      vectorItems.value = data.items
      vectorMode.value = 'semantic'
      vectorQueryLabel.value = text
      results.value = data.items.map((i) => i.inspiration)
      total.value = data.total
      addToHistory(text)
      window.scrollTo({ top: 0, behavior: 'smooth' })
    } catch (e: any) {
      if (seq === searchSeq) message.error(e.response?.data?.detail || '语义搜索失败，请确认后端向量服务已就绪')
    } finally {
      if (seq === searchSeq) vectorLoading.value = false
    }
  }

  /** 选择图片后触发以图搜图 */
  async function handleImagePicked(file: File) {
    vectorLoading.value = true
    const seq = ++searchSeq  // 纳入同一序号体系，防止与普通搜索乱序覆盖
    // 生成本地预览：先释放上一次的 blob URL，避免内存泄漏
    if (imagePreviewUrl.value) {
      URL.revokeObjectURL(imagePreviewUrl.value)
      imagePreviewUrl.value = null
    }
    imagePreviewUrl.value = URL.createObjectURL(file)
    try {
      const data = await vectorSearchImage(file, 50)
      if (seq !== searchSeq) return  // 已有更新的搜索请求，丢弃过期响应
      vectorItems.value = data.items
      vectorMode.value = 'image'
      vectorQueryLabel.value = file.name
      results.value = data.items.map((i) => i.inspiration)
      total.value = data.total
      window.scrollTo({ top: 0, behavior: 'smooth' })
    } catch (err: any) {
      if (seq === searchSeq) message.error(err.response?.data?.detail || '以图搜图失败，请确认已安装 CLIP 图像模型')
    } finally {
      if (seq === searchSeq) vectorLoading.value = false
    }
  }

  /** 清空向量搜索状态（不触发重新搜索） */
  function resetVectorState() {
    vectorMode.value = 'none'
    vectorItems.value = []
    vectorQueryLabel.value = ''
    if (imagePreviewUrl.value) {
      URL.revokeObjectURL(imagePreviewUrl.value)
      imagePreviewUrl.value = null
    }
  }

  /** 退出向量搜索，返回普通搜索 */
  function exitVectorMode() {
    resetVectorState()
    doSearch(1)
  }

  // ── 标签筛选变化时自动搜索 ──

  watch(
    () => [tagsStore.selectedTags, tagsStore.excludedTags, tagsStore.combineMode] as const,
    () => {
      if (tagsStore.selectedTags.size > 0 || tagsStore.excludedTags.size > 0) {
        doSearch(1)
      } else if (!keyword.value) {
        // 清除所有筛选后重新加载
        doSearch(1)
      }
    },
    { deep: false }
  )

  // ── 删除/收藏 ──

  async function handleDelete(id: string) {
    try {
      await inspStore.remove(id)
      results.value = results.value.filter(r => r.id !== id)
      total.value--
      message.success('已移入垃圾桶')
    } catch {
      message.error('操作失败')
    }
  }

  async function handleToggleFavorite(id: string) {
    // 搜索结果不经过素材库 store，直接调 API 并更新本地结果项，
    // 否则 store.toggleFavorite 找不到 item 会静默返回，按钮无任何反应
    const item = results.value.find((r) => r.id === id)
    if (!item) return
    try {
      const newState = !item.is_favorite
      await toggleFavoriteApi(id, newState)
      item.is_favorite = newState
    } catch {
      message.error('操作失败')
    }
  }

  /** 设置评分（搜索结果直接调 API 并更新本地结果项） */
  async function handleRate(id: string, value: number) {
    const item = results.value.find((r) => r.id === id)
    if (!item) return
    try {
      await updateRatingApi(id, value)
      item.rating = value
      message.success(value > 0 ? `已评分 ${value} 星` : '已清除评分')
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data
        ?.detail
      message.error(detail || '评分失败')
    }
  }

  // ── 搜索历史应用 ──

  function applyHistory(q: string) {
    keyword.value = q
    doSearch(1)
  }

  function clearHistory() {
    searchHistory.value = []
    localStorage.removeItem('search-history')
  }

  // ── 全局快捷键：/ 聚焦搜索框（焦点在输入框时放行避免干扰输入），Esc 退出向量搜索 ──

  function onGlobalKeydown(e: KeyboardEvent) {
    const target = e.target as HTMLElement | null
    const tag = target?.tagName
    if (tag === 'INPUT' || tag === 'TEXTAREA' || target?.isContentEditable) return
    if (e.key === '/') {
      e.preventDefault()
      searchBarRef.value?.focus()
    } else if (e.key === 'Escape' && vectorMode.value !== 'none') {
      exitVectorMode()
    }
  }

  // 初始加载
  onMounted(() => {
    tagsStore.load()
    doSearch(1)
    // 注册全局快捷键：/ 聚焦搜索框、Esc 退出向量搜索
    document.addEventListener('keydown', onGlobalKeydown)
  })

  onUnmounted(() => {
    // 卸载时释放以图搜图的本地预览 blob URL
    if (imagePreviewUrl.value) {
      URL.revokeObjectURL(imagePreviewUrl.value)
      imagePreviewUrl.value = null
    }
    // 移除全局快捷键监听，避免页面残留
    document.removeEventListener('keydown', onGlobalKeydown)
  })

  return {
    searchBarRef,
    tagsStore,
    results,
    total,
    searching,
    filterVisible,
    keyword,
    currentPage,
    pageSize,
    sortMode,
    sourceFilter,
    mediaFilter,
    analysisFilter,
    dateFrom,
    dateTo,
    ratingMin,
    density,
    vectorMode,
    semanticText,
    vectorLoading,
    vectorQueryLabel,
    imagePreviewUrl,
    searchHistory,
    vectorBadges,
    totalPages,
    sortOptions: SEARCH_SORT_OPTIONS,
    copySearchLink,
    doSearch,
    handleSearchBar,
    doSemanticSearch,
    handleImagePicked,
    exitVectorMode,
    handleDelete,
    handleToggleFavorite,
    handleRate,
    applyHistory,
    clearHistory,
  }
}
