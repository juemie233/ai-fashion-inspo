/** 灵感素材状态管理（Zustand）。 */

import { create } from 'zustand'
import {
  apiClient,
  getApiBaseUrl,
  loadApiBaseUrl,
  type Inspiration,
  type InspirationListResponse,
} from '../services/api'

/** 上传文件扩展名 → MIME 类型（避免 PNG/HEIC 被错误标记为 image/jpeg） */
const MIME_BY_EXT: Record<string, string> = {
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  png: 'image/png',
  webp: 'image/webp',
  gif: 'image/gif',
  mp4: 'video/mp4',
}

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

  /** 上传图片 */
  uploadImage: async (uri: string) => {
    const formData = new FormData()
    const filename = uri.split('/').pop() || 'photo.jpg'
    // 按真实扩展名推断 MIME，避免 PNG/HEIC 被错误标记为 image/jpeg
    const ext = filename.split('.').pop()?.toLowerCase() || 'jpg'
    const mime = MIME_BY_EXT[ext] || 'image/jpeg'
    formData.append('file', {
      uri,
      name: filename,
      type: mime,
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
