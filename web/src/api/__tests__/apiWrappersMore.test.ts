/**
 * API 封装补测（批次 B 剩余）：personModels / poseGallery / aiModelStats。
 *
 * - personModels：模特公共能力（复用 createPersonApi 工厂）、人脸注册/状态、CSV 导入、
 *   博主资料补全任务、照片组 CRUD 与照片上传（FormData + 进度/取消信号）；
 * - poseGallery：图库元信息/分页列表/随机/单条/提示词反推，id 必须 URL 编码；
 * - aiModelStats：`models` 缺失或形状异常时回退空数组（调用方回退旧速率估算），
 *   overallAvgMs 优先「（全部模型汇总）」行，否则按次数加权自算。
 *
 * 这些封装没有页面级测试兜底，路径/参数写错会直接 404/422，所以逐条锁死。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  default: {
    get: mocks.get,
    post: mocks.post,
    put: mocks.put,
    patch: mocks.patch,
    delete: mocks.delete,
  },
}))

import * as personModels from '@/api/personModels'
import * as poseGallery from '@/api/poseGallery'
import { fetchModelStats, overallAvgMs } from '@/api/aiModelStats'
import type { ModelStatItem } from '@/api/aiModelStats'

type AnyMock = { mock: { calls: unknown[][] } }

function lastArgs(mockFn: AnyMock): unknown[] {
  return mockFn.mock.calls.at(-1) ?? []
}

function lastPostFormData(): FormData {
  return lastArgs(mocks.post)[1] as FormData
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.get.mockResolvedValue({ data: {} })
  mocks.post.mockResolvedValue({ data: {} })
  mocks.put.mockResolvedValue({ data: {} })
  mocks.patch.mockResolvedValue({ data: {} })
  mocks.delete.mockResolvedValue({ data: {} })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('personModels 模特公共能力', () => {
  it('modelsApi 复用 createPersonApi：公共方法走 /models 前缀', async () => {
    await personModels.modelsApi.fetchList({ page: 2 })
    expect(mocks.get).toHaveBeenLastCalledWith('/models', { params: { page: 2 } })

    await personModels.modelsApi.fetchDetail(3)
    expect(mocks.get).toHaveBeenLastCalledWith('/models/3')

    await personModels.modelsApi.remove(3)
    expect(mocks.delete).toHaveBeenLastCalledWith('/models/3')
  })

  it('模特人脸注册：FormData 的 top_k 默认 5、可覆盖；显式 multipart 头', async () => {
    await personModels.modelsApi.registerFace(3)
    let form = lastPostFormData()
    expect(form.get('top_k')).toBe('5')
    expect(lastArgs(mocks.post)[0]).toBe('/models/3/face')
    expect(lastArgs(mocks.post)[2]).toEqual({
      headers: { 'Content-Type': 'multipart/form-data' },
    })

    await personModels.modelsApi.registerFace(3, 8)
    form = lastPostFormData()
    expect(form.get('top_k')).toBe('8')

    await personModels.modelsApi.fetchFaceStatus(3)
    expect(mocks.get).toHaveBeenLastCalledWith('/models/3/face')
  })
})

describe('personModels 博主导入与资料补全', () => {
  it('CSV 导入：POST /bloggers/import-csv，FormData 带 file', async () => {
    const file = new File(['id,name'], 'bloggers.csv', { type: 'text/csv' })

    await personModels.importBloggersCsv(file)

    const form = lastPostFormData()
    expect(form.get('file')).toBeInstanceOf(File)
    expect(lastArgs(mocks.post)[0]).toBe('/bloggers/import-csv')
    expect(lastArgs(mocks.post)[2]).toEqual({
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  })

  it('缺失资料列表 / 创建补全任务（ids 缺省=全部）', async () => {
    await personModels.fetchMissingProfiles()
    expect(mocks.get).toHaveBeenLastCalledWith('/bloggers/missing-profile')

    await personModels.enrichMissingProfiles()
    expect(lastArgs(mocks.post)).toEqual([
      '/bloggers/enrich-missing-profile',
      {
        blogger_ids: undefined,
      },
    ])
    await personModels.enrichMissingProfiles([1, 2])
    expect(mocks.post).toHaveBeenLastCalledWith('/bloggers/enrich-missing-profile', {
      blogger_ids: [1, 2],
    })
  })

  it('头像批量补全：overwrite 默认 false，显式传 true 时透传', async () => {
    await personModels.fillMissingAvatars()
    expect(mocks.post).toHaveBeenLastCalledWith('/bloggers/avatars/fill-missing', {
      blogger_ids: undefined,
      overwrite: false,
    })

    await personModels.fillMissingAvatars([3], true)
    expect(mocks.post).toHaveBeenLastCalledWith('/bloggers/avatars/fill-missing', {
      blogger_ids: [3],
      overwrite: true,
    })
  })

  it('跳过补全记录：查询 / 跳过（reason 可空）/ 解除跳过', async () => {
    await personModels.fetchEnrichSkips()
    expect(mocks.get).toHaveBeenLastCalledWith('/bloggers/enrich-skips')

    await personModels.skipEnrichBloggers([5])
    expect(mocks.post).toHaveBeenLastCalledWith('/bloggers/enrich-skip', {
      blogger_ids: [5],
      reason: undefined,
    })
    await personModels.skipEnrichBloggers([5], '账号已注销')
    expect(mocks.post).toHaveBeenLastCalledWith('/bloggers/enrich-skip', {
      blogger_ids: [5],
      reason: '账号已注销',
    })

    await personModels.unskipEnrichBloggers([5, 6])
    expect(mocks.post).toHaveBeenLastCalledWith('/bloggers/enrich-unskip', {
      blogger_ids: [5, 6],
    })
  })
})

describe('personModels 模特照片组', () => {
  it('照片组列表/详情：默认分页参数与自定义', async () => {
    await personModels.fetchModelPhotoSets(7)
    expect(mocks.get).toHaveBeenLastCalledWith('/models/7/photo-sets', {
      params: { page: 1, size: 50 },
    })
    await personModels.fetchModelPhotoSets(7, 2, 20)
    expect(mocks.get).toHaveBeenLastCalledWith('/models/7/photo-sets', {
      params: { page: 2, size: 20 },
    })

    await personModels.fetchModelPhotoSet(7, 9)
    expect(mocks.get).toHaveBeenLastCalledWith('/models/7/photo-sets/9', {
      params: { page: 1, size: 200 },
    })
    await personModels.fetchModelPhotoSet(7, 9, 3, 60)
    expect(mocks.get).toHaveBeenLastCalledWith('/models/7/photo-sets/9', {
      params: { page: 3, size: 60 },
    })
  })

  it('创建照片组：空 name 统一发 null（后端回退「未命名照片组」）', async () => {
    await personModels.createModelPhotoSet(7)
    expect(mocks.post).toHaveBeenLastCalledWith('/models/7/photo-sets', { name: null })

    await personModels.createModelPhotoSet(7, '')
    expect(mocks.post).toHaveBeenLastCalledWith('/models/7/photo-sets', { name: null })

    await personModels.createModelPhotoSet(7, '街拍')
    expect(mocks.post).toHaveBeenLastCalledWith('/models/7/photo-sets', { name: '街拍' })
  })

  it('删除照片组：DELETE /models/{id}/photo-sets/{setId}', async () => {
    await expect(personModels.deleteModelPhotoSet(7, 9)).resolves.toBeUndefined()
    expect(mocks.delete).toHaveBeenLastCalledWith('/models/7/photo-sets/9')
  })

  it('上传照片：FormData 带 file+sort_order，进度回调/取消信号透传', async () => {
    const file = new File(['img'], 'a.jpg', { type: 'image/jpeg' })
    const onProgress = vi.fn()
    const controller = new AbortController()

    await personModels.uploadModelPhoto(7, 9, file, 2, onProgress, controller.signal)

    const form = lastPostFormData()
    expect(form.get('file')).toBeInstanceOf(File)
    expect(form.get('sort_order')).toBe('2')
    expect(lastArgs(mocks.post)[0]).toBe('/models/7/photo-sets/9/photos')
    expect(lastArgs(mocks.post)[2]).toEqual({
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: onProgress,
      signal: controller.signal,
    })
  })
})

describe('poseGallery API', () => {
  it('元信息与分页列表：GET /pose-gallery/meta、/pose-gallery（params 透传）', async () => {
    await poseGallery.fetchPoseGalleryMeta()
    expect(mocks.get).toHaveBeenLastCalledWith('/pose-gallery/meta')

    await poseGallery.fetchPoses()
    expect(mocks.get).toHaveBeenLastCalledWith('/pose-gallery', { params: {} })

    const query = { q: '站姿', system: '全身', page: 2, size: 24 }
    await poseGallery.fetchPoses(query)
    expect(mocks.get).toHaveBeenLastCalledWith('/pose-gallery', { params: query })
  })

  it('随机姿势：params 含筛选与 exclude（排除当前已选）', async () => {
    await poseGallery.fetchRandomPose()
    expect(mocks.get).toHaveBeenLastCalledWith('/pose-gallery/random', { params: {} })

    await poseGallery.fetchRandomPose({ system: '上半身', exclude: 'full_std_P001_front' })
    expect(mocks.get).toHaveBeenLastCalledWith('/pose-gallery/random', {
      params: { system: '上半身', exclude: 'full_std_P001_front' },
    })
  })

  it('单条姿势与提示词反推：id 分段 URL 编码', async () => {
    const id = 'full_std_P001/正面 图'
    const encoded = encodeURIComponent(id)

    await poseGallery.fetchPose(id)
    expect(mocks.get).toHaveBeenLastCalledWith(`/pose-gallery/${encoded}`)

    await poseGallery.fetchPosePrompt(id)
    expect(mocks.get).toHaveBeenLastCalledWith(`/pose-gallery/${encoded}/prompt`)
  })

  it('请求失败原样 reject', async () => {
    mocks.get.mockRejectedValueOnce(new Error('gallery 502'))
    await expect(poseGallery.fetchPoseGalleryMeta()).rejects.toThrow('gallery 502')
  })
})

describe('aiModelStats', () => {
  const row = (over: Partial<ModelStatItem> = {}): ModelStatItem => ({
    model_name: 'qwen3-vl:8b',
    total_analyses: 10,
    success_count: 9,
    failure_count: 1,
    success_rate: 0.9,
    avg_time_ms: 2000,
    avg_tags: 5,
    last_used: '2026-11-01T00:00:00Z',
    ...over,
  })

  it('fetchModelStats：models 是数组时原样返回', async () => {
    const models = [row()]
    mocks.get.mockResolvedValueOnce({ data: { models } })

    await expect(fetchModelStats()).resolves.toBe(models)
    expect(mocks.get).toHaveBeenLastCalledWith('/ai/model-stats')
  })

  it('形状异常统一回退空数组（调用方回退旧速率估算）', async () => {
    mocks.get.mockResolvedValueOnce({ data: { models: 'oops' } })
    await expect(fetchModelStats()).resolves.toEqual([])

    mocks.get.mockResolvedValueOnce({ data: { models: null } })
    await expect(fetchModelStats()).resolves.toEqual([])

    mocks.get.mockResolvedValueOnce({ data: {} })
    await expect(fetchModelStats()).resolves.toEqual([])

    mocks.get.mockResolvedValueOnce({ data: null })
    await expect(fetchModelStats()).resolves.toEqual([])
  })

  it('overallAvgMs：优先取「（全部模型汇总）」行', () => {
    const items = [
      row({ model_name: '（全部模型汇总）', avg_time_ms: 1800 }),
      row({ model_name: 'qwen3-vl:8b', avg_time_ms: 5000, total_analyses: 10 }),
    ]

    expect(overallAvgMs(items)).toBe(1800)
  })

  it('overallAvgMs：汇总行缺失或无效时按分析次数加权自算', () => {
    const items = [
      // 汇总行 avg=0 → 视为无效，走加权
      row({ model_name: '（全部模型汇总）', avg_time_ms: 0, total_analyses: 30 }),
      row({ model_name: 'a', avg_time_ms: 1000, total_analyses: 10 }),
      row({ model_name: 'b', avg_time_ms: 3000, total_analyses: 20 }),
      // 次数为 0 或 avg 非正的行不参与加权
      row({ model_name: 'c', avg_time_ms: 9999, total_analyses: 0 }),
      row({ model_name: 'd', avg_time_ms: 0, total_analyses: 5 }),
    ]

    // (1000*10 + 3000*20) / 30 = 2333.33…
    expect(overallAvgMs(items)).toBeCloseTo(2333.33, 1)
  })

  it('overallAvgMs：没有可用行时返回 null', () => {
    expect(overallAvgMs([])).toBeNull()
    expect(overallAvgMs([row({ avg_time_ms: 0 })])).toBeNull()
    expect(overallAvgMs([row({ total_analyses: 0 })])).toBeNull()
    // model_name 为空的行（后端异常）不能误判成「全部模型汇总」行
    expect(overallAvgMs([row({ model_name: '', avg_time_ms: 0 })])).toBeNull()
  })
})
