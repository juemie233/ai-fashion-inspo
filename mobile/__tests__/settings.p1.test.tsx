/** 设置页（P1）：API Key、地址复制/粘贴、诊断与缓存清理。 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react-native'
import AsyncStorage from '@react-native-async-storage/async-storage'

jest.mock('expo-router', () => ({
  useRouter: () => ({ push: jest.fn(), back: jest.fn(), replace: jest.fn() }),
  useLocalSearchParams: () => ({}),
}))

jest.mock('../services/api', () => ({
  getApiBaseUrl: jest.fn(() => 'http://192.168.31.142:18888'),
  getApiKey: jest.fn(() => ''),
  loadApiBaseUrl: jest.fn(async () => 'http://192.168.31.142:18888'),
  loadApiKey: jest.fn(async () => ''),
  saveApiBaseUrl: jest.fn(async () => {}),
  saveApiKey: jest.fn(async () => {}),
  testApiConnection: jest.fn(async () => ({ ok: true, message: '连接成功' })),
}))

jest.mock('../utils/clipboard', () => ({
  copyText: jest.fn(async () => ({ ok: true, message: '已复制到剪贴板' })),
  readClipboard: jest.fn(async () => 'http://10.0.0.9:18888'),
}))

import SettingsScreen from '../app/(tabs)/settings'
import { useInspirationStore } from '../hooks/useInspirations'
import { saveGalleryCache, savePopularTags } from '../utils/offlineCache'
import * as clipboard from '../utils/clipboard'
// eslint-disable-next-line @typescript-eslint/no-var-requires
const api = require('../services/api') as {
  getApiKey: jest.Mock
  loadApiKey: jest.Mock
  saveApiKey: jest.Mock
  saveApiBaseUrl: jest.Mock
  testApiConnection: jest.Mock
}

beforeEach(async () => {
  jest.clearAllMocks()
  await AsyncStorage.clear()
  api.getApiKey.mockReturnValue('')
  api.loadApiKey.mockResolvedValue('')
  useInspirationStore.setState({ fetchInspirations: jest.fn(async () => {}) } as never)
})

describe('API Key', () => {
  it('未设置时提示「未设置」，保存后写入 Key', async () => {
    render(<SettingsScreen />)
    await waitFor(() => expect(screen.getByText('未设置（后端未开鉴权时正常）')).toBeTruthy())

    fireEvent.changeText(screen.getByTestId('api-key-input'), 'secret-key')
    fireEvent.press(screen.getByTestId('save-connection'))

    await waitFor(() => expect(api.saveApiKey).toHaveBeenCalledWith('secret-key'))
    expect(api.saveApiBaseUrl).toHaveBeenCalledWith('http://192.168.31.142:18888')
  })

  it('已设置时用掩码回显；保存掩码不会覆盖已保存的 Key', async () => {
    api.getApiKey.mockReturnValue('saved-key')
    api.loadApiKey.mockResolvedValue('saved-key')
    render(<SettingsScreen />)

    await waitFor(() => expect(screen.getByTestId('api-key-input').props.value).toBe('••••••••'))
    expect(screen.getByText('已设置')).toBeTruthy()

    fireEvent.press(screen.getByTestId('save-connection'))
    await waitFor(() => expect(api.saveApiBaseUrl).toHaveBeenCalled())
    // 掩码 == 沿用原 Key，不再调 saveApiKey
    expect(api.saveApiKey).not.toHaveBeenCalled()
  })

  it('测试连接带上 Key（掩码时用已保存的 Key）', async () => {
    api.getApiKey.mockReturnValue('saved-key')
    api.loadApiKey.mockResolvedValue('saved-key')
    render(<SettingsScreen />)
    await waitFor(() => expect(screen.getByTestId('api-key-input').props.value).toBe('••••••••'))

    fireEvent.press(screen.getByTestId('test-connection'))

    await waitFor(() =>
      expect(api.testApiConnection).toHaveBeenCalledWith('http://192.168.31.142:18888', 'saved-key'),
    )
    await waitFor(() => expect(screen.getByText('连接成功')).toBeTruthy())
  })

  it('Key 无效时展示后端鉴权失败原因', async () => {
    api.testApiConnection.mockResolvedValue({ ok: false, message: 'API Key 无效或缺失（后端已开启鉴权）' })
    render(<SettingsScreen />)
    await waitFor(() => expect(screen.getByTestId('api-url-input').props.value).toBeTruthy())

    fireEvent.press(screen.getByTestId('test-connection'))

    await waitFor(() =>
      expect(screen.getByText('API Key 无效或缺失（后端已开启鉴权）')).toBeTruthy(),
    )
  })
})

describe('地址复制 / 粘贴', () => {
  it('复制当前地址', async () => {
    render(<SettingsScreen />)
    await waitFor(() => expect(screen.getByTestId('api-url-input').props.value).toBeTruthy())

    fireEvent.press(screen.getByTestId('copy-address'))

    await waitFor(() =>
      expect(clipboard.copyText).toHaveBeenCalledWith('http://192.168.31.142:18888'),
    )
    await waitFor(() => expect(screen.getByText('已复制到剪贴板')).toBeTruthy())
  })

  it('从剪贴板粘贴（只取第一行）', async () => {
    ;(clipboard.readClipboard as jest.Mock).mockResolvedValue('http://10.0.0.9:18888\n第二行说明')
    render(<SettingsScreen />)

    fireEvent.press(screen.getByTestId('paste-address'))

    await waitFor(() =>
      expect(screen.getByTestId('api-url-input').props.value).toBe('http://10.0.0.9:18888'),
    )
  })

  it('剪贴板为空时给出提示，不清空输入框', async () => {
    ;(clipboard.readClipboard as jest.Mock).mockResolvedValue('')
    render(<SettingsScreen />)
    await waitFor(() => expect(screen.getByTestId('api-url-input').props.value).toBeTruthy())

    fireEvent.press(screen.getByTestId('paste-address'))

    await waitFor(() => expect(screen.getByText('剪贴板里没有可用文本')).toBeTruthy())
    expect(screen.getByTestId('api-url-input').props.value).toBe('http://192.168.31.142:18888')
  })
})

describe('诊断与缓存', () => {
  it('展示版本与「暂无缓存」，缓存存在时展示条数', async () => {
    const { unmount } = render(<SettingsScreen />)
    await waitFor(() => expect(screen.getByText('暂无（联网浏览一次素材库后生成）')).toBeTruthy())
    expect(screen.getByText(/^v0\.\d+\.\d+（build \d+）$/)).toBeTruthy()
    unmount()

    await saveGalleryCache(
      [
        { id: 'a', file_path: 'x', source_type: 'douyin', media_type: 'image', tags: [] },
      ] as never,
      1,
    )
    await savePopularTags(['法式'])
    render(<SettingsScreen />)
    await waitFor(() => expect(screen.getByText(/素材库 1 条 ·/)).toBeTruthy())
  })

  it('清理缓存调用离线缓存清理并给反馈', async () => {
    await saveGalleryCache(
      [
        { id: 'a', file_path: 'x', source_type: 'douyin', media_type: 'image', tags: [] },
      ] as never,
      1,
    )
    render(<SettingsScreen />)
    await waitFor(() => expect(screen.getByText(/素材库 1 条/)).toBeTruthy())

    fireEvent.press(screen.getByTestId('clear-cache'))

    await waitFor(() =>
      expect(screen.getByText('缓存已清理（图片会在下次浏览时重新下载）')).toBeTruthy(),
    )
    await waitFor(() => expect(screen.getByText('暂无（联网浏览一次素材库后生成）')).toBeTruthy())
  })
})
