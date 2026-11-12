/** 灵感素材状态管理：列表、分页、加载状态。 */

import { defineStore } from 'pinia'
import { ref } from 'vue'
import { logger } from '@/utils/logger'
import {
  fetchInspirations,
  fetchInspiration,
  uploadInspiration,
  toggleFavorite as toggleFavoriteApi,
  updateRating as updateRatingApi,
  moveToTrash as moveToTrashApi,
  type InspirationOut,
  type InspirationDetailOut,
  type TrashReason,
} from '@/api/inspirations'

export const useInspirationsStore = defineStore('inspirations', () => {
  /** 当前素材列表 */
  const items = ref<InspirationOut[]>([])
  /** 素材总数 */
  const total = ref(0)
  /** 当前页码 */
  const page = ref(1)
  /** 每页数量 */
  const size = ref(50)
  /** 是否正在加载 */
  const loading = ref(false)
  /** 当前查看的素材详情 */
  const currentDetail = ref<InspirationDetailOut | null>(null)
  /** 请求序号（用于忽略过期响应） */
  let _requestSeq = 0
  /** 最近一次 load 的筛选参数（供 loadMore 复用） */
  let _lastParams: Record<string, any> = {}

  /** 加载素材列表 */
  async function load(
    params: {
      page?: number
      size?: number
      source_type?: string
      is_favorite?: boolean
      media_type?: string
      analysis_status?: string
      tag_status?: string
      quality_status?: string
      is_ai_generated?: boolean
      include_tags?: string
      dominant_color?: string
      date_from?: string
      date_to?: string
      ids?: string
      rating_min?: number
      sort?: string
    } = {},
  ) {
    loading.value = true
    if (params.size) size.value = params.size
    _lastParams = params
    const seq = ++_requestSeq
    try {
      const result = await fetchInspirations({
        page: params.page ?? page.value,
        size: size.value,
        source_type: params.source_type,
        is_favorite: params.is_favorite,
        media_type: params.media_type,
        analysis_status: params.analysis_status,
        tag_status: params.tag_status,
        quality_status: params.quality_status,
        is_ai_generated: params.is_ai_generated,
        include_tags: params.include_tags,
        dominant_color: params.dominant_color,
        date_from: params.date_from,
        date_to: params.date_to,
        ids: params.ids,
        rating_min: params.rating_min,
        sort: params.sort,
      })
      // 忽略过期响应（快速翻页时旧请求可能后返回）
      if (seq !== _requestSeq) return
      items.value = result.items
      total.value = result.total
      page.value = result.page
    } catch (e) {
      if (seq !== _requestSeq) return
      logger.error('加载素材列表失败', e)
    } finally {
      if (seq === _requestSeq) loading.value = false
    }
  }

  /** 加载下一页（追加到列表，复用最近一次筛选参数） */
  async function loadMore() {
    if (loading.value || items.value.length >= total.value) return
    loading.value = true
    // 记录发起时的请求序号：加载更多在途时用户改筛选触发新的 load，
    // 本请求返回后必须丢弃，否则旧筛选数据被串进新列表
    const seq = ++_requestSeq
    try {
      const result = await fetchInspirations({
        page: page.value + 1,
        size: size.value,
        source_type: _lastParams.source_type,
        is_favorite: _lastParams.is_favorite,
        media_type: _lastParams.media_type,
        analysis_status: _lastParams.analysis_status,
        tag_status: _lastParams.tag_status,
        quality_status: _lastParams.quality_status,
        is_ai_generated: _lastParams.is_ai_generated,
        include_tags: _lastParams.include_tags,
        dominant_color: _lastParams.dominant_color,
        date_from: _lastParams.date_from,
        date_to: _lastParams.date_to,
        ids: _lastParams.ids,
        rating_min: _lastParams.rating_min,
        sort: _lastParams.sort,
      })
      if (seq !== _requestSeq) return
      items.value.push(...result.items)
      total.value = result.total
      page.value = result.page
    } catch (e) {
      if (seq !== _requestSeq) return
      logger.error('加载更多失败', e)
    } finally {
      if (seq === _requestSeq) loading.value = false
    }
  }

  /** 加载素材详情 */
  async function loadDetail(id: string) {
    try {
      currentDetail.value = await fetchInspiration(id)
    } catch (e) {
      logger.error('加载素材详情失败', e)
    }
  }

  /** 上传新素材（signal 用于取消正在进行的上传请求） */
  async function upload(formData: FormData, onProgress?: (e: any) => void, signal?: AbortSignal) {
    const item = await uploadInspiration(formData, onProgress, signal)
    items.value.unshift(item)
    total.value++
    return item
  }

  /** 正在请求中的收藏 ID：防双击重复发同一请求（2026-11-12 收藏审查 P2） */
  const pendingFavoriteIds = ref<Set<string>>(new Set())

  /** 设置收藏（显式目标值；列表/详情同步，当前筛选为「仅收藏」且取消时移除条目） */
  async function setFavorite(id: string, favorite: boolean) {
    if (pendingFavoriteIds.value.has(id)) return
    pendingFavoriteIds.value = new Set(pendingFavoriteIds.value).add(id)
    try {
      await toggleFavoriteApi(id, favorite)
      // 仅收藏筛选下取消收藏：条目必须立即消失、总数减一（2026-11-12 P1-1）
      if (_lastParams.is_favorite === true && !favorite) {
        items.value = items.value.filter((i) => i.id !== id)
        total.value = Math.max(0, total.value - 1)
      } else {
        const item = items.value.find((i) => i.id === id)
        if (item) item.is_favorite = favorite
      }
      if (currentDetail.value?.id === id) {
        currentDetail.value.is_favorite = favorite
      }
    } finally {
      const next = new Set(pendingFavoriteIds.value)
      next.delete(id)
      pendingFavoriteIds.value = next
    }
  }

  /** 切换收藏（兼容旧调用；详情页可直接用 setFavorite 显式目标值） */
  async function toggleFavorite(id: string, desiredState?: boolean) {
    const item = items.value.find((i) => i.id === id)
    if (item) {
      await setFavorite(id, desiredState ?? !item.is_favorite)
      return
    }
    if (currentDetail.value?.id === id) {
      await setFavorite(id, desiredState ?? !currentDetail.value.is_favorite)
      return
    }
    if (desiredState !== undefined) {
      await setFavorite(id, desiredState)
    }
  }

  /** 设置评分（0~5，0 清除）：同步列表项与详情 */
  async function setRating(id: string, rating: number) {
    await updateRatingApi(id, rating)
    const item = items.value.find((i) => i.id === id)
    if (item) item.rating = rating
    if (currentDetail.value?.id === id) {
      currentDetail.value.rating = rating
    }
  }

  /** 移入垃圾桶（软删除，可恢复）；reason 缺省时由后端推断为「不喜欢」 */
  async function remove(id: string, reason?: TrashReason) {
    await moveToTrashApi(id, reason)
    items.value = items.value.filter((i) => i.id !== id)
    total.value--
  }

  return {
    items,
    total,
    page,
    size,
    loading,
    currentDetail,
    load,
    loadMore,
    loadDetail,
    upload,
    toggleFavorite,
    setFavorite,
    pendingFavoriteIds,
    setRating,
    remove,
  }
})
