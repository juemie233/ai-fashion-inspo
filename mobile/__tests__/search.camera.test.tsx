/** 拍照以图搜图（P0-5）：相机权限、裁剪参数、取消与失败路径。 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react-native'
import { Alert } from 'react-native'
import * as ImagePicker from 'expo-image-picker'

jest.mock('../services/api', () => {
  const actual = jest.requireActual('../services/api')
  return {
    ...actual,
    apiClient: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() },
    fetchTagGroups: jest.fn().mockResolvedValue([]),
    vectorSearchByImage: jest.fn(),
    vectorSearchByText: jest.fn(),
  }
})

import SearchScreen from '../app/(tabs)/search'
// eslint-disable-next-line @typescript-eslint/no-var-requires
const api = require('../services/api') as { vectorSearchByImage: jest.Mock }

const cameraMock = ImagePicker.launchCameraAsync as unknown as jest.Mock
const requestCamera = ImagePicker.requestCameraPermissionsAsync as unknown as jest.Mock

function makeHit(id: string) {
  return {
    inspiration: {
      id,
      source_type: 'douyin',
      file_path: `images/${id}.webp`,
      thumbnail_path: `thumbnails/${id}.jpg`,
      media_type: 'image',
      is_favorite: false,
      created_at: '2026-10-01T00:00:00Z',
      tags: [],
    },
    score: 0.9,
  }
}

beforeEach(() => {
  jest.clearAllMocks()
  requestCamera.mockResolvedValue({ granted: true })
  cameraMock.mockResolvedValue({ canceled: false, assets: [{ uri: 'file:///camera/q.jpg' }] })
  api.vectorSearchByImage.mockResolvedValue([makeHit('a'), makeHit('b')])
  jest.spyOn(Alert, 'alert').mockImplementation(() => {})
})

afterEach(() => {
  jest.restoreAllMocks()
})

describe('拍照搜图', () => {
  it('拍照（带裁剪）后跑图像检索并展示结果', async () => {
    render(<SearchScreen />)
    await waitFor(() => expect(screen.getByTestId('cond-camera')).toBeTruthy())

    fireEvent.press(screen.getByTestId('cond-camera'))

    await waitFor(() => expect(cameraMock).toHaveBeenCalled())
    // 关键参数：允许裁剪（拍完可裁主体），不带 aspect（Android 会锁死宽高比）
    expect(cameraMock.mock.calls[0][0]).toEqual(
      expect.objectContaining({ allowsEditing: true, quality: 0.8 }),
    )
    expect(cameraMock.mock.calls[0][0]).not.toHaveProperty('aspect')

    await waitFor(() =>
      expect(api.vectorSearchByImage).toHaveBeenCalledWith('file:///camera/q.jpg', 30),
    )
    await waitFor(() => expect(screen.getByText('找到 2 条结果')).toBeTruthy())
  })

  it('相机权限被拒时提示，且不打开相机', async () => {
    requestCamera.mockResolvedValue({ granted: false })
    render(<SearchScreen />)
    await waitFor(() => expect(screen.getByTestId('cond-camera')).toBeTruthy())

    fireEvent.press(screen.getByTestId('cond-camera'))

    await waitFor(() =>
      expect(Alert.alert).toHaveBeenCalledWith('权限不足', '请在系统设置中允许使用相机'),
    )
    expect(cameraMock).not.toHaveBeenCalled()
  })

  it('取消拍照不发起检索（当前列表保持不变）', async () => {
    cameraMock.mockResolvedValue({ canceled: true, assets: [] })
    render(<SearchScreen />)
    await waitFor(() => expect(screen.getByTestId('cond-camera')).toBeTruthy())

    fireEvent.press(screen.getByTestId('cond-camera'))

    await waitFor(() => expect(cameraMock).toHaveBeenCalled())
    expect(api.vectorSearchByImage).not.toHaveBeenCalled()
  })

  it('检索失败给出模型未就绪提示（不破坏页面）', async () => {
    api.vectorSearchByImage.mockRejectedValue(new Error('boom'))
    render(<SearchScreen />)
    await waitFor(() => expect(screen.getByTestId('cond-camera')).toBeTruthy())

    fireEvent.press(screen.getByTestId('cond-camera'))

    await waitFor(() =>
      expect(screen.getByText('以图搜图失败：可能图像向量模型未就绪')).toBeTruthy(),
    )
  })

  it('语义模式下拍照搜图禁用（与标签/以图一致）', async () => {
    render(<SearchScreen />)
    await waitFor(() => expect(screen.getByText('关键词')).toBeTruthy())

    fireEvent.press(screen.getByTestId('mode-semantic'))

    expect(screen.getByTestId('cond-camera').props.accessibilityState?.disabled).toBe(true)
    fireEvent.press(screen.getByTestId('cond-camera'))
    expect(cameraMock).not.toHaveBeenCalled()
  })
})
