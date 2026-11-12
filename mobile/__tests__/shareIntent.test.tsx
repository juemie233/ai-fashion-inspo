/** 分享 Intent：URL 提取 + 图片/链接入库两条链路 + P1 多图/判重。 */

import { render, waitFor } from '@testing-library/react-native'
import { Alert } from 'react-native'

const mockShare = {
  hasShareIntent: false,
  shareIntent: {} as {
    files?: { path: string }[]
    text?: string
    webUrl?: string | null
    meta?: { title?: string }
  },
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

/** 取最后一次 Alert 的标题与正文 */
function lastAlert(): [string, string] {
  const calls = (Alert.alert as jest.Mock).mock.calls
  const call = calls[calls.length - 1] as [string, string]
  return [call[0], call[1]]
}

const ok = (id: string) => ({ id, file_path: `images/${id}.jpg`, tags: [] })

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
    api.uploadInspirationImage.mockResolvedValue(ok('n1'))

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
    api.importInspirationFromUrl.mockResolvedValue(ok('n2'))

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

    await waitFor(() => expect(Alert.alert).toHaveBeenCalled())
    expect(lastAlert()).toEqual(['入库失败', '1 张失败：请检查后端地址与网络后重试'])
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

describe('ShareIntentBridge · 多图分享（P1）', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    jest.spyOn(Alert, 'alert').mockImplementation(() => {})
    useInspirationStore.setState({ items: [], total: 0, page: 1, loading: false })
    mockShare.hasShareIntent = true
    mockShare.resetShareIntent = jest.fn()
  })

  afterEach(() => {
    jest.restoreAllMocks()
  })

  it('一次分享 3 张：逐张入库，只弹一条汇总', async () => {
    mockShare.shareIntent = {
      files: [
        { path: 'file:///tmp/a.jpg' },
        { path: 'file:///tmp/b.jpg' },
        { path: 'file:///tmp/c.jpg' },
      ],
    }
    api.uploadInspirationImage
      .mockResolvedValueOnce(ok('n1'))
      .mockResolvedValueOnce(ok('n2'))
      .mockResolvedValueOnce(ok('n3'))

    render(<ShareIntentBridge />)

    await waitFor(() => expect(api.uploadInspirationImage).toHaveBeenCalledTimes(3))
    expect(useInspirationStore.getState().total).toBe(3)
    await waitFor(() => expect(Alert.alert).toHaveBeenCalled())
    expect(lastAlert()).toEqual(['已入库', '已入库 3 张，AI 将自动分析标签'])
    expect((Alert.alert as jest.Mock).mock.calls).toHaveLength(1)
  })

  it('内容重复（409）算「已在库中」而不是失败', async () => {
    mockShare.shareIntent = {
      files: [{ path: 'file:///tmp/a.jpg' }, { path: 'file:///tmp/dup.jpg' }],
    }
    api.uploadInspirationImage.mockResolvedValueOnce(ok('n1')).mockRejectedValueOnce({
      response: { status: 409, data: { detail: '该素材已存在（内容重复）' } },
    })

    render(<ShareIntentBridge />)

    await waitFor(() => expect(Alert.alert).toHaveBeenCalled())
    const [title, message] = lastAlert()
    expect(title).toBe('分享入库结果')
    expect(message).toContain('已入库 1 张')
    expect(message).toContain('1 张已在库中（内容重复），已跳过')
  })

  it('全部重复时标题是「已在库中」，不出现失败字样', async () => {
    mockShare.shareIntent = { files: [{ path: 'file:///tmp/dup.jpg' }] }
    api.uploadInspirationImage.mockRejectedValue({
      response: { status: 409, data: { detail: '该素材已存在（内容重复）' } },
    })

    render(<ShareIntentBridge />)

    await waitFor(() => expect(Alert.alert).toHaveBeenCalled())
    const [title, message] = lastAlert()
    expect(title).toBe('已在库中')
    expect(message).toContain('已在库中（内容重复）')
    expect(message).not.toContain('失败')
  })

  it('部分失败时汇总里带第一条失败原因', async () => {
    mockShare.shareIntent = {
      files: [{ path: 'file:///tmp/a.jpg' }, { path: 'file:///tmp/b.jpg' }],
    }
    api.uploadInspirationImage.mockResolvedValueOnce(ok('n1')).mockRejectedValueOnce({
      response: { status: 400, data: { detail: '不支持的文件类型: video/webm' } },
    })

    render(<ShareIntentBridge />)

    await waitFor(() => expect(Alert.alert).toHaveBeenCalled())
    const [title, message] = lastAlert()
    expect(title).toBe('分享入库结果')
    expect(message).toContain('已入库 1 张')
    expect(message).toContain('1 张失败：不支持的文件类型: video/webm')
  })

  it('超过单次上限（12 张）时只处理前 12 张并说明剩余数量', async () => {
    mockShare.shareIntent = {
      files: Array.from({ length: 14 }, (_, i) => ({ path: `file:///tmp/${i}.jpg` })),
    }
    api.uploadInspirationImage.mockResolvedValue(ok('n'))

    render(<ShareIntentBridge />)

    await waitFor(() => expect(api.uploadInspirationImage).toHaveBeenCalledTimes(12))
    await waitFor(() => expect(Alert.alert).toHaveBeenCalled())
    const [, message] = lastAlert()
    expect(message).toContain('另有 2 张超出单次上限（12），未处理')
  })

  it('链接已在库中时明确提示，不伪装成成功', async () => {
    mockShare.shareIntent = { text: '看看这个 https://v.douyin.com/xyz/ 复制打开' }
    api.importInspirationFromUrl.mockRejectedValue({
      response: { status: 409, data: { detail: '该素材已存在（内容重复）' } },
    })

    render(<ShareIntentBridge />)

    await waitFor(() => expect(Alert.alert).toHaveBeenCalled())
    const [title, message] = lastAlert()
    expect(title).toBe('已在库中')
    expect(message).toContain('1 个链接已在库中')
  })

  it('链接导入成功时把文案标题带进提示', async () => {
    mockShare.shareIntent = {
      text: '7.88 复制打开抖音，看看【夏日穿搭】这套真好看 https://v.douyin.com/abc/',
    }
    api.importInspirationFromUrl.mockResolvedValue(ok('n9'))

    render(<ShareIntentBridge />)

    await waitFor(() => expect(Alert.alert).toHaveBeenCalled())
    const [title, message] = lastAlert()
    expect(title).toBe('已导入')
    expect(message).toContain('夏日穿搭')
    expect(message).toContain('链接')
  })

  it('webUrl 也能当链接导入（iOS 形态的分享载荷）', async () => {
    mockShare.shareIntent = { webUrl: 'https://example.com/p/1', files: [] }
    api.importInspirationFromUrl.mockResolvedValue(ok('n10'))

    render(<ShareIntentBridge />)

    await waitFor(() =>
      expect(api.importInspirationFromUrl).toHaveBeenCalledWith('https://example.com/p/1'),
    )
  })
})
