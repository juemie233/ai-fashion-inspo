/** 收藏筛选/防连点（2026-11-12 收藏审查 P1-1/P2）。 */

jest.mock('../services/api', () => {
  const actual = jest.requireActual('../services/api')
  return {
    ...actual,
    apiClient: { get: jest.fn(), patch: jest.fn(), post: jest.fn(), delete: jest.fn() },
    fetchInspirationPage: jest.fn(),
    batchFavorite: jest.fn(),
  }
})

import { useInspirationStore } from '../hooks/useInspirations'
const api = require('../services/api') as {
  apiClient: { patch: jest.Mock }
  fetchInspirationPage: jest.Mock
  batchFavorite: jest.Mock
}

function item(id: string, favorite: boolean) {
  return {
    id,
    source_type: 'douyin' as const,
    file_path: `images/${id}.webp`,
    media_type: 'image' as const,
    is_favorite: favorite,
    created_at: '2026-11-12T00:00:00Z',
    tags: [],
  }
}

describe('收藏筛选与防连点', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    api.fetchInspirationPage.mockResolvedValue({ items: [], total: 0, page: 1, size: 30 })
    useInspirationStore.setState({
      items: [item('a', true), item('b', true)],
      total: 2,
      page: 1,
      filter: { is_favorite: true },
      favoriteRevision: 0,
    })
  })

  it('仅收藏筛选下取消收藏：立即移除并修正 total', async () => {
    api.apiClient.patch.mockResolvedValue({ data: {} })

    await useInspirationStore.getState().toggleFavorite('a', false)

    const state = useInspirationStore.getState()
    expect(state.items.map((i) => i.id)).toEqual(['b'])
    expect(state.total).toBe(1)
  })

  it('收藏请求进行中重复点击只发一次请求', async () => {
    let resolvePatch!: (v: unknown) => void
    api.apiClient.patch.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolvePatch = resolve
        }),
    )
    const store = useInspirationStore.getState()
    const first = store.toggleFavorite('b', false)
    const second = store.toggleFavorite('b', false)
    await Promise.resolve()
    expect(api.apiClient.patch).toHaveBeenCalledTimes(1)

    resolvePatch({ data: {} })
    await Promise.all([first, second])
  })
})
