/** 素材库离线回退（P1 弱网/离线）：断网时显示上次缓存 + 横幅，并给重试入口。 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react-native'
import AsyncStorage from '@react-native-async-storage/async-storage'
import { Alert } from 'react-native'

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

import GalleryScreen from '../app/(tabs)/index'
import { useInspirationStore } from '../hooks/useInspirations'
import { saveGalleryCache } from '../utils/offlineCache'
// eslint-disable-next-line @typescript-eslint/no-var-requires
const api = require('../services/api') as {
  fetchInspirationPage: jest.Mock
  fetchCollections: jest.Mock
}

function makeItem(id: string) {
  return {
    id,
    source_type: 'douyin' as const,
    file_path: `images/${id}.webp`,
    thumbnail_path: `thumbnails/${id}.jpg`,
    media_type: 'image' as const,
    is_favorite: false,
    created_at: '2026-10-01T00:00:00Z',
    tags: [],
  }
}

beforeEach(async () => {
  jest.clearAllMocks()
  await AsyncStorage.clear()
  api.fetchCollections.mockResolvedValue([])
  useInspirationStore.setState({
    items: [],
    total: 0,
    page: 1,
    loading: false,
    error: null,
    filter: {},
    sort: 'newest',
  })
  jest.spyOn(Alert, 'alert').mockImplementation(() => {})
})

afterEach(() => {
  jest.restoreAllMocks()
})

describe('素材库离线回退', () => {
  it('断网且列表为空时展示缓存，并提示「离线模式」与重试', async () => {
    await saveGalleryCache([makeItem('c1'), makeItem('c2')], 2)
    api.fetchInspirationPage.mockRejectedValue(new Error('offline'))

    render(<GalleryScreen />)

    await waitFor(() => expect(screen.getByText(/离线模式：显示/)).toBeTruthy())
    // 缓存里的素材照常渲染
    expect(screen.getByTestId('card-c1')).toBeTruthy()
    expect(screen.getByTestId('card-c2')).toBeTruthy()
    // 不再显示「还没有素材」
    expect(screen.queryByText('还没有素材')).toBeNull()
  })

  it('点横幅上的「重试」会重新请求；网络恢复后横幅消失', async () => {
    await saveGalleryCache([makeItem('c1')], 1)
    api.fetchInspirationPage.mockRejectedValueOnce(new Error('offline'))

    render(<GalleryScreen />)
    await waitFor(() => expect(screen.getByText(/离线模式/)).toBeTruthy())

    api.fetchInspirationPage.mockResolvedValue({
      items: [makeItem('n1')],
      total: 1,
      page: 1,
      size: 30,
    })
    fireEvent.press(screen.getByText('重试'))

    await waitFor(() => expect(screen.getByTestId('card-n1')).toBeTruthy())
    await waitFor(() => expect(screen.queryByText(/离线模式/)).toBeNull())
  })

  it('筛选视图（收藏）断网时不使用缓存，仍给错误态', async () => {
    await saveGalleryCache([makeItem('c1')], 1)
    api.fetchInspirationPage.mockRejectedValue(new Error('offline'))
    useInspirationStore.setState({ filter: { is_favorite: true } })

    render(<GalleryScreen />)

    await waitFor(() => expect(screen.getByText('加载失败')).toBeTruthy())
    expect(screen.queryByText(/离线模式/)).toBeNull()
    expect(screen.queryByTestId('card-c1')).toBeNull()
  })

  it('切到「随机」排序后联网成功，离线横幅也会消失', async () => {
    await saveGalleryCache([makeItem('c1')], 1)
    api.fetchInspirationPage.mockRejectedValueOnce(new Error('offline'))

    render(<GalleryScreen />)
    await waitFor(() => expect(screen.getByText(/离线模式/)).toBeTruthy())

    // 切排序 → 触发新请求（成功）→ 横幅必须撤掉
    api.fetchInspirationPage.mockResolvedValue({
      items: [makeItem('r1')],
      total: 1,
      page: 1,
      size: 30,
    })
    fireEvent.press(screen.getByTestId('sort-button'))
    fireEvent.press(await screen.findByText('随机'))

    await waitFor(() => expect(screen.getByTestId('card-r1')).toBeTruthy())
    await waitFor(() => expect(screen.queryByText(/离线模式/)).toBeNull())
  })

  it('成功拉到首页时会写入缓存（供下次断网使用）', async () => {
    api.fetchInspirationPage.mockResolvedValue({
      items: [makeItem('n1')],
      total: 1,
      page: 1,
      size: 30,
    })

    render(<GalleryScreen />)
    await waitFor(() => expect(screen.getByTestId('card-n1')).toBeTruthy())

    const raw = await AsyncStorage.getItem('offline:gallery')
    expect(raw).not.toBeNull()
    const parsed = JSON.parse(raw ?? '{}') as { items: { id: string }[] }
    expect(parsed.items.map((i) => i.id)).toEqual(['n1'])
  })
})
