/**
 * 人物详情增强区（usePersonDetailExtras）**补齐用例**：既有用例覆盖了成功路径与「洞察/相似
 * 静默失败」的一半，本文件补：
 *
 * - 相似推荐失败时同时清空 similar 与 duplicates（不残留旧数据）；
 * - 补齐资料真失败（异常）的提示与出错态复位；
 * - 人脸候选加载失败：提示、候选置空、弹窗仍打开；
 * - 用候选素材注册人脸失败：提示并复位 loading；
 * - idsOfCluster 只取前 5 张（后端已排序，超出部分忽略）；
 * - hasFaceCandidates 三种状态。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  fetchInsights: vi.fn(),
  fetchSimilar: vi.fn(),
  enrichProfile: vi.fn(),
  fetchFaceCandidates: vi.fn(),
  registerFace: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
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

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { usePersonDetailExtras } from '../usePersonDetailExtras'

const onProfileUpdated = vi.fn()

function makeExtras(kind: 'blogger' | 'model' = 'blogger') {
  return usePersonDetailExtras(
    () => 7,
    () => kind,
    onProfileUpdated,
  )
}

beforeEach(() => {
  Object.values(mocks).forEach((fn) => {
    if (typeof fn === 'function') fn.mockReset()
  })
  onProfileUpdated.mockReset()
})

describe('洞察/相似失败清理', () => {
  it('相似接口失败：similar 与 duplicates 都清空（不残留上次结果）', async () => {
    mocks.fetchInsights.mockResolvedValue({ months: [] })
    mocks.fetchSimilar.mockRejectedValue({ response: { data: { detail: '500' } } })
    const api = makeExtras()
    api.similar.value = [{ id: 1 }] as never
    api.duplicates.value = [{ key: 'a', items: [] }]

    await api.loadInsightsAndSimilar()

    expect(api.similar.value).toEqual([])
    expect(api.duplicates.value).toEqual([])
    expect(api.similarLoading.value).toBe(false)
    // 洞察仍正常写入，互不影响
    expect(api.insights.value).toEqual({ months: [] })
  })

  it('洞察失败但相似成功：insights 置空、similar 正常写入', async () => {
    mocks.fetchInsights.mockRejectedValue(new Error('insights 500'))
    mocks.fetchSimilar.mockResolvedValue({
      similar: [{ id: 2 }],
      duplicates: [{ key: 'dup', items: [] }],
    })
    const api = makeExtras()

    await api.loadInsightsAndSimilar()

    expect(api.insights.value).toBeNull()
    expect(api.similar.value).toEqual([{ id: 2 }])
    expect(api.duplicates.value).toEqual([{ key: 'dup', items: [] }])
    expect(api.insightsLoading.value).toBe(false)
    expect(api.similarLoading.value).toBe(false)
  })
})

describe('补齐资料失败', () => {
  it('接口异常：提示后端 detail，返回 false，enriching 复位', async () => {
    mocks.enrichProfile.mockRejectedValue({ response: { data: { detail: 'f2 库被占用' } } })
    const api = makeExtras()

    await expect(api.enrichProfile()).resolves.toBe(false)

    expect(mocks.message.error).toHaveBeenCalledWith('f2 库被占用')
    expect(api.enriching.value).toBe(false)
    expect(onProfileUpdated).not.toHaveBeenCalled()
  })
})

describe('人脸候选弹窗失败', () => {
  it('加载失败：提示、候选置空、弹窗保持打开（可重试）', async () => {
    mocks.fetchFaceCandidates.mockRejectedValue({
      response: { data: { detail: '人脸服务未启动' } },
    })
    const api = makeExtras()

    await api.openFaceCandidates()

    expect(mocks.message.error).toHaveBeenCalledWith('人脸服务未启动')
    expect(api.faceCandidates.value).toBeNull()
    expect(api.faceCandidatesVisible.value).toBe(true)
    expect(api.faceCandidatesLoading.value).toBe(false)
  })

  it('hasFaceCandidates：无候选/空簇为 false，有簇为 true', async () => {
    const api = makeExtras()
    expect(api.hasFaceCandidates.value).toBe(false)

    api.faceCandidates.value = { clusters: [] } as never
    expect(api.hasFaceCandidates.value).toBe(false)

    api.faceCandidates.value = { clusters: [{ cluster_id: 1, inspiration_ids: ['a'] }] } as never
    expect(api.hasFaceCandidates.value).toBe(true)
  })
})

describe('用候选素材注册人脸', () => {
  it('接口失败：提示「注册人脸失败」，返回 false，faceRegistering 复位', async () => {
    mocks.registerFace.mockRejectedValue({ response: { data: { detail: '未检出人脸' } } })
    const api = makeExtras()

    await expect(api.registerFaceFromIds(['a', 'b'])).resolves.toBe(false)

    expect(mocks.registerFace).toHaveBeenCalledWith(7, [], ['a', 'b'])
    expect(mocks.message.error).toHaveBeenCalledWith('未检出人脸')
    expect(api.faceRegistering.value).toBe(false)
    expect(onProfileUpdated).not.toHaveBeenCalled()
  })

  it('idsOfCluster：只取前 5 张（后端按置信度已排序）', () => {
    const api = makeExtras()

    expect(api.idsOfCluster({ inspiration_ids: ['a', 'b', 'c'] } as never)).toEqual(['a', 'b', 'c'])
    expect(api.idsOfCluster({ inspiration_ids: ['a', 'b', 'c', 'd', 'e', 'f'] } as never)).toEqual([
      'a',
      'b',
      'c',
      'd',
      'e',
    ])
  })
})
