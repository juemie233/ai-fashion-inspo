/** 灵感素材状态管理：列表、分页、加载状态。 */

import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  fetchInspirations,
  fetchInspiration,
  uploadInspiration,
  toggleFavorite as toggleFavoriteApi,
  deleteInspiration as deleteInspirationApi,
  type InspirationOut,
  type InspirationDetailOut,
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

  /** 加载素材列表 */
  async function load(params: {
    page?: number
    size?: number
    source_type?: string
    is_favorite?: boolean
    sort?: string
  } = {}) {
    loading.value = true
    if (params.size) size.value = params.size
    try {
      const result = await fetchInspirations({
        page: params.page ?? page.value,
        size: size.value,
        source_type: params.source_type,
        is_favorite: params.is_favorite,
        sort: params.sort,
      })
      items.value = result.items
      total.value = result.total
      page.value = result.page
    } catch (e) {
      console.error('加载素材列表失败', e)
    } finally {
      loading.value = false
    }
  }

  /** 加载下一页（追加到列表） */
  async function loadMore() {
    if (loading.value || items.value.length >= total.value) return
    loading.value = true
    try {
      const result = await fetchInspirations({
        page: page.value + 1,
        size: size.value,
      })
      items.value.push(...result.items)
      total.value = result.total
      page.value = result.page
    } catch (e) {
      console.error('加载更多失败', e)
    } finally {
      loading.value = false
    }
  }

  /** 加载素材详情 */
  async function loadDetail(id: string) {
    try {
      currentDetail.value = await fetchInspiration(id)
    } catch (e) {
      console.error('加载素材详情失败', e)
    }
  }

  /** 上传新素材 */
  async function upload(formData: FormData) {
    const item = await uploadInspiration(formData)
    items.value.unshift(item)
    total.value++
    return item
  }

  /** 切换收藏 */
  async function toggleFavorite(id: string) {
    const item = items.value.find((i) => i.id === id)
    if (!item) return
    const newState = !item.is_favorite
    await toggleFavoriteApi(id, newState)
    item.is_favorite = newState
    if (currentDetail.value?.id === id) {
      currentDetail.value.is_favorite = newState
    }
  }

  /** 删除素材 */
  async function remove(id: string) {
    await deleteInspirationApi(id)
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
    remove,
  }
})
