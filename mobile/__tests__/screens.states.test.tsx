/** 页面级状态与交互回归：冷启动单次请求、人物搜索防抖、三态切换。
 *
 * 对应审查报告 P0-1 / P0-2 / P0-3 —— 这三条光看代码很难确认，必须用「请求次数」
 * 与「渲染出来的是哪一种态」来断言。
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react-native'
import { Alert } from 'react-native'

jest.mock('expo-router', () => ({
  useLocalSearchParams: () => ({ id: '1' }),
  useRouter: () => ({ push: jest.fn(), back: jest.fn(), replace: jest.fn() }),
  Stack: ({ children }: { children?: unknown }) => children ?? null,
  Tabs: ({ children }: { children?: unknown }) => children ?? null,
}))

jest.mock('../services/api', () => {
  const actual = jest.requireActual('../services/api')
  return {
    ...actual,
    apiClient: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() },
    fetchInspirationPage: jest.fn(),
    fetchCollections: jest.fn(),
    fetchPersons: jest.fn(),
  }
})

import GalleryScreen from '../app/(tabs)/index'
import PersonsScreen from '../app/persons'
import { useInspirationStore } from '../hooks/useInspirations'
import { mockedApi } from '../test-utils/mockedApi'

const api = mockedApi()

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

function makePerson(id: number, name: string) {
  return {
    id,
    kind: 'blogger' as const,
    name,
    platform: 'douyin' as const,
    avatar_path: null,
    bio: null,
    ip_location: null,
    inspiration_count: 3,
    face_registered: false,
  }
}

beforeEach(() => {
  jest.clearAllMocks()
  api.fetchInspirationPage.mockResolvedValue({ items: [makeItem('i1')], total: 1, page: 1, size: 30 })
  api.fetchCollections.mockResolvedValue([])
  api.fetchPersons.mockResolvedValue({ items: [makePerson(1, '小A')], total: 1, page: 1, size: 20 })
  useInspirationStore.setState({
    items: [],
    total: 0,
    page: 1,
    loading: false,
    error: null,
    filter: {},
    sort: 'newest',
    apiBaseUrl: 'http://127.0.0.1:18888',
  })
  jest.spyOn(Alert, 'alert').mockImplementation(() => {})
})

afterEach(() => {
  jest.restoreAllMocks()
})

describe('冷启动（P0-3）', () => {
  it('init() 只恢复地址，不拉第一页', async () => {
    await useInspirationStore.getState().init()
    expect(api.fetchInspirationPage).not.toHaveBeenCalled()
  })

  it('素材库挂载时只发一次第一页请求（不再与 init 重复）', async () => {
    await useInspirationStore.getState().init()
    render(<GalleryScreen />)

    await waitFor(() => expect(api.fetchInspirationPage).toHaveBeenCalledTimes(1))
    expect(api.fetchInspirationPage).toHaveBeenCalledWith(1, 30, {}, 'newest')
  })
})

describe('人物搜索防抖（P0-2）', () => {
  it('连敲 6 个字只发 1 次请求', async () => {
    render(<PersonsScreen />)
    await waitFor(() => expect(api.fetchPersons).toHaveBeenCalledTimes(1))
    api.fetchPersons.mockClear()

    const input = screen.getByPlaceholderText('搜人物名称')
    for (const ch of '穿搭博主') {
      fireEvent.changeText(input, `穿搭${ch}`)
    }

    // 防抖 300ms：只应发一次（最后一个词），且不会把列表换成整屏 spinner
    await waitFor(() => expect(api.fetchPersons).toHaveBeenCalledTimes(1), { timeout: 5000 })
    expect(api.fetchPersons).toHaveBeenCalledWith('blogger', 1, 20, '穿搭主')
  })

  it('加载失败显示错误态并可重试（不再显示「还没有穿搭博主」）', async () => {
    api.fetchPersons.mockRejectedValue(new Error('offline'))
    render(<PersonsScreen />)

    await waitFor(() => expect(screen.getByText('加载失败')).toBeTruthy())
    expect(screen.queryByText('还没有穿搭博主')).toBeNull()

    api.fetchPersons.mockClear()
    api.fetchPersons.mockResolvedValue({ items: [], total: 0, page: 1, size: 20 })
    fireEvent.press(screen.getByTestId('retry-button'))
    await waitFor(() => expect(api.fetchPersons).toHaveBeenCalled())
  })

  it('切换 Tab 会清空旧数据并重新拉取', async () => {
    render(<PersonsScreen />)
    await waitFor(() => expect(screen.getByText('小A')).toBeTruthy())

    fireEvent.press(screen.getByTestId('person-kind-model'))
    await waitFor(() => expect(api.fetchPersons).toHaveBeenCalledWith('model', 1, 20, ''))
  })
})
