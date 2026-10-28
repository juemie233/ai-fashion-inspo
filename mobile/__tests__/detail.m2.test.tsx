/** 详情页 M2 行为：标签编辑入口、移入垃圾桶、相似推荐、已有标签渲染。 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react-native'
import { Alert } from 'react-native'

jest.mock('expo-router', () => ({
  useLocalSearchParams: () => ({ id: 'i1' }),
  useRouter: () => ({ push: jest.fn(), back: jest.fn(), replace: jest.fn() }),
  Stack: ({ children }: { children?: unknown }) => children ?? null,
}))

jest.mock('../services/api', () => {
  const actual = jest.requireActual('../services/api')
  return {
    ...actual,
    apiClient: { get: jest.fn(), patch: jest.fn(), post: jest.fn(), delete: jest.fn() },
    addTagsToInspiration: jest.fn(),
    removeTagFromInspiration: jest.fn(),
    trashInspiration: jest.fn(),
    fetchSimilarInspirations: jest.fn(),
    fetchTagGroups: jest.fn(),
  }
})

import DetailScreen from '../app/detail/[id]'
import { findBareText } from '../test-utils/noBareText'
// eslint-disable-next-line @typescript-eslint/no-var-requires
const api = require('../services/api') as {
  apiClient: { get: jest.Mock; patch: jest.Mock }
  addTagsToInspiration: jest.Mock
  trashInspiration: jest.Mock
  fetchSimilarInspirations: jest.Mock
  fetchTagGroups: jest.Mock
}

const DETAIL = {
  id: 'i1',
  source_type: 'generated',
  source_url: null,
  source_author: null,
  file_path: 'images/2026-10/a.webp',
  thumbnail_path: 'thumbnails/2026-10/a.jpg',
  media_type: 'image',
  is_favorite: false,
  created_at: '2026-10-23T00:00:00Z',
  tags: [{ tag: { id: 1, name: '甜美', category: 'style' }, confidence: 0.9 }],
}

describe('DetailScreen（M2）', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    api.apiClient.get.mockResolvedValue({ data: DETAIL })
    api.fetchSimilarInspirations.mockResolvedValue([])
    api.fetchTagGroups.mockResolvedValue([
      { category: 'style', tags: [{ id: 1, name: '甜美', category: 'style' }] },
    ])
  })

  it('渲染详情与已有标签，且无裸字符串', async () => {
    const utils = render(<DetailScreen />)
    await waitFor(() => expect(screen.getByText('甜美')).toBeTruthy())

    expect(screen.getByText('编辑标签')).toBeTruthy()
    expect(screen.getByText('删除')).toBeTruthy()
    expect(findBareText(utils.toJSON())).toEqual([])
    // 没有相似结果时不渲染「相似素材」标题
    expect(screen.queryByText('相似素材')).toBeNull()
  })

  it('有相似结果时渲染「相似素材」区块', async () => {
    api.fetchSimilarInspirations.mockResolvedValue([
      { id: 'x1', file_path: 'images/x1.jpg', thumbnail_path: null, tags: [] },
    ])

    render(<DetailScreen />)
    await waitFor(() => expect(screen.getByText('相似素材')).toBeTruthy())
  })

  it('点「编辑标签」拉取标签库并打开弹层', async () => {
    render(<DetailScreen />)
    await waitFor(() => expect(screen.getByText('编辑标签')).toBeTruthy())

    fireEvent.press(screen.getByText('编辑标签'))

    await waitFor(() => expect(api.fetchTagGroups).toHaveBeenCalled())
    // 弹层已打开（标题 + 空暂存态提示）
    await waitFor(() => expect(screen.getByText('点已关联的标签可移除')).toBeTruthy())
  })

  it('点「删除」弹二次确认，确认后调移入垃圾桶并返回', async () => {
    const alertSpy = jest.spyOn(Alert, 'alert').mockImplementation(() => {})
    api.trashInspiration.mockResolvedValue({ id: 'i1' })

    render(<DetailScreen />)
    await waitFor(() => expect(screen.getByText('删除')).toBeTruthy())

    fireEvent.press(screen.getByText('删除'))
    expect(alertSpy).toHaveBeenCalled()

    const buttons = alertSpy.mock.calls[0][2] as { text: string; onPress?: () => void }[]
    const confirm = buttons.find((b) => b.text === '移入')
    expect(confirm).toBeTruthy()

    await act(async () => {
      confirm?.onPress?.()
    })
    await waitFor(() => expect(api.trashInspiration).toHaveBeenCalledWith('i1'), { timeout: 5000 })

    alertSpy.mockRestore()
  })

  it('素材不存在时（404）显示「素材未找到」', async () => {
    api.apiClient.get.mockRejectedValue({ response: { status: 404 } })

    render(<DetailScreen />)
    await waitFor(() => expect(screen.getByText('素材未找到')).toBeTruthy())
  })
})
