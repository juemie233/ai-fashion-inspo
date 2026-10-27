/**
 * 人物详情「增强区」composable：补齐资料 / 人脸候选 / 注册 / 洞察与相似。
 *
 * 这几条最容易出错的都是**失败与边界**：
 * - 洞察/相似属于锦上添花，失败必须静默（否则详情页整块报错，主信息也看不到了）；
 * - 补齐资料要区分「补成功」「没可补的（f2 没这个账号）」「真失败」三种反馈；
 * - 用候选素材注册人脸：后端上限 5 张，前端要在发请求前就挡住并提示。
 */

import { describe, expect, it, vi, beforeEach } from 'vitest'
import { Message } from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  fetchInsights: vi.fn(),
  fetchSimilar: vi.fn(),
  enrichProfile: vi.fn(),
  fetchFaceCandidates: vi.fn(),
  registerFace: vi.fn(),
}))

vi.mock('@/api/persons', () => ({
  bloggersApi: {
    fetchInsights: mocks.fetchInsights,
    fetchSimilar: mocks.fetchSimilar,
    enrichProfile: mocks.enrichProfile,
    fetchFaceCandidates: mocks.fetchFaceCandidates,
    registerFace: mocks.registerFace,
  },
}))

import { usePersonDetailExtras } from '../usePersonDetailExtras'

const onProfileUpdated = vi.fn()

function makeExtras(kind: 'blogger' | 'model' = 'blogger') {
  return usePersonDetailExtras(
    () => 7,
    () => kind,
    onProfileUpdated,
  )
}

describe('usePersonDetailExtras', () => {
  beforeEach(() => {
    Object.values(mocks).forEach((fn) => fn.mockReset())
    onProfileUpdated.mockReset()
    vi.restoreAllMocks()
  })

  it('洞察/相似各自失败都静默（不影响另一半）', async () => {
    mocks.fetchInsights.mockRejectedValue(new Error('boom'))
    mocks.fetchSimilar.mockResolvedValue({
      similar: [
        {
          id: 2,
          name: '甲',
          avatar_path: null,
          inspiration_count: 3,
          shared_tags: 5,
          similarity: 0.4,
        },
      ],
      duplicates: [],
    })
    const extras = makeExtras()

    await extras.loadInsightsAndSimilar()

    expect(extras.insights.value).toBeNull()
    expect(extras.similar.value).toHaveLength(1)
    expect(extras.insightsLoading.value).toBe(false)
    expect(extras.similarLoading.value).toBe(false)
  })

  it('补齐资料成功：刷新详情 + 展示补了哪些字段', async () => {
    mocks.enrichProfile.mockResolvedValue({
      status: 'updated',
      updated_fields: ['follower_count', 'bio'],
    })
    const success = vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
    const extras = makeExtras()

    expect(await extras.enrichProfile()).toBe(true)
    expect(onProfileUpdated).toHaveBeenCalledTimes(1)
    expect(success.mock.calls[0][0]).toContain('follower_count')
  })

  it('没有可补的资料：给后端的可读原因，不算失败', async () => {
    mocks.enrichProfile.mockResolvedValue({
      status: 'skipped',
      reason: 'f2 用户库里没有这个账号',
    })
    const info = vi.spyOn(Message, 'info').mockImplementation((() => {}) as never)
    const extras = makeExtras()

    expect(await extras.enrichProfile()).toBe(false)
    expect(info.mock.calls[0][0]).toContain('f2 用户库里没有这个账号')
    expect(onProfileUpdated).not.toHaveBeenCalled()
  })

  it('模特不支持补齐资料（不发请求）', async () => {
    const extras = makeExtras('model')
    expect(await extras.enrichProfile()).toBe(false)
    expect(mocks.enrichProfile).not.toHaveBeenCalled()
  })

  it('人脸候选只拉一次（重复打开弹窗不重复聚类）', async () => {
    mocks.fetchFaceCandidates.mockResolvedValue({
      matched: [],
      clusters: [],
      candidate_faces: 0,
      face_dim: 512,
    })
    const extras = makeExtras()

    await extras.openFaceCandidates()
    await extras.openFaceCandidates()

    expect(mocks.fetchFaceCandidates).toHaveBeenCalledTimes(1)
    expect(extras.faceCandidatesVisible.value).toBe(true)
  })

  it('注册人脸：超过 5 张或空选都在本地挡下', async () => {
    const warn = vi.spyOn(Message, 'warning').mockImplementation((() => {}) as never)
    const extras = makeExtras()

    expect(await extras.registerFaceFromIds([])).toBe(false)
    expect(await extras.registerFaceFromIds(['1', '2', '3', '4', '5', '6'])).toBe(false)
    expect(warn).toHaveBeenCalledTimes(2)
    expect(mocks.registerFace).not.toHaveBeenCalled()
  })

  it('注册人脸成功：关闭弹窗、清空缓存、刷新详情', async () => {
    mocks.registerFace.mockResolvedValue({ message: 'ok' })
    vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
    const extras = makeExtras()
    extras.faceCandidates.value = {
      matched: [],
      clusters: [],
      candidate_faces: 0,
      face_dim: 512,
    }

    expect(await extras.registerFaceFromIds(['a', 'b', 'c'])).toBe(true)
    expect(mocks.registerFace).toHaveBeenCalledWith(7, [], ['a', 'b', 'c'])
    expect(extras.faceCandidatesVisible.value).toBe(false)
    expect(extras.faceCandidates.value).toBeNull()
    expect(onProfileUpdated).toHaveBeenCalledTimes(1)
  })
})
