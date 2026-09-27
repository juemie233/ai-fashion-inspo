/**
 * 相似推荐 composable（useSimilarItems）回归测试。
 *
 * 覆盖「加载失败不能伪装成没有相似素材」：2026-09-27 用户报某素材没有相似推荐，
 * 真因是接口 500（候选素材带人物关联 → 序列化时异步懒加载报错），而这里原先
 * 静默 catch 成空列表，界面于是显示「暂无相似素材（需要先回填向量…）」——
 * 把后端故障伪装成数据缺失。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import { useSimilarItems } from '../useSimilarItems'

const api = vi.hoisted(() => ({
  fetchSimilar: vi.fn(),
  toggleFavorite: vi.fn(),
  moveToTrash: vi.fn(),
  batchAddTagsToInspirations: vi.fn(),
}))

vi.mock('@/api/search', () => ({ fetchSimilar: api.fetchSimilar }))
vi.mock('@/api/inspirations', () => ({
  toggleFavorite: api.toggleFavorite,
  moveToTrash: api.moveToTrash,
  batchAddTagsToInspirations: api.batchAddTagsToInspirations,
}))

/** 构造 composable：isCurrentSeq 由 current.seq 控制，便于测过期响应 */
function setup(current: { seq: number } = { seq: 1 }) {
  return useSimilarItems(
    ref(null),
    ref([]),
    () => [],
    (seq) => seq === current.seq,
  )
}

describe('useSimilarItems 加载状态', () => {
  beforeEach(() => {
    api.fetchSimilar.mockReset()
  })

  it('接口失败：清空列表并标记 failed（界面据此显示「加载失败」而非「暂无」）', async () => {
    api.fetchSimilar.mockRejectedValueOnce(new Error('Request failed with status code 500'))
    const { similarItems, similarFailed, loadSimilar } = setup()

    await loadSimilar('insp-1', 1)

    expect(similarItems.value).toEqual([])
    expect(similarFailed.value).toBe(true)
  })

  it('接口成功：failed 复位并填充列表', async () => {
    api.fetchSimilar.mockResolvedValueOnce({
      similar: [
        { inspiration: { id: 'c1' }, similarity: 0.9, shared_tags: 1, match_source: 'tag' },
      ],
    })
    const { similarItems, similarFailed, loadSimilar } = setup()

    await loadSimilar('insp-2', 1)

    expect(similarFailed.value).toBe(false)
    expect(similarItems.value.map((it) => it.inspiration.id)).toEqual(['c1'])
  })

  it('失败后再成功：failed 能复位（不会一直显示失败）', async () => {
    const { similarFailed, loadSimilar } = setup()
    api.fetchSimilar.mockRejectedValueOnce(new Error('500'))
    await loadSimilar('insp-3', 1)
    expect(similarFailed.value).toBe(true)

    api.fetchSimilar.mockResolvedValueOnce({ similar: [] })
    await loadSimilar('insp-4', 1)
    expect(similarFailed.value).toBe(false)
  })

  it('过期响应不写状态（序号不匹配时既不填充也不标记失败）', async () => {
    api.fetchSimilar.mockRejectedValueOnce(new Error('500'))
    const current = { seq: 1 }
    const { similarFailed, loadSimilar } = setup(current)

    await loadSimilar('insp-5', 2) // 2 !== 1 → 过期响应

    expect(similarFailed.value).toBe(false)
  })
})
