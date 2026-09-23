/**
 * useFaceServiceStatus 单测：共享探测、离线判定、未知态不误伤。
 *
 * 模块级缓存状态跨用例共享，因此每个用例用 resetModules + 动态 import
 * 取一份全新实例（连同它依赖的 mock api）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { FaceServiceStatus } from '@/api/faceScan'

vi.mock('@/api/faceScan', () => ({
  fetchFaceServiceStatus: vi.fn(),
}))

const OFFLINE: FaceServiceStatus = {
  enabled: true,
  reachable: false,
  url: 'http://127.0.0.1:18889',
  message: '人脸识别子服务未启动或不可达：连接被拒绝',
}

const ONLINE: FaceServiceStatus = {
  enabled: true,
  reachable: true,
  url: 'http://127.0.0.1:18889',
  message: '人脸识别子服务正常',
  detail: { registered: 3 },
}

/** 取一份全新的 composable 实例 + 与之共享的 mock api */
async function freshComposable() {
  vi.resetModules()
  const { useFaceServiceStatus } = await import('../useFaceServiceStatus')
  const api = await import('@/api/faceScan')
  return { ...useFaceServiceStatus(), api }
}

describe('useFaceServiceStatus', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('未知态不误伤：尚未探测时 offline 为 false', async () => {
    const { offline } = await freshComposable()
    expect(offline.value).toBe(false)
  })

  it('探测到不可达：offline 为 true 且带回中文原因', async () => {
    const { offline, status, load, api } = await freshComposable()
    vi.mocked(api.fetchFaceServiceStatus).mockResolvedValue(OFFLINE)
    await load()
    expect(offline.value).toBe(true)
    expect(status.value?.message).toContain('未启动')
  })

  it('探测到正常：offline 为 false 并可读子服务详情', async () => {
    const { offline, status, load, api } = await freshComposable()
    vi.mocked(api.fetchFaceServiceStatus).mockResolvedValue(ONLINE)
    await load()
    expect(offline.value).toBe(false)
    expect(status.value?.detail).toEqual({ registered: 3 })
  })

  it('探测请求本身失败：保持未知态，不把人脸功能整体锁死', async () => {
    const { offline, status, load, api } = await freshComposable()
    vi.mocked(api.fetchFaceServiceStatus).mockRejectedValue(new Error('network'))
    await load()
    expect(status.value).toBeNull()
    expect(offline.value).toBe(false)
  })

  it('并发调用复用同一次探测（只发一个请求）', async () => {
    const { load, api } = await freshComposable()
    vi.mocked(api.fetchFaceServiceStatus).mockResolvedValue(ONLINE)
    await Promise.all([load(), load(), load()])
    expect(api.fetchFaceServiceStatus).toHaveBeenCalledTimes(1)
  })
})
