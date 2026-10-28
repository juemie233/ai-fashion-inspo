/** 分享 Intent：URL 提取 + 图片/链接入库两条链路。 */

import { render, waitFor } from '@testing-library/react-native'
import { Alert } from 'react-native'

const mockShare = {
  hasShareIntent: false,
  shareIntent: {} as { files?: { path: string }[]; text?: string },
  resetShareIntent: jest.fn(),
}

jest.mock('expo-share-intent', () => ({
  __esModule: true,
  useShareIntent: () => mockShare,
}))

jest.mock('../services/api', () => {
  const actual = jest.requireActual('../services/api')
  return {
    ...actual,
    uploadInspirationImage: jest.fn(),
    importInspirationFromUrl: jest.fn(),
  }
})

import ShareIntentBridge from '../components/ShareIntentBridge'
import { extractUrl, looksLikeImageUrl } from '../utils/extractUrl'
import { useInspirationStore } from '../hooks/useInspirations'
// eslint-disable-next-line @typescript-eslint/no-var-requires
const api = require('../services/api') as {
  uploadInspirationImage: jest.Mock
  importInspirationFromUrl: jest.Mock
}

describe('extractUrl', () => {
  it('从抖音分享文案里提取链接，并去掉尾部标点', () => {
    const text = '7.88 复制打开抖音，看看【某人的作品】好看 https://v.douyin.com/abc123/，谢谢'
    expect(extractUrl(text)).toBe('https://v.douyin.com/abc123/')
  })

  it('小红书文案（带话题与换行）也能取到', () => {
    const text = '看看这篇笔记\n#穿搭#  https://www.xiaohongshu.com/explore/1a2b3c?xsec=1 更多'
    expect(extractUrl(text)).toBe('https://www.xiaohongshu.com/explore/1a2b3c?xsec=1')
  })

  it('没有链接 / 空值返回 null', () => {
    expect(extractUrl('只有文字没有链接')).toBeNull()
    expect(extractUrl('')).toBeNull()
    expect(extractUrl(null)).toBeNull()
    expect(extractUrl(undefined)).toBeNull()
  })

  it('looksLikeImageUrl 识别常见图片扩展名', () => {
    expect(looksLikeImageUrl('https://a.com/x.JPG')).toBe(true)
    expect(looksLikeImageUrl('https://a.com/x.webp?x=1')).toBe(true)
    expect(looksLikeImageUrl('https://v.douyin.com/abc/')).toBe(false)
    expect(looksLikeImageUrl(null)).toBe(false)
  })
})

describe('ShareIntentBridge', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    jest.spyOn(Alert, 'alert').mockImplementation(() => {})
    useInspirationStore.setState({ items: [], total: 0, page: 1, loading: false })
    mockShare.hasShareIntent = false
    mockShare.shareIntent = {}
    mockShare.resetShareIntent = jest.fn()
  })

  afterEach(() => {
    jest.restoreAllMocks()
  })

  it('分享图片：走上传并插到列表最前，随后重置 intent', async () => {
    mockShare.hasShareIntent = true
    mockShare.shareIntent = { files: [{ path: 'file:///tmp/shared.jpg' }] }
    api.uploadInspirationImage.mockResolvedValue({ id: 'n1', file_path: 'images/n1.jpg', tags: [] })

    render(<ShareIntentBridge />)

    await waitFor(() =>
      expect(api.uploadInspirationImage).toHaveBeenCalledWith('file:///tmp/shared.jpg'),
    )
    await waitFor(() => expect(useInspirationStore.getState().total).toBe(1))
    await waitFor(() => expect(mockShare.resetShareIntent).toHaveBeenCalled())
    await waitFor(() => expect(Alert.alert).toHaveBeenCalledWith('已入库', expect.any(String)))
  })

  it('分享链接：走 URL 导入而不是上传', async () => {
    mockShare.hasShareIntent = true
    mockShare.shareIntent = { text: '看看这个 https://v.douyin.com/xyz/ 复制打开' }
    api.importInspirationFromUrl.mockResolvedValue({ id: 'n2', file_path: 'images/n2.jpg', tags: [] })

    render(<ShareIntentBridge />)

    await waitFor(() =>
      expect(api.importInspirationFromUrl).toHaveBeenCalledWith('https://v.douyin.com/xyz/'),
    )
    await waitFor(() => expect(api.uploadInspirationImage).not.toHaveBeenCalled())
    await waitFor(() => expect(mockShare.resetShareIntent).toHaveBeenCalled())
  })

  it('分享纯文字（无链接）：提示不支持，且不发请求', async () => {
    mockShare.hasShareIntent = true
    mockShare.shareIntent = { text: '一段没有链接的文字' }

    render(<ShareIntentBridge />)

    await waitFor(() =>
      expect(Alert.alert).toHaveBeenCalledWith('暂不支持', '目前只能分享图片或包含链接的文本'),
    )
    expect(api.importInspirationFromUrl).not.toHaveBeenCalled()
    expect(api.uploadInspirationImage).not.toHaveBeenCalled()
  })

  it('入库失败时给出提示并重置 intent（避免卡住）', async () => {
    mockShare.hasShareIntent = true
    mockShare.shareIntent = { files: [{ path: 'file:///tmp/shared.jpg' }] }
    api.uploadInspirationImage.mockRejectedValue(new Error('network'))

    render(<ShareIntentBridge />)

    await waitFor(() =>
      expect(Alert.alert).toHaveBeenCalledWith('入库失败', '请检查后端地址与网络后重试'),
    )
    await waitFor(() => expect(mockShare.resetShareIntent).toHaveBeenCalled())
  })

  it('没有分享内容时什么都不做', async () => {
    mockShare.hasShareIntent = false

    render(<ShareIntentBridge />)

    expect(api.uploadInspirationImage).not.toHaveBeenCalled()
    expect(api.importInspirationFromUrl).not.toHaveBeenCalled()
    expect(mockShare.resetShareIntent).not.toHaveBeenCalled()
  })
})
