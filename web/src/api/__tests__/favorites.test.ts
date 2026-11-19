/** 收藏中心 API 封装：端点、查询参数与动作/来源中文映射。 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ get: vi.fn() }))

vi.mock('../client', () => ({ default: { get: mocks.get } }))

import {
  favoriteActionLabel,
  favoriteSourceLabel,
  fetchFavoriteViewItems,
  fetchFavoritesCleanup,
  fetchFavoritesHistory,
  fetchFavoritesOverview,
  fetchFavoriteViews,
  isUnfavoriteAction,
} from '../favorites'

beforeEach(() => {
  vi.clearAllMocks()
  mocks.get.mockResolvedValue({ data: {} })
})

describe('favorites API', () => {
  it('概览：GET /favorites/overview?recent_limit=…', async () => {
    await fetchFavoritesOverview(7)
    expect(mocks.get).toHaveBeenCalledWith('/favorites/overview', {
      params: { recent_limit: 7 },
    })
    // 默认 12 条最近收藏
    await fetchFavoritesOverview()
    expect(mocks.get).toHaveBeenLastCalledWith('/favorites/overview', {
      params: { recent_limit: 12 },
    })
  })

  it('动态：GET /favorites/history?limit=…', async () => {
    await fetchFavoritesHistory(5)
    expect(mocks.get).toHaveBeenCalledWith('/favorites/history', { params: { limit: 5 } })
    await fetchFavoritesHistory()
    expect(mocks.get).toHaveBeenLastCalledWith('/favorites/history', { params: { limit: 30 } })
  })

  it('动作/来源中文映射；未知值原样展示', () => {
    expect(favoriteActionLabel('favorite')).toBe('收藏')
    expect(favoriteActionLabel('unfavorite')).toBe('取消收藏')
    expect(favoriteActionLabel('archived')).toBe('archived')
    expect(isUnfavoriteAction('unfavorite')).toBe(true)
    expect(isUnfavoriteAction('favorite')).toBe(false)

    // 后端 FavoriteEvent.source 的全部取值（web/batch/api/legacy）
    expect(favoriteSourceLabel('web')).toBe('网页')
    expect(favoriteSourceLabel('batch')).toBe('批量操作')
    expect(favoriteSourceLabel('api')).toBe('接口')
    expect(favoriteSourceLabel('legacy')).toBe('历史收藏')
    expect(favoriteSourceLabel('new_entry')).toBe('new_entry')
    expect(favoriteSourceLabel(null)).toBe('未知来源')
  })

  it('智能视图列表：GET /favorites/views', async () => {
    mocks.get.mockResolvedValue({ data: { views: [] } })
    await fetchFavoriteViews()
    expect(mocks.get).toHaveBeenCalledWith('/favorites/views')
  })

  it('智能视图素材：GET /favorites/views/{key}?page&size', async () => {
    await fetchFavoriteViewItems('unrated', 2, 24)
    expect(mocks.get).toHaveBeenCalledWith('/favorites/views/unrated', {
      params: { page: 2, size: 24 },
    })
    // 默认第一页 24 条
    await fetchFavoriteViewItems('high_rating')
    expect(mocks.get).toHaveBeenLastCalledWith('/favorites/views/high_rating', {
      params: { page: 1, size: 24 },
    })
  })

  it('清理摘要：GET /favorites/cleanup', async () => {
    await fetchFavoritesCleanup()
    expect(mocks.get).toHaveBeenCalledWith('/favorites/cleanup')
  })
})
