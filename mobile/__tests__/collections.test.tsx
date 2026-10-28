/** 合集相关界面：宫格页、合集内容页、合集选择器。 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react-native'
import { Alert } from 'react-native'

jest.mock('expo-router', () => ({
  useLocalSearchParams: () => ({ id: '3' }),
  useRouter: () => ({ push: jest.fn(), back: jest.fn(), replace: jest.fn() }),
  Stack: ({ children }: { children?: unknown }) => children ?? null,
}))

jest.mock('../services/api', () => {
  const actual = jest.requireActual('../services/api')
  return {
    ...actual,
    apiClient: { get: jest.fn(), post: jest.fn(), delete: jest.fn(), patch: jest.fn() },
    fetchCollections: jest.fn(),
    createCollection: jest.fn(),
    addToCollection: jest.fn(),
    fetchCollectionInspirations: jest.fn(),
  }
})

import CollectionsScreen from '../app/collections'
import CollectionDetailScreen from '../app/collection/[id]'
import CollectionPicker from '../components/CollectionPicker'
import { findBareText } from '../test-utils/noBareText'
// eslint-disable-next-line @typescript-eslint/no-var-requires
const api = require('../services/api') as {
  fetchCollections: jest.Mock
  fetchCollectionInspirations: jest.Mock
  createCollection: jest.Mock
  addToCollection: jest.Mock
}

describe('CollectionsScreen（合集宫格）', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('渲染合集封面信息与数量，且无裸字符串', async () => {
    api.fetchCollections.mockResolvedValue([
      { id: 1, name: '春日穿搭', kind: 'manual', item_count: 12, cover_thumbnail_path: null },
      { id: 2, name: '通勤', kind: 'manual', item_count: 3, cover_thumbnail_path: 'thumbnails/2.jpg' },
      { id: 4, name: '二级夹', kind: 'manual', parent_id: 1, item_count: 1 },
    ])

    const utils = render(<CollectionsScreen />)
    await waitFor(() => expect(screen.getByText('春日穿搭')).toBeTruthy())

    expect(screen.getByText('12 张')).toBeTruthy()
    expect(screen.getByText('3 张')).toBeTruthy()
    // 二级收藏夹不在宫格里展示
    expect(screen.queryByText('二级夹')).toBeNull()
    expect(findBareText(utils.toJSON())).toEqual([])
  })

  it('加载失败显示错误文案', async () => {
    api.fetchCollections.mockRejectedValue(new Error('boom'))

    render(<CollectionsScreen />)
    await waitFor(() => expect(screen.getByText('加载失败，请检查网络或后端服务')).toBeTruthy())
  })

  it('没有合集时给出引导', async () => {
    api.fetchCollections.mockResolvedValue([])

    render(<CollectionsScreen />)
    await waitFor(() => expect(screen.getByText('还没有合集')).toBeTruthy())
    expect(screen.getByText('在素材库长按素材 → 加合集，可现场新建')).toBeTruthy()
  })
})

describe('CollectionDetailScreen（合集内容）', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('渲染合集内素材', async () => {
    api.fetchCollectionInspirations.mockResolvedValue({
      items: [
        { id: 'a', file_path: 'images/a.jpg', thumbnail_path: null, tags: [] },
        { id: 'b', file_path: 'images/b.jpg', thumbnail_path: 'thumbnails/b.jpg', tags: [] },
      ],
      total: 2,
      page: 1,
      size: 30,
    })

    render(<CollectionDetailScreen />)
    await waitFor(() => expect(api.fetchCollectionInspirations).toHaveBeenCalledWith(3, 1, 30))
  })

  it('空合集显示占位文案', async () => {
    api.fetchCollectionInspirations.mockResolvedValue({ items: [], total: 0, page: 1, size: 30 })

    render(<CollectionDetailScreen />)
    await waitFor(() => expect(screen.getByText('合集里还没有素材')).toBeTruthy())
  })
})

describe('CollectionPicker（加合集）', () => {
  const COLLECTIONS = [
    { id: 1, name: '春日穿搭', kind: 'manual', item_count: 5 },
    { id: 2, name: '智能合集', kind: 'smart', item_count: null },
    { id: 3, name: '二级夹', kind: 'manual', parent_id: 1, item_count: 2 },
  ] as never

  it('只列一级手动合集，点选回调带合集对象', () => {
    const onPick = jest.fn()
    render(
      <CollectionPicker
        visible
        count={3}
        collections={COLLECTIONS}
        loading={false}
        onClose={jest.fn()}
        onPick={onPick}
        onCreate={jest.fn()}
      />,
    )

    expect(screen.getByText('加入合集')).toBeTruthy()
    expect(screen.getByText('把选中的 3 张素材加入下面某个合集')).toBeTruthy()
    expect(screen.queryByText('智能合集')).toBeNull()
    expect(screen.queryByText('二级夹')).toBeNull()

    fireEvent.press(screen.getByText('春日穿搭'))
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ id: 1 }))
  })

  it('输入名称后「新建并加入」回调带名称；空名不可点', () => {
    const onCreate = jest.fn()
    render(
      <CollectionPicker
        visible
        count={1}
        collections={[] as never}
        loading={false}
        onClose={jest.fn()}
        onPick={jest.fn()}
        onCreate={onCreate}
      />,
    )

    fireEvent.press(screen.getByText('新建并加入'))
    expect(onCreate).not.toHaveBeenCalled()

    fireEvent.changeText(screen.getByPlaceholderText('新建合集名称'), '夏日')
    fireEvent.press(screen.getByText('新建并加入'))
    expect(onCreate).toHaveBeenCalledWith('夏日')
  })

  it('没有合集时提示可以现场新建', () => {
    render(
      <CollectionPicker
        visible
        count={1}
        collections={[] as never}
        loading={false}
        onClose={jest.fn()}
        onPick={jest.fn()}
        onCreate={jest.fn()}
      />,
    )

    expect(screen.getByText('还没有合集，可以在上面新建一个')).toBeTruthy()
  })
})
