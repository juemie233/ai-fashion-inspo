/**
 * 随机浏览 composable：抽一张、回看上一张、换人物清空。
 *
 * 关键行为：
 * - 抽到新图前先把当前图压进历史，「上一张」是**本地回看**（不发请求）；
 * - loading 期间重复点「换一张」只发一次请求（否则连点会乱序覆盖）；
 * - 关掉弹窗不清历史（再打开能接着回看），换人物才清空。
 */

import { describe, expect, it, vi, beforeEach } from 'vitest'
import { Message } from '@arco-design/web-vue'

import { usePersonRandomBrowse } from '../usePersonRandomBrowse'

function item(id: string) {
  return {
    inspiration_id: id,
    file_path: `images/${id}.jpg`,
    thumbnail_path: `thumbnails/${id}.jpg`,
    media_type: 'image',
    confidence: 0,
    created_at: '2026-10-01T00:00:00',
  }
}

function makeApi() {
  const fetchRandom = vi.fn()
  return { api: { fetchRandom }, fetchRandom }
}

describe('usePersonRandomBrowse', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('打开即抽一张，并把筛选条件透传（在筛选结果里随机）', async () => {
    const { api, fetchRandom } = makeApi()
    fetchRandom.mockResolvedValue({ items: [item('a')], total: 12 })
    const browse = usePersonRandomBrowse(
      () => api,
      () => 7,
      () => ({ quality_status: 'approved' }),
    )

    await browse.open()

    expect(browse.visible.value).toBe(true)
    expect(browse.item.value?.inspiration_id).toBe('a')
    expect(browse.total.value).toBe(12)
    expect(fetchRandom).toHaveBeenCalledWith(7, 1, { quality_status: 'approved' })
  })

  it('「上一张」是本地回看，不发请求', async () => {
    const { api, fetchRandom } = makeApi()
    fetchRandom
      .mockResolvedValueOnce({ items: [item('a')], total: 2 })
      .mockResolvedValueOnce({ items: [item('b')], total: 2 })
    const browse = usePersonRandomBrowse(
      () => api,
      () => 7,
      () => ({}),
    )

    await browse.open()
    await browse.next()
    expect(browse.item.value?.inspiration_id).toBe('b')
    expect(browse.historyCount.value).toBe(1)

    browse.prev()
    expect(browse.item.value?.inspiration_id).toBe('a')
    expect(browse.historyCount.value).toBe(0)
    expect(fetchRandom).toHaveBeenCalledTimes(2) // 回看不发请求
  })

  it('没有历史时「上一张」什么都不做', () => {
    const { api } = makeApi()
    const browse = usePersonRandomBrowse(
      () => api,
      () => 7,
      () => ({}),
    )
    browse.prev()
    expect(browse.item.value).toBeNull()
  })

  it('抽取中重复点「换一张」只发一次请求（防连点乱序）', async () => {
    const { api, fetchRandom } = makeApi()
    // 用对象持有 resolve：TS 不会跟踪回调里的赋值，直接写 let 会被窄化成 never
    const pending: { resolve?: (v: unknown) => void } = {}
    fetchRandom.mockImplementation(
      () =>
        new Promise((resolve) => {
          pending.resolve = resolve
        }),
    )
    const browse = usePersonRandomBrowse(
      () => api,
      () => 7,
      () => ({}),
    )

    const first = browse.next()
    const second = browse.next() // loading 中被挡住
    pending.resolve?.({ items: [item('a')], total: 1 })
    await Promise.all([first, second])

    expect(fetchRandom).toHaveBeenCalledTimes(1)
  })

  it('接口失败给提示且不塞空图', async () => {
    const { api, fetchRandom } = makeApi()
    const error = vi.spyOn(Message, 'error').mockImplementation((() => {}) as never)
    fetchRandom.mockRejectedValue(new Error('boom'))
    const browse = usePersonRandomBrowse(
      () => api,
      () => 7,
      () => ({}),
    )

    await browse.next()

    expect(error).toHaveBeenCalled()
    expect(browse.item.value).toBeNull()
    expect(browse.loading.value).toBe(false)
  })

  it('换人物 reset 会清空当前图/历史并关闭弹窗', async () => {
    const { api, fetchRandom } = makeApi()
    fetchRandom.mockResolvedValue({ items: [item('a')], total: 1 })
    const browse = usePersonRandomBrowse(
      () => api,
      () => 7,
      () => ({}),
    )
    await browse.open()
    browse.reset()

    expect(browse.visible.value).toBe(false)
    expect(browse.item.value).toBeNull()
    expect(browse.historyCount.value).toBe(0)
  })
})
