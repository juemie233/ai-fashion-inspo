/** 采集页：相册多选入队、逐张上传、失败标记。 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react-native'
import * as ImagePicker from 'expo-image-picker'
import { Alert } from 'react-native'

jest.mock('../services/api', () => {
  const actual = jest.requireActual('../services/api')
  return { ...actual, uploadInspirationImage: jest.fn() }
})

import CaptureScreen from '../app/(tabs)/capture'
import { findBareText } from '../test-utils/noBareText'
import { useInspirationStore } from '../hooks/useInspirations'
// eslint-disable-next-line @typescript-eslint/no-var-requires
const api = require('../services/api') as { uploadInspirationImage: jest.Mock }

const pickMock = ImagePicker.launchImageLibraryAsync as unknown as jest.Mock

describe('CaptureScreen（多选 + 逐张进度）', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    useInspirationStore.setState({ items: [], total: 0, page: 1, loading: false })
    jest.spyOn(Alert, 'alert').mockImplementation(() => {})
  })

  afterEach(() => {
    jest.restoreAllMocks()
  })

  it('初始状态下引导文案与按钮齐全，无裸字符串', () => {
    const utils = render(<CaptureScreen />)

    expect(screen.getByText('相册可多选、也可直接拍')).toBeTruthy()
    expect(screen.getByText('上传入库')).toBeTruthy()
    expect(findBareText(utils.toJSON())).toEqual([])
  })

  it('相册多选后把多张图一起入队，并显示统计', async () => {
    pickMock.mockResolvedValue({
      canceled: false,
      assets: [{ uri: 'file:///a.jpg' }, { uri: 'file:///b.jpg' }, { uri: 'file:///c.jpg' }],
    })

    render(<CaptureScreen />)
    fireEvent.press(screen.getByText('相册多选'))

    await waitFor(() => expect(screen.getByText('共 3 张 · 已上传 0 · 待上传 3')).toBeTruthy())
    expect(screen.getByText('上传 3 张')).toBeTruthy()
  })

  it('上传成功逐张入库，按钮回到「上传入库」', async () => {
    pickMock.mockResolvedValue({
      canceled: false,
      assets: [{ uri: 'file:///a.jpg' }, { uri: 'file:///b.jpg' }],
    })
    api.uploadInspirationImage.mockResolvedValue({ id: 'n1', file_path: 'images/n1.jpg', tags: [] })

    render(<CaptureScreen />)
    fireEvent.press(screen.getByText('相册多选'))
    await waitFor(() => expect(screen.getByText('上传 2 张')).toBeTruthy())

    fireEvent.press(screen.getByText('上传 2 张'))

    await waitFor(() => expect(api.uploadInspirationImage).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.getByText('共 2 张 · 已上传 2 · 待上传 0')).toBeTruthy())
    // 上传成功后应插到 store 列表最前
    expect(useInspirationStore.getState().total).toBe(2)
  })

  it('某张失败时标记失败并保留待上传计数，可重试', async () => {
    pickMock.mockResolvedValue({ canceled: false, assets: [{ uri: 'file:///a.jpg' }] })
    api.uploadInspirationImage.mockRejectedValueOnce(new Error('网络错误'))

    render(<CaptureScreen />)
    fireEvent.press(screen.getByText('相册多选'))
    await waitFor(() => expect(screen.getByText('上传 1 张')).toBeTruthy())

    fireEvent.press(screen.getByText('上传 1 张'))

    // 失败后仍算「待上传」，按钮文案保持可重试
    await waitFor(() => expect(screen.getByText('共 1 张 · 已上传 0 · 待上传 1')).toBeTruthy())
    expect(screen.getByText('上传 1 张')).toBeTruthy()
  })

  it('相册权限被拒绝时提示，而不是静默无反应', async () => {
    ;(ImagePicker.requestMediaLibraryPermissionsAsync as unknown as jest.Mock).mockResolvedValue({
      granted: false,
    })

    render(<CaptureScreen />)
    fireEvent.press(screen.getByText('相册多选'))

    await waitFor(() =>
      expect(Alert.alert).toHaveBeenCalledWith('权限不足', '请在系统设置中允许访问相册'),
    )
    expect(pickMock).not.toHaveBeenCalled()
  })
})
