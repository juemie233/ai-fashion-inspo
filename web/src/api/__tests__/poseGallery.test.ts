/** 姿势图库 API：参数透传与路径契约（顺带让 ts-prune 看到这两个导出确实被使用）。 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  default: { get: mocks.get },
}))

import { fetchPoseGalleryMeta, fetchPoses } from '../poseGallery'

describe('poseGallery API', () => {
  beforeEach(() => {
    mocks.get.mockReset()
  })

  it('fetchPoseGalleryMeta 打 /pose-gallery/meta', async () => {
    mocks.get.mockResolvedValue({
      data: { total: 0, systems: [], categories: [], angles: [], note: '', source_url: '' },
    })

    const data = await fetchPoseGalleryMeta()

    expect(mocks.get).toHaveBeenCalledWith('/pose-gallery/meta')
    expect(data.total).toBe(0)
  })

  it('fetchPoses 把筛选与分页参数原样透传', async () => {
    mocks.get.mockResolvedValue({ data: { items: [], total: 0, page: 2, size: 40, note: '' } })

    await fetchPoses({ q: '站姿', system: '上半身单人', angle: '俯视', page: 2, size: 40 })

    expect(mocks.get).toHaveBeenCalledWith('/pose-gallery', {
      params: { q: '站姿', system: '上半身单人', angle: '俯视', page: 2, size: 40 },
    })
  })
})
