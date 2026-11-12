/** 大屏适配与无障碍（P1）：列数切换、图片预取、卡片朗读文案。 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react-native'
import { Image } from 'expo-image'

const mockRouter = { push: jest.fn(), back: jest.fn(), replace: jest.fn() }

jest.mock('expo-router', () => ({
  useRouter: () => mockRouter,
  useLocalSearchParams: () => ({}),
}))

jest.mock('../services/api', () => {
  const actual = jest.requireActual('../services/api')
  return {
    ...actual,
    apiClient: { get: jest.fn(), patch: jest.fn(), post: jest.fn(), delete: jest.fn() },
    fetchInspirationPage: jest.fn(),
    fetchCollections: jest.fn(),
    createCollection: jest.fn(),
    addToCollection: jest.fn(),
    batchFavorite: jest.fn(),
    batchTrash: jest.fn(),
  }
})

// 只替换列数计算，其它导出保持真实实现
jest.mock('../utils/layout', () => {
  const actual = jest.requireActual('../utils/layout')
  return { ...actual, gridColumns: jest.fn(actual.gridColumns) }
})

import GalleryScreen from '../app/(tabs)/index'
import { useInspirationStore } from '../hooks/useInspirations'
import { gridColumns } from '../utils/layout'
// eslint-disable-next-line @typescript-eslint/no-var-requires
const api = require('../services/api') as { fetchInspirationPage: jest.Mock; fetchCollections: jest.Mock }

const prefetch = (Image as unknown as { prefetch: jest.Mock }).prefetch

function makeItem(id: string, over: Record<string, unknown> = {}) {
  return {
    id,
    source_type: 'douyin',
    file_path: `images/${id}.webp`,
    thumbnail_path: `thumbnails/${id}.jpg`,
    media_type: 'image',
    is_favorite: false,
    created_at: '2026-10-01T00:00:00Z',
    tags: [],
    ...over,
  }
}

beforeEach(() => {
  jest.clearAllMocks()
  api.fetchCollections.mockResolvedValue([])
  api.fetchInspirationPage.mockResolvedValue({
    items: [makeItem('i1'), makeItem('i2', { is_favorite: true, media_type: 'video' })],
    total: 2,
    page: 1,
    size: 30,
  })
  useInspirationStore.setState({
    items: [],
    total: 0,
    page: 1,
    loading: false,
    error: null,
    filter: {},
    sort: 'newest',
    apiBaseUrl: 'http://192.168.31.142:18888',
  })
})

describe('gridColumns', () => {
  it('手机 2 列，平板 3 列，超宽 4 列（默认上限 3）', () => {
    // 走真实实现（mock 只是转调 actual）
    const actual = jest.requireActual('../utils/layout') as typeof import('../utils/layout')
    expect(actual.gridColumns(360)).toBe(2)
    expect(actual.gridColumns(599)).toBe(2)
    expect(actual.gridColumns(600)).toBe(3)
    expect(actual.gridColumns(800)).toBe(3)
    expect(actual.gridColumns(1000)).toBe(3)
    expect(actual.gridColumns(1000, 4)).toBe(4)
    // 非法宽度回退 2 列
    expect(actual.gridColumns(Number.NaN)).toBe(2)
    expect(actual.gridColumns(0)).toBe(2)
  })
})

describe('素材库大屏与无障碍', () => {
  it('大屏时网格按计算出的列数渲染', async () => {
    ;(gridColumns as jest.Mock).mockReturnValue(3)
    render(<GalleryScreen />)
    await waitFor(() => expect(screen.getByTestId('gallery-list')).toBeTruthy())

    // testID 落在内部 ScrollView 上，numColumns 要从 FlatList 元素读
    const list = screen.UNSAFE_getByType(require('react-native').FlatList)
    expect(list.props.numColumns).toBe(3)
  })

  it('手机宽度下仍是 2 列', async () => {
    ;(gridColumns as jest.Mock).mockReturnValue(2)
    render(<GalleryScreen />)
    await waitFor(() => expect(screen.getByTestId('gallery-list')).toBeTruthy())

    const list = screen.UNSAFE_getByType(require('react-native').FlatList)
    expect(list.props.numColumns).toBe(2)
  })

  it('卡片带可读的朗读文案（来源 + 收藏 + 视频）', async () => {
    render(<GalleryScreen />)
    await waitFor(() => expect(screen.getByTestId('card-i1')).toBeTruthy())

    expect(screen.getByTestId('card-i1').props.accessibilityLabel).toBe('素材：抖音')
    expect(screen.getByTestId('card-i2').props.accessibilityLabel).toBe(
      '素材：抖音，已收藏，视频',
    )
  })

  it('「随机看一张」从已加载素材里挑一条进详情', async () => {
    render(<GalleryScreen />)
    await waitFor(() => expect(screen.getByTestId('card-i1')).toBeTruthy())

    fireEvent.press(screen.getByTestId('random-open'))

    expect(mockRouter.push).toHaveBeenCalledTimes(1)
    expect(String(mockRouter.push.mock.calls[0][0])).toMatch(/^\/detail\/i[12]$/)
  })

  it('点卡片进详情前预取原图（详情打开更快）', async () => {
    render(<GalleryScreen />)
    await waitFor(() => expect(screen.getByTestId('card-i1')).toBeTruthy())

    fireEvent.press(screen.getByTestId('card-i1'))

    expect(prefetch).toHaveBeenCalledWith('http://192.168.31.142:18888/api/files/images/i1.webp')
  })
})
