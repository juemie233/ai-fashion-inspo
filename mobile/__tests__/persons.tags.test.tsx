/** 人物浏览（列表 + 作品墙）与标签轻量管理（浏览/搜索/改名）。 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react-native'

jest.mock('expo-router', () => ({
  useLocalSearchParams: () => ({ id: '3', kind: 'blogger' }),
  useRouter: () => ({ push: jest.fn(), back: jest.fn(), replace: jest.fn() }),
  Stack: ({ children }: { children?: unknown }) => children ?? null,
}))

jest.mock('../services/api', () => {
  const actual = jest.requireActual('../services/api')
  return {
    ...actual,
    apiClient: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() },
    fetchPersons: jest.fn(),
    fetchPersonInspirations: jest.fn(),
    fetchTagGroups: jest.fn(),
    renameTag: jest.fn(),
  }
})

import PersonsScreen from '../app/persons'
import PersonDetailScreen from '../app/person/[id]'
import TagsScreen from '../app/tags'
import { findBareText } from '../test-utils/noBareText'
// eslint-disable-next-line @typescript-eslint/no-var-requires
const api = require('../services/api') as {
  fetchPersons: jest.Mock
  fetchPersonInspirations: jest.Mock
  fetchTagGroups: jest.Mock
  renameTag: jest.Mock
}

const BLOGGERS = [
  {
    id: 3,
    kind: 'blogger',
    name: '一朵芝士',
    platform: 'douyin',
    avatar_path: 'avatars/a.jpg',
    inspiration_count: 2022,
    ip_location: '浙江',
    face_registered: true,
  },
  {
    id: 4,
    kind: 'blogger',
    name: '小鹿',
    platform: 'xiaohongshu',
    avatar_path: null,
    inspiration_count: 120,
    ip_location: null,
    face_registered: false,
  },
]

describe('PersonsScreen（人物列表）', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    api.fetchPersons.mockResolvedValue({ items: BLOGGERS, total: 2, page: 1, size: 20 })
  })

  it('渲染博主行（名称/平台/素材数/地域），无裸字符串', async () => {
    const utils = render(<PersonsScreen />)
    await waitFor(() => expect(screen.getByText('一朵芝士')).toBeTruthy())

    expect(screen.getByText('抖音 · 2022 张 · 浙江')).toBeTruthy()
    expect(screen.getByText('小红书 · 120 张')).toBeTruthy()
    expect(screen.getByText('共 2 位')).toBeTruthy()
    expect(findBareText(utils.toJSON())).toEqual([])
  })

  it('切到模特 Tab 会按 model 重新拉取', async () => {
    render(<PersonsScreen />)
    await waitFor(() => expect(screen.getByText('一朵芝士')).toBeTruthy())

    api.fetchPersons.mockResolvedValue({ items: [], total: 0, page: 1, size: 20 })
    fireEvent.press(screen.getByText('模特'))

    await waitFor(() => expect(api.fetchPersons).toHaveBeenCalledWith('model', 1, 20, ''))
    await waitFor(() => expect(screen.getByText('还没有模特')).toBeTruthy())
  })

  it('搜索把关键词传给接口', async () => {
    render(<PersonsScreen />)
    await waitFor(() => expect(screen.getByText('一朵芝士')).toBeTruthy())

    fireEvent.changeText(screen.getByPlaceholderText('搜人物名称'), '芝士')
    fireEvent(screen.getByPlaceholderText('搜人物名称'), 'submitEditing')

    await waitFor(() => expect(api.fetchPersons).toHaveBeenCalledWith('blogger', 1, 20, '芝士'))
  })

  it('加载失败显示错误文案', async () => {
    api.fetchPersons.mockRejectedValue(new Error('boom'))
    render(<PersonsScreen />)

    await waitFor(() => expect(screen.getByText('加载失败，请检查网络或后端服务')).toBeTruthy())
  })
})

describe('PersonDetailScreen（作品墙）', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    api.fetchPersons.mockResolvedValue({ items: BLOGGERS, total: 2, page: 1, size: 200 })
    api.fetchPersonInspirations.mockResolvedValue({
      items: [
        { id: 'a', file_path: 'images/a.jpg', thumbnail_path: null, tags: [] },
        { id: 'b', file_path: 'images/b.jpg', thumbnail_path: 'thumbnails/b.jpg', tags: [] },
      ],
      total: 2,
      page: 1,
      size: 30,
    })
  })

  it('显示人物资料与作品数', async () => {
    render(<PersonDetailScreen />)

    await waitFor(() => expect(screen.getByText('一朵芝士')).toBeTruthy())
    expect(screen.getByText('抖音 · 2022 张素材 · 浙江')).toBeTruthy()
    expect(screen.getByText('作品')).toBeTruthy()
    expect(api.fetchPersonInspirations).toHaveBeenCalledWith('blogger', 3, 1, 30)
  })

  it('没有作品时给出占位文案', async () => {
    api.fetchPersonInspirations.mockResolvedValue({ items: [], total: 0, page: 1, size: 30 })
    render(<PersonDetailScreen />)

    await waitFor(() => expect(screen.getByText('这个人还没有作品')).toBeTruthy())
  })
})

describe('TagsScreen（标签管理）', () => {
  const GROUPS = [
    { category: 'style', tags: [{ id: 1, name: '甜美', category: 'style' }] },
    { category: 'color', tags: [{ id: 2, name: '白色', category: 'color' }] },
  ]

  beforeEach(() => {
    jest.clearAllMocks()
    api.fetchTagGroups.mockResolvedValue(GROUPS)
    api.renameTag.mockResolvedValue({ id: 1, name: '新甜美' })
  })

  it('渲染标签与规模提示，无裸字符串', async () => {
    const utils = render(<TagsScreen />)
    await waitFor(() => expect(screen.getByText('甜美')).toBeTruthy())

    expect(screen.getByText('共 2 个标签，输入关键词缩小范围')).toBeTruthy()
    expect(screen.getByText('白色')).toBeTruthy()
    expect(findBareText(utils.toJSON())).toEqual([])
  })

  it('搜索过滤标签并提示匹配数', async () => {
    render(<TagsScreen />)
    await waitFor(() => expect(screen.getByText('甜美')).toBeTruthy())

    fireEvent.changeText(screen.getByPlaceholderText('搜索标签'), '白')

    await waitFor(() => expect(screen.getByText('白色')).toBeTruthy())
    expect(screen.queryByText('甜美')).toBeNull()
    expect(screen.getByText('匹配 1 个')).toBeTruthy()
  })

  it('点标签打开改名弹层，保存后调用 renameTag 并本地更新', async () => {
    render(<TagsScreen />)
    await waitFor(() => expect(screen.getByText('甜美')).toBeTruthy())

    fireEvent.press(screen.getByTestId('tag-1'))
    await waitFor(() => expect(screen.getByText('重命名标签')).toBeTruthy())
    expect(screen.getByText('原名：甜美')).toBeTruthy()

    fireEvent.changeText(screen.getByPlaceholderText('新名称'), '新甜美')
    fireEvent.press(screen.getByText('保存'))

    await waitFor(() => expect(api.renameTag).toHaveBeenCalledWith(1, '新甜美'))
    await waitFor(() => expect(screen.getByText('新甜美')).toBeTruthy())
  })

  it('名称没改动时不发请求', async () => {
    render(<TagsScreen />)
    await waitFor(() => expect(screen.getByText('甜美')).toBeTruthy())

    fireEvent.press(screen.getByTestId('tag-1'))
    await waitFor(() => expect(screen.getByText('重命名标签')).toBeTruthy())
    fireEvent.press(screen.getByText('保存'))

    expect(api.renameTag).not.toHaveBeenCalled()
  })

  it('改名失败时提示且弹层保留（便于重试）', async () => {
    api.renameTag.mockRejectedValue(new Error('duplicate'))
    render(<TagsScreen />)
    await waitFor(() => expect(screen.getByText('甜美')).toBeTruthy())

    fireEvent.press(screen.getByTestId('tag-1'))
    await waitFor(() => expect(screen.getByText('重命名标签')).toBeTruthy())
    fireEvent.changeText(screen.getByPlaceholderText('新名称'), '重复名')
    fireEvent.press(screen.getByText('保存'))

    await waitFor(() => expect(screen.getByText('改名失败：名称可能重复')).toBeTruthy())
  })
})
