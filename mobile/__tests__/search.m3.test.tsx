/** 搜索页 M3：分页、以图搜图、标签筛选弹层不渲染全量标签。
 *
 * 审查修复后的断言变化：
 * - 「搜索」既是页头标题又是按钮文案 → 按钮改用 testID `search-submit` 定位
 * - 语义模式下标签/以图搜图**禁用**（改前是隐藏且静默忽略已选标签）
 * - 初始态不再是空白：展示最近搜索 + 常用标签（P2-5）
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react-native'
import * as ImagePicker from 'expo-image-picker'
import { Alert } from 'react-native'

jest.mock('../services/api', () => {
  const actual = jest.requireActual('../services/api')
  return {
    ...actual,
    apiClient: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() },
    // 搜索页标签筛选走 fetchTagGroups（会话缓存）；actual 内部仍引用真实 axios，
    // 不单独 mock 会打到真后端（tagsLoading 永远停在「正在加载标签…」）
    fetchTagGroups: jest.fn().mockResolvedValue([
      { category: 'style', tags: [{ id: 1, name: '甜美', category: 'style', usage_count: 12 }] },
    ]),
    vectorSearchByImage: jest.fn(),
    vectorSearchByText: jest.fn(),
  }
})

import SearchScreen from '../app/(tabs)/search'
import { findBareText } from '../test-utils/noBareText'
// eslint-disable-next-line @typescript-eslint/no-var-requires
const api = require('../services/api') as {
  apiClient: { get: jest.Mock }
  vectorSearchByImage: jest.Mock
  vectorSearchByText: jest.Mock
}

function makeItem(id: string) {
  return {
    id,
    source_type: 'generated',
    file_path: `images/${id}.webp`,
    thumbnail_path: `thumbnails/${id}.jpg`,
    media_type: 'image',
    is_favorite: false,
    created_at: '2026-10-01T00:00:00Z',
    tags: [],
  }
}

describe('SearchScreen（M3）', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    // /tags 返回少量标签，/search 返回两页数据
    api.apiClient.get.mockImplementation(
      (url: string, config?: { params?: Record<string, unknown> }) => {
        if (url === '/tags') {
          return Promise.resolve({
            data: [{ category: 'style', tags: [{ id: 1, name: '甜美', category: 'style' }] }],
          })
        }
        const page = Number(config?.params?.page ?? 1)
        return Promise.resolve({
          data: {
            items: page === 1 ? [makeItem('a'), makeItem('b')] : [makeItem('c')],
            total: 3,
          },
        })
      },
    )
    jest.spyOn(Alert, 'alert').mockImplementation(() => {})
  })

  afterEach(() => {
    jest.restoreAllMocks()
  })

  it('初始界面有搜索框、标签筛选与以图搜图入口，且无裸字符串', async () => {
    const utils = render(<SearchScreen />)
    await waitFor(() => expect(screen.getByText('标签')).toBeTruthy())

    expect(screen.getByPlaceholderText('搜标签名 / 作者 / 文件名')).toBeTruthy()
    expect(screen.getByText('以图搜图')).toBeTruthy()
    expect(screen.getByTestId('search-submit')).toBeTruthy()
    expect(findBareText(utils.toJSON())).toEqual([])
  })

  it('初始态展示最近搜索与常用标签，不再是空白页', async () => {
    render(<SearchScreen />)

    // 常用标签来自标签库 usage_count 排序（后端分组响应自带）
    await waitFor(() => expect(screen.getByText('常用标签')).toBeTruthy())
    expect(screen.getAllByText('甜美').length).toBeGreaterThan(0)
  })

  it('搜索走 /search 并带分页参数，结果数量正确', async () => {
    render(<SearchScreen />)

    fireEvent.press(screen.getByTestId('search-submit'))

    await waitFor(() =>
      expect(api.apiClient.get).toHaveBeenCalledWith('/search', {
        params: { page: 1, size: 30 },
      }),
    )
    await waitFor(() => expect(screen.getByText('找到 3 条结果')).toBeTruthy())
  })

  it('关键词以 keyword 参数提交（不是塞进 include_tags）', async () => {
    render(<SearchScreen />)
    fireEvent.changeText(screen.getByPlaceholderText('搜标签名 / 作者 / 文件名'), '白裙')
    fireEvent.press(screen.getByTestId('search-submit'))

    await waitFor(() =>
      expect(api.apiClient.get).toHaveBeenCalledWith('/search', {
        params: { page: 1, size: 30, keyword: '白裙' },
      }),
    )
  })

  it('以图搜图：选图后调用向量检索并展示结果', async () => {
    ;(ImagePicker.launchImageLibraryAsync as unknown as jest.Mock).mockResolvedValue({
      canceled: false,
      assets: [{ uri: 'file:///tmp/q.jpg' }],
    })
    api.vectorSearchByImage.mockResolvedValue([
      { inspiration: makeItem('x'), score: 0.91 },
      { inspiration: makeItem('y'), score: 0.82 },
    ])

    render(<SearchScreen />)
    fireEvent.press(screen.getByText('以图搜图'))

    await waitFor(() => expect(api.vectorSearchByImage).toHaveBeenCalledWith('file:///tmp/q.jpg', 30))
    await waitFor(() => expect(screen.getByText('以这张图搜到的相似素材')).toBeTruthy())
    expect(screen.getByText('找到 2 条结果')).toBeTruthy()
  })

  it('以图搜图失败时提示（模型未就绪是常见原因）', async () => {
    ;(ImagePicker.launchImageLibraryAsync as unknown as jest.Mock).mockResolvedValue({
      canceled: false,
      assets: [{ uri: 'file:///tmp/q.jpg' }],
    })
    api.vectorSearchByImage.mockRejectedValue(new Error('boom'))

    render(<SearchScreen />)
    fireEvent.press(screen.getByText('以图搜图'))

    await waitFor(() =>
      expect(screen.getByText('以图搜图失败：可能图像向量模型未就绪')).toBeTruthy(),
    )
  })

  it('相册权限被拒绝时提示，且不发起检索', async () => {
    ;(ImagePicker.requestMediaLibraryPermissionsAsync as unknown as jest.Mock).mockResolvedValue({
      granted: false,
    })

    render(<SearchScreen />)
    fireEvent.press(screen.getByText('以图搜图'))

    await waitFor(() =>
      expect(Alert.alert).toHaveBeenCalledWith('权限不足', '请在系统设置中允许访问相册'),
    )
    expect(api.vectorSearchByImage).not.toHaveBeenCalled()
  })

  it('切到语义模式：标签/以图入口变为禁用并给出原因（不再静默忽略）', async () => {
    render(<SearchScreen />)
    await waitFor(() => expect(screen.getByText('关键词')).toBeTruthy())

    fireEvent.press(screen.getByTestId('mode-semantic'))

    expect(screen.getByText('语义检索不支持标签筛选')).toBeTruthy()
    // RNTL 12 未挂 jest-native 的 toBeDisabled，直接读无障碍状态
    expect(screen.getByTestId('cond-tag').props.accessibilityState?.disabled).toBe(true)
    expect(screen.getByTestId('cond-image').props.accessibilityState?.disabled).toBe(true)
  })

  it('绕过禁用点标签 chip 不会打开筛选弹层', async () => {
    render(<SearchScreen />)
    await waitFor(() => expect(screen.getByText('关键词')).toBeTruthy())
    fireEvent.press(screen.getByTestId('mode-semantic'))

    fireEvent.press(screen.getByTestId('cond-tag'))
    expect(screen.queryByText('按标签筛选')).toBeNull()
  })

  it('语义模式没输入文本时给提示，不发请求', async () => {
    render(<SearchScreen />)
    await waitFor(() => expect(screen.getByText('语义')).toBeTruthy())

    fireEvent.press(screen.getByTestId('mode-semantic'))
    fireEvent.press(screen.getByTestId('search-submit'))

    await waitFor(() =>
      expect(screen.getByText('语义检索需要先输入一句话，例如「白色连衣裙 约会」')).toBeTruthy(),
    )
    expect(api.vectorSearchByText).not.toHaveBeenCalled()
    expect(api.apiClient.get).not.toHaveBeenCalledWith('/search', expect.anything())
  })

  it('语义模式走向量检索，标题显示「语义匹配」', async () => {
    api.vectorSearchByText.mockResolvedValue([
      { inspiration: makeItem('s1'), score: 0.9 },
      { inspiration: makeItem('s2'), score: 0.8 },
    ])

    render(<SearchScreen />)
    await waitFor(() => expect(screen.getByText('语义')).toBeTruthy())

    fireEvent.press(screen.getByTestId('mode-semantic'))
    fireEvent.changeText(
      screen.getByPlaceholderText('搜标签名 / 作者 / 文件名'),
      '白色连衣裙 约会',
    )
    fireEvent.press(screen.getByTestId('search-submit'))

    await waitFor(() => expect(api.vectorSearchByText).toHaveBeenCalledWith('白色连衣裙 约会', 30))
    await waitFor(() => expect(screen.getByText('语义匹配 2 条')).toBeTruthy())
  })

  it('语义检索失败时提示模型未就绪', async () => {
    api.vectorSearchByText.mockRejectedValue(new Error('boom'))

    render(<SearchScreen />)
    await waitFor(() => expect(screen.getByText('语义')).toBeTruthy())

    fireEvent.press(screen.getByTestId('mode-semantic'))
    fireEvent.changeText(screen.getByPlaceholderText('搜标签名 / 作者 / 文件名'), '通勤')
    fireEvent.press(screen.getByTestId('search-submit'))

    await waitFor(() =>
      expect(
        screen.getByText('语义检索失败：可能文本向量模型未就绪（需 Ollama + 嵌入模型）'),
      ).toBeTruthy(),
    )
  })

  it('标签筛选弹层：打开后显示标签库规模提示（不全量渲染）', async () => {
    render(<SearchScreen />)
    await waitFor(() => expect(screen.getByText('标签')).toBeTruthy())

    fireEvent.press(screen.getByText('标签'))

    await waitFor(() => expect(screen.getByText('按标签筛选')).toBeTruthy())
    expect(screen.getByText('共 1 个标签，输入关键词缩小范围')).toBeTruthy()
    expect(screen.getAllByText('甜美').length).toBeGreaterThan(0)
  })

  it('没有结果时给「清除标签」入口', async () => {
    api.apiClient.get.mockImplementation((url: string) => {
      if (url === '/tags') return Promise.resolve({ data: [] })
      return Promise.resolve({ data: { items: [], total: 0 } })
    })

    render(<SearchScreen />)
    fireEvent.changeText(screen.getByPlaceholderText('搜标签名 / 作者 / 文件名'), '不存在')
    fireEvent.press(screen.getByTestId('search-submit'))

    await waitFor(() => expect(screen.getByText('没有找到匹配的素材')).toBeTruthy())
  })
})
