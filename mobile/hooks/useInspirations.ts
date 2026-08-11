/** 灵感素材状态管理（Zustand）。 */

import { create } from 'zustand'
import { apiClient, getApiBaseUrl, type Inspiration, type InspirationListResponse } from '../services/api'

interface InspirationState {
  items: Inspiration[]
  total: number
  page: number
  loading: boolean
  apiBaseUrl: string

  fetchInspirations: () => Promise<void>
  fetchMore: () => Promise<void>
  uploadImage: (uri: string) => Promise<Inspiration>
  toggleFavorite: (id: string) => Promise<void>
}

export const useInspirationStore = create<InspirationState>((set, get) => ({
  items: [],
  total: 0,
  page: 1,
  loading: false,
  apiBaseUrl: getApiBaseUrl(),

  /** 加载第一页 */
  fetchInspirations: async () => {
    set({ loading: true })
    try {
      const { data } = await apiClient.get<InspirationListResponse>('/inspirations', {
        params: { page: 1, size: 30 },
      })
      set({ items: data.items, total: data.total, page: 1 })
    } catch (e) {
      console.error('加载素材失败', e)
    } finally {
      set({ loading: false })
    }
  },

  /** 加载更多（无限滚动） */
  fetchMore: async () => {
    const { loading, page, items, total } = get()
    if (loading || items.length >= total) return

    set({ loading: true })
    try {
      const { data } = await apiClient.get<InspirationListResponse>('/inspirations', {
        params: { page: page + 1, size: 30 },
      })
      set({
        items: [...items, ...data.items],
        total: data.total,
        page: page + 1,
      })
    } catch (e) {
      console.error('加载更多失败', e)
    } finally {
      set({ loading: false })
    }
  },

  /** 上传图片 */
  uploadImage: async (uri: string) => {
    const formData = new FormData()
    const filename = uri.split('/').pop() || 'photo.jpg'
    formData.append('file', {
      uri,
      name: filename,
      type: 'image/jpeg',
    } as any)
    formData.append('source_type', 'manual_upload')

    const { data } = await apiClient.post<Inspiration>('/inspirations', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })

    // 插入到列表最前面
    set((state) => ({
      items: [data, ...state.items],
      total: state.total + 1,
    }))

    return data
  },

  /** 切换收藏 */
  toggleFavorite: async (id: string) => {
    const { items } = get()
    const item = items.find((i) => i.id === id)
    if (!item) return

    const newState = !item.is_favorite
    await apiClient.patch(`/inspirations/${id}`, { is_favorite: newState })

    set({
      items: items.map((i) =>
        i.id === id ? { ...i, is_favorite: newState } : i
      ),
    })
  },
}))

export type { Inspiration }
