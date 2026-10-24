/** 灵感素材状态管理（Zustand）。 */

import { create } from 'zustand'
import {
  apiClient,
  getApiBaseUrl,
  loadApiBaseUrl,
  uploadInspirationImage,
  type Inspiration,
  type InspirationListResponse,
} from '../services/api'

interface InspirationState {
  items: Inspiration[]
  total: number
  page: number
  loading: boolean
  apiBaseUrl: string

  /** App 启动初始化：从持久化存储恢复自定义后端地址并刷新数据 */
  init: () => Promise<void>
  fetchInspirations: () => Promise<void>
  fetchMore: () => Promise<void>
  uploadImage: (uri: string) => Promise<Inspiration>
  /** 插入一条素材到列表最前（多图上传用） */
  prependItem: (item: Inspiration) => void
  toggleFavorite: (id: string, desiredState: boolean) => Promise<void>
}

export const useInspirationStore = create<InspirationState>((set, get) => ({
  items: [],
  total: 0,
  page: 1,
  loading: false,
  apiBaseUrl: getApiBaseUrl(),

  /** 启动初始化：恢复持久化地址（设置页保存的自定义地址）后同步状态 */
  init: async () => {
    const url = await loadApiBaseUrl()
    set({ apiBaseUrl: url })
    await get().fetchInspirations()
  },

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

  /** 上传单张图片（multipart 逻辑收敛在 services/api.ts） */
  uploadImage: async (uri: string) => {
    const data = await uploadInspirationImage(uri)

    // 插入到列表最前面
    set((state) => ({
      items: [data, ...state.items],
      total: state.total + 1,
    }))

    return data
  },

  /** 把一条新素材插到列表最前（多图上传逐张完成后调用） */
  prependItem: (item: Inspiration) => {
    set((state) => ({ items: [item, ...state.items], total: state.total + 1 }))
  },

  /** 切换收藏：显式传入目标状态，素材不在列表（详情页）时同样生效 */
  toggleFavorite: async (id: string, desiredState: boolean) => {
    await apiClient.patch(`/inspirations/${id}`, { is_favorite: desiredState })

    set((state) => ({
      items: state.items.map((i) =>
        i.id === id ? { ...i, is_favorite: desiredState } : i
      ),
    }))
  },
}))

export type { Inspiration }
