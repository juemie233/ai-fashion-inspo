/** 上传队列持久化（P0-1）：结构规整、落盘取舍、本地文件探测。 */

import AsyncStorage from '@react-native-async-storage/async-storage'
import { Image } from 'expo-image'
import {
  loadUploadQueue,
  markMissingFiles,
  MISSING_FILE_REASON,
  normalizeRestoredQueue,
  persistableQueue,
  probeLocalFile,
  saveUploadQueue,
  type UploadQueueItem,
} from '../utils/uploadQueue'

const STORAGE_KEY = 'upload:queue'
const loadAsync = Image.loadAsync as unknown as jest.Mock

function item(uri: string, over: Partial<UploadQueueItem> = {}): UploadQueueItem {
  return { uri, status: 'pending', progress: 0, addedAt: 1, ...over }
}

beforeEach(async () => {
  jest.clearAllMocks()
  await AsyncStorage.clear()
  loadAsync.mockResolvedValue({ width: 1, height: 1 })
})

describe('normalizeRestoredQueue', () => {
  it('丢弃结构不对的项，uploading 降级为 pending，done 直接丢掉', () => {
    const restored = normalizeRestoredQueue([
      { uri: 'file:///a.jpg', status: 'uploading', progress: 0.6, addedAt: 3 },
      { uri: 'file:///b.jpg', status: 'done', progress: 1, addedAt: 1 },
      { uri: 'file:///c.jpg', status: 'error', error: '网络错误', addedAt: 2 },
      { uri: '', status: 'pending' },
      { status: 'pending' },
      'not-an-object',
      null,
    ])

    expect(restored.map((i) => i.uri)).toEqual(['file:///c.jpg', 'file:///a.jpg'])
    expect(restored[1].status).toBe('pending')
    expect(restored[1].progress).toBe(0)
    expect(restored[0].error).toBe('网络错误')
  })

  it('非数组输入返回空数组', () => {
    expect(normalizeRestoredQueue(null)).toEqual([])
    expect(normalizeRestoredQueue('{}')).toEqual([])
  })
})

describe('persistableQueue', () => {
  it('只保留未完成项，并把进度归零', () => {
    const saved = persistableQueue([
      item('file:///a.jpg', { status: 'pending' }),
      item('file:///b.jpg', { status: 'uploading', progress: 0.4 }),
      item('file:///c.jpg', { status: 'error', error: '失败' }),
      item('file:///d.jpg', { status: 'done', progress: 1 }),
    ])
    expect(saved.map((i) => i.uri)).toEqual(['file:///a.jpg', 'file:///c.jpg'])
    expect(saved.every((i) => i.progress === 0)).toBe(true)
  })
})

describe('读写 AsyncStorage', () => {
  it('保存后能读回未完成项', async () => {
    await saveUploadQueue([item('file:///a.jpg'), item('file:///b.jpg', { status: 'done' })])

    const stored = JSON.parse((await AsyncStorage.getItem(STORAGE_KEY)) ?? '[]') as unknown[]
    expect(stored).toHaveLength(1)

    const restored = await loadUploadQueue()
    expect(restored.map((i) => i.uri)).toEqual(['file:///a.jpg'])
  })

  it('全部完成时删除存储键（不留垃圾）', async () => {
    await saveUploadQueue([item('file:///a.jpg')])
    expect(await AsyncStorage.getItem(STORAGE_KEY)).not.toBeNull()

    await saveUploadQueue([item('file:///a.jpg', { status: 'done' })])
    expect(await AsyncStorage.getItem(STORAGE_KEY)).toBeNull()
  })

  it('存储内容损坏时返回空数组，不抛错', async () => {
    await AsyncStorage.setItem(STORAGE_KEY, '{not-json')
    await expect(loadUploadQueue()).resolves.toEqual([])
  })
})

describe('probeLocalFile', () => {
  it('能解码 → 文件还在', async () => {
    await expect(probeLocalFile('file:///a.jpg')).resolves.toBe(true)
    expect(loadAsync).toHaveBeenCalledWith('file:///a.jpg')
  })

  it('解码失败 → 文件已失效', async () => {
    loadAsync.mockRejectedValueOnce(new Error('not found'))
    await expect(probeLocalFile('file:///gone.jpg')).resolves.toBe(false)
  })

  it('空 URI 直接判为不存在', async () => {
    await expect(probeLocalFile('')).resolves.toBe(false)
    expect(loadAsync).not.toHaveBeenCalled()
  })
})

describe('markMissingFiles', () => {
  it('丢失的项标记为失败并给出中文原因，其余保持原状', async () => {
    loadAsync.mockImplementation(async (uri: string) => {
      if (uri === 'file:///gone.jpg') throw new Error('gone')
      return { width: 1, height: 1 }
    })

    const { items, missing } = await markMissingFiles([
      item('file:///ok.jpg'),
      item('file:///gone.jpg'),
    ])

    expect(missing).toEqual(['file:///gone.jpg'])
    expect(items[0].status).toBe('pending')
    expect(items[1].status).toBe('error')
    expect(items[1].error).toBe(MISSING_FILE_REASON)
  })

  it('已有上传失败原因的项不重复探测（错误与文件无关）', async () => {
    const uploadFailed = item('file:///a.jpg', { status: 'error', error: '网络错误' })
    const { items } = await markMissingFiles([uploadFailed])

    expect(loadAsync).not.toHaveBeenCalled()
    expect(items[0].error).toBe('网络错误')
  })
})
