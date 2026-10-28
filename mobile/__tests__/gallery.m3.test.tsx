/** 素材库 M3：筛选胶囊、排序、长按多选、批量操作与合集选择器。 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react-native'
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
import { findBareText } from '../test-utils/noBareText'
// eslint-disable-next-line @typescript-eslint/no-var-requires
const api = require('../services/api') as {
  fetchInspirationPage: jest.Mock
  fetchCollections: jest.Mock
  createCollection: jest.Mock
  addToCollection: jest.Mock
  batchFavorite: jest.Mock
  batchTrash: jest.Mock
}

function makeItem(over: Record<string, unknown> = {}) {
  return {
    id: 'i1',
    source_type: 'douyin',
    file_path: 'images/a.webp',
    thumbnail_path: 'thumbnails/a.jpg',
    media_type: 'image',
    is_favorite: false,
    created_at: '2026-10-01T00:00:00Z',
    tags: [],
    ...over,
  }
}

const ITEMS = [makeItem({ id: 'i1' }), makeItem({ id: 'i2', is_favorite: true }), makeItem({ id: 'i3' })]

describe('GalleryScreen（M3）', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    api.fetchInspirationPage.mockResolvedValue({ items: ITEMS, total: 3, page: 1, size: 30 })
    api.fetchCollections.mockResolvedValue([
      { id: 1, name: '春日穿搭', kind: 'manual', item_count: 5 },
      { id: 2, name: '智能合集', kind: 'smart', item_count: null },
      { id: 3, name: '二级夹', kind: 'manual', parent_id: 1, item_count: 2 },
    ])
    useInspirationStore.setState({
      items: ITEMS as never,
      total: 3,
      page: 1,
      loading: false,
      filter: {},
      sort: 'newest',
    })
    jest.spyOn(Alert, 'alert').mockImplementation(() => {})
  })

  afterEach(() => {
    jest.restoreAllMocks()
  })

  it('渲染筛选胶囊与排序按钮，且无裸字符串', () => {
    const utils = render(<GalleryScreen />)

    expect(screen.getByText('全部')).toBeTruthy()
    expect(screen.getByText('收藏')).toBeTruthy()
    expect(screen.getByText('已标注')).toBeTruthy()
    expect(screen.getByText('未标注')).toBeTruthy()
    expect(screen.getByText('最新')).toBeTruthy()
    expect(findBareText(utils.toJSON())).toEqual([])
  })

  it('点「收藏」胶囊触发带 is_favorite 的筛选', async () => {
    render(<GalleryScreen />)
    fireEvent.press(screen.getByText('收藏'))

    await waitFor(() =>
      expect(api.fetchInspirationPage).toHaveBeenCalledWith(
        1,
        30,
        expect.objectContaining({ is_favorite: true }),
        'newest',
      ),
    )
  })

  it('点「全部」会清掉上一个筛选条件（胶囊互斥，不是叠加）', async () => {
    render(<GalleryScreen />)

    fireEvent.press(screen.getByText('收藏'))
    await waitFor(() =>
      expect(api.fetchInspirationPage).toHaveBeenLastCalledWith(
        1,
        30,
        expect.objectContaining({ is_favorite: true }),
        'newest',
      ),
    )

    fireEvent.press(screen.getByText('全部'))
    await waitFor(() =>
      expect(api.fetchInspirationPage).toHaveBeenLastCalledWith(1, 30, {}, 'newest'),
    )
  })

  it('切换筛选会退出多选，避免批量操作作用于看不见的旧素材', async () => {
    render(<GalleryScreen />)

    fireEvent(screen.getByTestId('card-i1'), 'longPress')
    await waitFor(() => expect(screen.getByText('已选 1 张')).toBeTruthy())

    fireEvent.press(screen.getByTestId('filter-fav'))
    await waitFor(() => expect(screen.queryByText('已选 1 张')).toBeNull())
  })

  it('点排序按钮在四种排序间轮转', async () => {
    render(<GalleryScreen />)
    fireEvent.press(screen.getByText('最新'))

    await waitFor(() => expect(api.fetchInspirationPage).toHaveBeenCalledWith(1, 30, {}, 'oldest'))
    await waitFor(() => expect(screen.getByText('最早')).toBeTruthy())
  })

  it('长按卡片进入多选，显示已选数量', async () => {
    render(<GalleryScreen />)

    fireEvent(screen.getByTestId('card-i2'), 'longPress')

    await waitFor(() => expect(screen.getByText('已选 1 张')).toBeTruthy())
    expect(screen.getByText('加合集')).toBeTruthy()
    expect(screen.getByText('移入垃圾桶')).toBeTruthy()
  })

  it('批量收藏：调用 batchFavorite 并退出多选', async () => {
    api.batchFavorite.mockResolvedValue({ updated: 1 })
    render(<GalleryScreen />)

    fireEvent(screen.getByTestId('card-i2'), 'longPress')
    await waitFor(() => expect(screen.getByText('已选 1 张')).toBeTruthy())

    fireEvent.press(screen.getByTestId('batch-favorite'))
    await waitFor(() => expect(api.batchFavorite).toHaveBeenCalledWith(['i2'], false))
    await waitFor(() => expect(screen.queryByText('已选 1 张')).toBeNull())
  })

  it('批量移入垃圾桶需二次确认', async () => {
    api.batchTrash.mockResolvedValue({ trashed: 1 })
    render(<GalleryScreen />)

    fireEvent(screen.getByTestId('card-i1'), 'longPress')
    await waitFor(() => expect(screen.getByText('已选 1 张')).toBeTruthy())

    fireEvent.press(screen.getByTestId('batch-trash'))
    expect(Alert.alert).toHaveBeenCalled()

    const buttons = (Alert.alert as jest.Mock).mock.calls[0][2] as {
      text: string
      onPress?: () => void
    }[]
    buttons.find((b) => b.text === '移入')?.onPress?.()

    await waitFor(() => expect(api.batchTrash).toHaveBeenCalledWith(['i1']), { timeout: 5000 })
  })

  it('加合集：只列一级手动合集，点选后调用 addToCollection', async () => {
    api.addToCollection.mockResolvedValue(undefined)
    render(<GalleryScreen />)

    fireEvent(screen.getByTestId('card-i2'), 'longPress')
    await waitFor(() => expect(screen.getByText('已选 1 张')).toBeTruthy())

    fireEvent.press(screen.getByTestId('batch-collection'))
    await waitFor(() => expect(screen.getByText('加入合集')).toBeTruthy())

    await waitFor(() => expect(screen.getByText('春日穿搭')).toBeTruthy())
    expect(screen.queryByText('智能合集')).toBeNull()
    expect(screen.queryByText('二级夹')).toBeNull()

    fireEvent.press(screen.getByText('春日穿搭'))
    await waitFor(() => expect(api.addToCollection).toHaveBeenCalledWith(1, ['i2']))
  })

  it('视频素材显示「视频」角标，图片素材不显示', async () => {
    useInspirationStore.setState({
      items: [
        makeItem({ id: 'v1', media_type: 'video' }),
        makeItem({ id: 'i9', media_type: 'image' }),
      ] as never,
      total: 2,
      page: 1,
      filter: {},
    })
    render(<GalleryScreen />)

    expect(screen.getByText('视频')).toBeTruthy()
    // 两张卡片里只有一个视频角标
    expect(screen.getAllByText('视频')).toHaveLength(1)
  })

  it('筛选结果为空时给出对应文案而不是「还没有素材」', async () => {
    useInspirationStore.setState({ items: [], total: 0, filter: { is_favorite: true } })
    render(<GalleryScreen />)

    expect(screen.getByText('还没有收藏的素材')).toBeTruthy()
  })
})
