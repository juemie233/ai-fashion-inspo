/** 采集页上传队列持久化（P0-1）：冷启动恢复、失效文件、继续上传、新选图落盘。 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react-native'
import { Alert } from 'react-native'
import AsyncStorage from '@react-native-async-storage/async-storage'
import * as ImagePicker from 'expo-image-picker'
import { Image } from 'expo-image'

jest.mock('../services/api', () => {
  const actual = jest.requireActual('../services/api')
  return { ...actual, uploadInspirationImage: jest.fn() }
})

import CaptureScreen from '../app/(tabs)/capture'
import { useInspirationStore } from '../hooks/useInspirations'
import { MISSING_FILE_REASON } from '../utils/uploadQueue'
// eslint-disable-next-line @typescript-eslint/no-var-requires
const api = require('../services/api') as { uploadInspirationImage: jest.Mock }

const STORAGE_KEY = 'upload:queue'
const pickMock = ImagePicker.launchImageLibraryAsync as unknown as jest.Mock
const loadAsync = (Image as unknown as { loadAsync: jest.Mock }).loadAsync

/** 预置一份「上次没传完」的队列 */
async function seedQueue(items: unknown[]) {
  await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(items))
}

beforeEach(async () => {
  jest.clearAllMocks()
  await AsyncStorage.clear()
  loadAsync.mockResolvedValue({ width: 1, height: 1 })
  useInspirationStore.setState({ items: [], total: 0, page: 1, loading: false })
  jest.spyOn(Alert, 'alert').mockImplementation(() => {})
})

afterEach(() => {
  jest.restoreAllMocks()
})

describe('冷启动恢复', () => {
  it('恢复未完成队列并提示，uploading 降级为待传', async () => {
    await seedQueue([
      { uri: 'file:///a.jpg', status: 'uploading', progress: 0.5, addedAt: 1 },
      { uri: 'file:///b.jpg', status: 'error', error: '网络错误', addedAt: 2 },
    ])

    render(<CaptureScreen />)

    await waitFor(() => expect(screen.getByText('已恢复 2 张未完成的上传，点「上传」继续')).toBeTruthy())
    expect(screen.getByText('已选 2 · 已完成 0 · 失败 1')).toBeTruthy()
    // 恢复后立即规整存储：uploading → pending
    await waitFor(async () => {
      const stored = JSON.parse((await AsyncStorage.getItem(STORAGE_KEY)) ?? '[]') as {
        status: string
      }[]
      expect(stored.every((i) => i.status !== 'uploading')).toBe(true)
    })
  })

  it('本地文件已失效的项标记失败并给出可读原因', async () => {
    loadAsync.mockImplementation(async (uri: string) => {
      if (uri === 'file:///gone.jpg') throw new Error('gone')
      return { width: 1, height: 1 }
    })
    await seedQueue([
      { uri: 'file:///ok.jpg', status: 'pending', addedAt: 1 },
      { uri: 'file:///gone.jpg', status: 'pending', addedAt: 2 },
    ])

    render(<CaptureScreen />)

    await waitFor(() => expect(screen.getByText('已选 2 · 已完成 0 · 失败 1')).toBeTruthy())
    // 点失败项看原因
    fireEvent.press(screen.getAllByLabelText('查看失败原因')[0])
    await waitFor(() => expect(screen.getByText(MISSING_FILE_REASON)).toBeTruthy())
  })

  it('恢复后点上传能继续入库，全部完成后清空存储', async () => {
    api.uploadInspirationImage.mockResolvedValue({ id: 'n1', file_path: 'images/n1.jpg', tags: [] })
    await seedQueue([{ uri: 'file:///a.jpg', status: 'pending', addedAt: 1 }])

    render(<CaptureScreen />)
    await waitFor(() => expect(screen.getByText('上传 1 张')).toBeTruthy())

    fireEvent.press(screen.getByTestId('upload-button'))

    await waitFor(() => expect(api.uploadInspirationImage).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(screen.getByText('已选 1 · 已完成 1 · 失败 0')).toBeTruthy())
    // 已完成的项不再落盘
    await waitFor(async () => expect(await AsyncStorage.getItem(STORAGE_KEY)).toBeNull())
  })
})

describe('新选图落盘', () => {
  it('相册选图后把待传项写入存储（杀进程可恢复）', async () => {
    pickMock.mockResolvedValue({
      canceled: false,
      assets: [{ uri: 'file:///x.jpg' }, { uri: 'file:///y.jpg' }],
    })

    render(<CaptureScreen />)
    fireEvent.press(screen.getByText('相册多选'))

    await waitFor(() => expect(screen.getByText('已选 2 · 已完成 0 · 失败 0')).toBeTruthy())
    await waitFor(async () => {
      const stored = JSON.parse((await AsyncStorage.getItem(STORAGE_KEY)) ?? '[]') as {
        uri: string
        status: string
      }[]
      expect(stored.map((i) => i.uri)).toEqual(['file:///x.jpg', 'file:///y.jpg'])
      expect(stored.every((i) => i.status === 'pending')).toBe(true)
    })
  })

  it('移除某张后存储同步移除', async () => {
    pickMock.mockResolvedValue({ canceled: false, assets: [{ uri: 'file:///x.jpg' }] })

    render(<CaptureScreen />)
    fireEvent.press(screen.getByText('相册多选'))
    await waitFor(() => expect(screen.getByText('已选 1 · 已完成 0 · 失败 0')).toBeTruthy())

    fireEvent.press(screen.getByLabelText('移除这张'))
    await waitFor(() => expect(screen.getByText('相册可多选、也可直接拍')).toBeTruthy())
    await waitFor(async () => expect(await AsyncStorage.getItem(STORAGE_KEY)).toBeNull())
  })
})
