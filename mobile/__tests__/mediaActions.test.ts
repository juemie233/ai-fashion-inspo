/** 保存到相册 / 系统分享（P0-3）：权限、远程图落盘、失败文案、原生模块缺失降级。 */

jest.mock('expo-file-system', () => ({
  cacheDirectory: 'file:///cache/',
  downloadAsync: jest.fn(),
  deleteAsync: jest.fn(async () => {}),
}))

jest.mock('expo-media-library', () => ({
  requestPermissionsAsync: jest.fn(),
  saveToLibraryAsync: jest.fn(async () => {}),
}))

jest.mock('expo-sharing', () => ({
  isAvailableAsync: jest.fn(async () => true),
  shareAsync: jest.fn(async () => {}),
}))

import * as FileSystem from 'expo-file-system'
import * as MediaLibrary from 'expo-media-library'
import * as Sharing from 'expo-sharing'
import { saveImageToLibrary, shareImage } from '../utils/mediaActions'

const download = FileSystem.downloadAsync as jest.Mock
const removeFile = FileSystem.deleteAsync as jest.Mock
const requestPermission = MediaLibrary.requestPermissionsAsync as jest.Mock
const saveToLibrary = MediaLibrary.saveToLibraryAsync as jest.Mock
const shareAsync = Sharing.shareAsync as jest.Mock
const isAvailable = Sharing.isAvailableAsync as jest.Mock

const REMOTE = 'http://127.0.0.1:18888/api/files/images/a.jpg'

beforeEach(() => {
  jest.clearAllMocks()
  requestPermission.mockResolvedValue({ granted: true })
  isAvailable.mockResolvedValue(true)
  download.mockResolvedValue({ status: 200, uri: 'file:///cache/share-1.jpg' })
  removeFile.mockResolvedValue(undefined)
})

describe('saveImageToLibrary', () => {
  it('远程图：下载到缓存 → 保存 → 清理临时文件', async () => {
    const result = await saveImageToLibrary(REMOTE)

    expect(result).toEqual({ ok: true, message: '已保存到相册' })
    expect(download).toHaveBeenCalledWith(REMOTE, expect.stringMatching(/share-\d+\.jpg$/))
    expect(saveToLibrary).toHaveBeenCalledWith('file:///cache/share-1.jpg')
    expect(removeFile).toHaveBeenCalledWith(expect.stringContaining('share-'), {
      idempotent: true,
    })
  })

  it('本地 file:// 直接保存，不重复下载也不删原文件', async () => {
    await saveImageToLibrary('file:///local/a.webp')

    expect(download).not.toHaveBeenCalled()
    expect(saveToLibrary).toHaveBeenCalledWith('file:///local/a.webp')
    expect(removeFile).not.toHaveBeenCalled()
  })

  it('没有相册权限时给出可操作提示，不下载', async () => {
    requestPermission.mockResolvedValue({ granted: false })

    const result = await saveImageToLibrary(REMOTE)

    expect(result.ok).toBe(false)
    expect(result.message).toContain('相册权限')
    expect(download).not.toHaveBeenCalled()
  })

  it('下载失败（非 200）时报错并清掉半截文件', async () => {
    download.mockResolvedValue({ status: 404, uri: 'file:///cache/share-1.jpg' })

    const result = await saveImageToLibrary(REMOTE)

    expect(result.ok).toBe(false)
    expect(result.message).toContain('404')
    expect(saveToLibrary).not.toHaveBeenCalled()
    expect(removeFile).toHaveBeenCalled()
  })

  it('保存抛错时转成中文提示，不把异常抛给页面', async () => {
    saveToLibrary.mockRejectedValueOnce(new Error('native boom'))

    const result = await saveImageToLibrary('file:///local/a.jpg')

    expect(result).toEqual({ ok: false, message: '保存失败，请稍后重试' })
  })
})

describe('shareImage', () => {
  it('可用时打开分享面板并清理临时文件', async () => {
    const result = await shareImage(REMOTE)

    expect(result.ok).toBe(true)
    expect(shareAsync).toHaveBeenCalledWith('file:///cache/share-1.jpg', expect.objectContaining({
      mimeType: 'image/jpeg',
    }))
    expect(removeFile).toHaveBeenCalled()
  })

  it('设备不支持分享时直接提示', async () => {
    isAvailable.mockResolvedValue(false)

    const result = await shareImage(REMOTE)

    expect(result).toEqual({ ok: false, message: '当前设备不支持系统分享' })
    expect(shareAsync).not.toHaveBeenCalled()
  })
})

describe('原生模块缺失（旧 APK 未重建）', () => {
  it('给出「请更新 App」而不是崩溃', async () => {
    jest.resetModules()
    jest.doMock('expo-sharing', () => {
      throw new Error('native module missing')
    })

    let actions: typeof import('../utils/mediaActions') | null = null
    jest.isolateModules(() => {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      actions = require('../utils/mediaActions') as typeof import('../utils/mediaActions')
    })

    const result = await actions!.shareImage(REMOTE)
    expect(result).toEqual({ ok: false, message: '当前版本不支持该功能，请更新 App 后重试' })
  })
})
