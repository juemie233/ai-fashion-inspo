/** 全屏看图（P0-3）：渲染、关闭、保存/分享按钮的成功与失败反馈。 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react-native'
import { Alert } from 'react-native'

jest.mock('../utils/mediaActions', () => ({
  saveImageToLibrary: jest.fn(),
  shareImage: jest.fn(),
}))

import ImageViewer from '../components/ImageViewer'
import { findBareText } from '../test-utils/noBareText'
// eslint-disable-next-line @typescript-eslint/no-var-requires
const media = require('../utils/mediaActions') as {
  saveImageToLibrary: jest.Mock
  shareImage: jest.Mock
}

const URI = 'http://127.0.0.1:18888/api/files/images/a.jpg'

beforeEach(() => {
  jest.clearAllMocks()
  jest.spyOn(Alert, 'alert').mockImplementation(() => {})
})

afterEach(() => {
  jest.restoreAllMocks()
})

describe('ImageViewer', () => {
  it('渲染图片、提示与操作按钮，且无裸字符串', () => {
    const utils = render(<ImageViewer visible uri={URI} onClose={jest.fn()} />)

    expect(screen.getByTestId('image-viewer-image')).toBeTruthy()
    expect(screen.getByText('双指缩放 · 双击放大 · 点按隐藏按钮')).toBeTruthy()
    expect(screen.getByTestId('viewer-save')).toBeTruthy()
    expect(screen.getByTestId('viewer-share')).toBeTruthy()
    expect(findBareText(utils.toJSON())).toEqual([])
  })

  it('关闭按钮回调 onClose', () => {
    const onClose = jest.fn()
    render(<ImageViewer visible uri={URI} onClose={onClose} />)
    fireEvent.press(screen.getByLabelText('关闭'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('保存成功提示「已保存 / 已保存到相册」', async () => {
    media.saveImageToLibrary.mockResolvedValue({ ok: true, message: '已保存到相册' })
    render(<ImageViewer visible uri={URI} onClose={jest.fn()} />)

    fireEvent.press(screen.getByTestId('viewer-save'))

    await waitFor(() =>
      expect(Alert.alert).toHaveBeenCalledWith('已保存', '已保存到相册'),
    )
    expect(media.saveImageToLibrary).toHaveBeenCalledWith(URI)
  })

  it('保存失败透传可读原因（权限/网络）', async () => {
    media.saveImageToLibrary.mockResolvedValue({
      ok: false,
      message: '没有相册权限，请在系统设置中允许后重试',
    })
    render(<ImageViewer visible uri={URI} onClose={jest.fn()} />)

    fireEvent.press(screen.getByTestId('viewer-save'))

    await waitFor(() =>
      expect(Alert.alert).toHaveBeenCalledWith(
        '保存失败',
        '没有相册权限，请在系统设置中允许后重试',
      ),
    )
  })

  it('分享调用 shareImage 并提示结果', async () => {
    media.shareImage.mockResolvedValue({ ok: true, message: '已打开分享面板' })
    render(<ImageViewer visible uri={URI} onClose={jest.fn()} />)

    await act(async () => {
      fireEvent.press(screen.getByTestId('viewer-share'))
    })

    await waitFor(() => expect(media.shareImage).toHaveBeenCalledWith(URI))
    expect(Alert.alert).toHaveBeenCalledWith('分享', '已打开分享面板')
  })

  it('分享不可用时提示失败原因', async () => {
    media.shareImage.mockResolvedValue({ ok: false, message: '当前设备不支持系统分享' })
    render(<ImageViewer visible uri={URI} onClose={jest.fn()} />)

    await act(async () => {
      fireEvent.press(screen.getByTestId('viewer-share'))
    })

    await waitFor(() =>
      expect(Alert.alert).toHaveBeenCalledWith('分享失败', '当前设备不支持系统分享'),
    )
  })
})
