/** 收藏中心 API 封装：端点、查询参数与动作/来源中文映射。 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ get: vi.fn() }))

vi.mock('../client', () => ({ default: { get: mocks.get } }))

import {
  favoriteActionLabel,
  favoriteSourceLabel,
  fetchFavoritesHistory,
  fetchFavoritesOverview,
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

    expect(favoriteSourceLabel('detail')).toBe('素材详情')
    expect(favoriteSourceLabel('card')).toBe('素材卡片')
    expect(favoriteSourceLabel('new_entry')).toBe('new_entry')
    expect(favoriteSourceLabel(null)).toBe('未知来源')
  })
})
