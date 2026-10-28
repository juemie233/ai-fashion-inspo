/** 灵感素材状态管理（Zustand）。 */

import { create } from 'zustand'
import {
  apiClient,
  batchFavorite as apiBatchFavorite,
  batchTrash as apiBatchTrash,
  fetchInspirationPage,
  getApiBaseUrl,
  loadApiBaseUrl,
  uploadInspirationImage,
  type Inspiration,
  type ListFilter,
  type SortKey,
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
  /** 当前筛选条件（素材库顶部胶囊） */
  filter: ListFilter
  /** 当前排序 */
  sort: SortKey
  /** 修改筛选（自动重新拉第一页） */
  setFilter: (patch: ListFilter) => Promise<void>
  /** 修改排序（自动重新拉第一页） */
  setSort: (sort: SortKey) => Promise<void>
  /** 批量收藏 / 取消收藏 */
  batchFavorite: (ids: string[], isFavorite: boolean) => Promise<void>
  /** 批量移入垃圾桶（本地同步移除） */
  batchTrash: (ids: string[]) => Promise<number>
  /** 从列表本地移除若干条（移入垃圾桶/移出合集后调用） */
  removeItems: (ids: string[]) => void
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
  filter: {},
  sort: 'newest',

  /** 启动初始化：恢复持久化地址（设置页保存的自定义地址）后同步状态 */
  init: async () => {
    const url = await loadApiBaseUrl()
    set({ apiBaseUrl: url })
    await get().fetchInspirations()
  },

  /** 加载第一页（带当前筛选与排序） */
  fetchInspirations: async () => {
    const { filter, sort } = get()
    set({ loading: true })
    try {
      const data = await fetchInspirationPage(1, 30, filter, sort)
      set({ items: data.items, total: data.total, page: 1 })
    } catch (e) {
      console.error('加载素材失败', e)
    } finally {
      set({ loading: false })
    }
  },

  /** 切换筛选：更新条件后重拉第一页 */
  setFilter: async (patch: ListFilter) => {
    set((state) => ({ filter: { ...state.filter, ...patch }, items: [], page: 1 }))
    await get().fetchInspirations()
  },

  /** 切换排序：更新后重拉第一页 */
  setSort: async (sort: SortKey) => {
    set({ sort, items: [], page: 1 })
    await get().fetchInspirations()
  },

  /** 批量收藏 / 取消收藏：成功后本地同步，失败抛出交给调用方提示 */
  batchFavorite: async (ids: string[], isFavorite: boolean) => {
    await apiBatchFavorite(ids, isFavorite)
    set((state) => ({
      items: state.items.map((i) => (ids.includes(i.id) ? { ...i, is_favorite: isFavorite } : i)),
    }))
  },

  /** 批量移入垃圾桶：返回成功条数，本地移除 */
  batchTrash: async (ids: string[]) => {
    const result = await apiBatchTrash(ids)
    const trashed = result.trashed ?? ids.length
    set((state) => ({
      items: state.items.filter((i) => !ids.includes(i.id)),
      total: Math.max(0, state.total - trashed),
    }))
    return trashed
  },

  /** 本地移除（不再请求后端，用于移出合集等场景） */
  removeItems: (ids: string[]) => {
    set((state) => ({
      items: state.items.filter((i) => !ids.includes(i.id)),
      total: Math.max(0, state.total - ids.length),
    }))
  },

  /** 加载更多（无限滚动） */
  fetchMore: async () => {
    const { loading, page, items, total } = get()
    if (loading || items.length >= total) return

    set({ loading: true })
    try {
      const { filter, sort } = get()
      const data = await fetchInspirationPage(page + 1, 30, filter, sort)
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
