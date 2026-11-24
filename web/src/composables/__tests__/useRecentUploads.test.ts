/**
 * 最近上传记录（`useRecentUploads`）的测试。
 *
 * 记录存 sessionStorage，是「上传完还能在侧栏回看」的唯一数据源：
 * - 解析失败/非数组必须回退空数组（坏数据不能让页面初始化就抛错）；
 * - 新记录插最前、按 id 去重、最多 20 条（再次上传同一张不能出现两条）；
 * - 每次变更都要写回 sessionStorage，否则刷新即丢。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useRecentUploads } from '../useRecentUploads'

const KEY = 'recent-uploads'

function record(id: string, overrides: Record<string, unknown> = {}) {
  return { id, thumbnailPath: `thumbnails/${id}.jpg`, filePath: `images/${id}.webp`, ...overrides }
}

describe('useRecentUploads', () => {
  beforeEach(() => {
    sessionStorage.clear()
  })

  it('无存储时返回空数组', () => {
    const recent = useRecentUploads()

    expect(recent.recentUploads.value).toEqual([])
  })

  it('读取已保存的记录', () => {
    sessionStorage.setItem(KEY, JSON.stringify([record('a'), record('b', { mediaType: 'video' })]))

    const recent = useRecentUploads()

    expect(recent.recentUploads.value).toHaveLength(2)
    expect(recent.recentUploads.value[0].id).toBe('a')
    expect(recent.recentUploads.value[1].mediaType).toBe('video')
  })

  it('JSON 解析失败回退空数组（坏数据不抛错）', () => {
    sessionStorage.setItem(KEY, 'not-json')

    const recent = useRecentUploads()

    expect(recent.recentUploads.value).toEqual([])
  })

  it('解析结果不是数组时也回退空数组', () => {
    sessionStorage.setItem(KEY, JSON.stringify({ id: 'a' }))
    expect(useRecentUploads().recentUploads.value).toEqual([])

    sessionStorage.setItem(KEY, '123')
    expect(useRecentUploads().recentUploads.value).toEqual([])
  })

  it('新记录插到最前并写回 sessionStorage', () => {
    const recent = useRecentUploads()

    recent.prependRecent('a', null, 'images/a.webp')

    expect(recent.recentUploads.value).toEqual([
      { id: 'a', thumbnailPath: null, filePath: 'images/a.webp', mediaType: undefined },
    ])
    // mediaType 为 undefined，序列化后键被丢弃
    expect(JSON.parse(sessionStorage.getItem(KEY) ?? '[]')).toEqual([
      { id: 'a', thumbnailPath: null, filePath: 'images/a.webp' },
    ])
  })

  it('同 id 去重：旧记录被新记录替换并移到最前', () => {
    sessionStorage.setItem(
      KEY,
      JSON.stringify([record('a', { filePath: 'old.webp' }), record('b')]),
    )
    const recent = useRecentUploads()

    recent.prependRecent('a', null, 'new.webp', 'image')

    expect(recent.recentUploads.value.map((r) => r.id)).toEqual(['a', 'b'])
    expect(recent.recentUploads.value[0].filePath).toBe('new.webp')
    expect(recent.recentUploads.value[1].id).toBe('b')
  })

  it('最多保留 20 条，超出丢弃最旧的', () => {
    const recent = useRecentUploads()

    for (let i = 0; i < 21; i += 1) recent.prependRecent(`id-${i}`, null, `${i}.webp`)

    expect(recent.recentUploads.value).toHaveLength(20)
    expect(recent.recentUploads.value[0].id).toBe('id-20')
    expect(recent.recentUploads.value.at(-1)?.id).toBe('id-1')
    expect(JSON.parse(sessionStorage.getItem(KEY) ?? '[]')).toHaveLength(20)
  })

  it('sessionStorage 写入失败（配额/隐私模式）时静默降级，内存列表仍更新', () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('QuotaExceededError')
    })
    try {
      const recent = useRecentUploads()

      expect(() => recent.prependRecent('q', null, 'q.webp')).not.toThrow()
      expect(recent.recentUploads.value.map((r) => r.id)).toEqual(['q'])
    } finally {
      setItem.mockRestore()
    }
  })

  it('写入后可被新实例读回（刷新页面场景）', () => {
    const first = useRecentUploads()
    first.prependRecent('x', 'thumbnails/x.jpg', 'images/x.webp', 'video')

    const reloaded = useRecentUploads()

    expect(reloaded.recentUploads.value).toEqual([
      { id: 'x', thumbnailPath: 'thumbnails/x.jpg', filePath: 'images/x.webp', mediaType: 'video' },
    ])
  })
})
