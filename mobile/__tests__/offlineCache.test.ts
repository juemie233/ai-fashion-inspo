/** 离线缓存（P1）：保质期、缓存范围、读写与清理。 */

import AsyncStorage from '@react-native-async-storage/async-storage'
import {
  clearOfflineCache,
  formatCacheTime,
  isCacheFresh,
  loadGalleryCache,
  loadPopularTags,
  saveGalleryCache,
  savePopularTags,
  shouldCache,
  DEFAULT_CACHE_TTL_MS,
} from '../utils/offlineCache'
import type { Inspiration } from '../services/api'

function item(id: string): Inspiration {
  return {
    id,
    source_type: 'douyin',
    file_path: `images/${id}.webp`,
    thumbnail_path: null,
    media_type: 'image',
    is_favorite: false,
    created_at: '2026-11-01T00:00:00Z',
    tags: [],
  } as Inspiration
}

beforeEach(async () => {
  jest.clearAllMocks()
  await AsyncStorage.clear()
})

describe('isCacheFresh', () => {
  it('7 天内可用，超过则不可用', () => {
    const now = Date.now()
    expect(isCacheFresh(now - 1000, now)).toBe(true)
    expect(isCacheFresh(now - DEFAULT_CACHE_TTL_MS + 1000, now)).toBe(true)
    expect(isCacheFresh(now - DEFAULT_CACHE_TTL_MS - 1000, now)).toBe(false)
  })

  it('非法时间戳一律当过期（防脏数据）', () => {
    expect(isCacheFresh(null, Date.now())).toBe(false)
    expect(isCacheFresh(undefined, Date.now())).toBe(false)
    expect(isCacheFresh(Number.NaN, Date.now())).toBe(false)
    // 未来时间（设备时间被调过）也算不可用
    expect(isCacheFresh(Date.now() + 10_000_000, Date.now())).toBe(false)
  })
})

describe('shouldCache', () => {
  it('只缓存「无筛选 + 最新排序」的默认视图', () => {
    expect(shouldCache({}, 'newest')).toBe(true)
    expect(shouldCache({ is_favorite: true }, 'newest')).toBe(false)
    expect(shouldCache({}, 'random')).toBe(false)
  })
})

describe('素材库首页缓存', () => {
  it('写入后能读回（裁剪到 30 条）', async () => {
    const items = Array.from({ length: 40 }, (_, i) => item(`i${i}`))
    await saveGalleryCache(items, 999)

    const entry = await loadGalleryCache()
    expect(entry).not.toBeNull()
    expect(entry?.items).toHaveLength(30)
    expect(entry?.total).toBe(999)
  })

  it('空列表不写缓存（避免把「确实没有素材」当缓存）', async () => {
    await saveGalleryCache([], 0)
    expect(await loadGalleryCache()).toBeNull()
  })

  it('过期缓存读不出来', async () => {
    await saveGalleryCache([item('a')], 1)
    const cached = await loadGalleryCache()
    expect(cached).not.toBeNull()
    // 用「很久以后」的 now 模拟过期
    expect(await loadGalleryCache(Date.now() + DEFAULT_CACHE_TTL_MS + 1000)).toBeNull()
  })

  it('存储损坏时返回 null，不抛错', async () => {
    await AsyncStorage.setItem('offline:gallery', '{not-json')
    await expect(loadGalleryCache()).resolves.toBeNull()
  })
})

describe('常用标签缓存', () => {
  it('写入后能读回（最多 12 个）', async () => {
    await savePopularTags(Array.from({ length: 20 }, (_, i) => `标签${i}`))
    const names = await loadPopularTags()
    expect(names).toHaveLength(12)
    expect(names[0]).toBe('标签0')
  })

  it('空数组不写、损坏返回空数组', async () => {
    await savePopularTags([])
    expect(await loadPopularTags()).toEqual([])

    await AsyncStorage.setItem('offline:popular-tags', '{"savedAt":"x"}')
    expect(await loadPopularTags()).toEqual([])
  })
})

describe('clearOfflineCache', () => {
  it('同时清掉两类缓存', async () => {
    await saveGalleryCache([item('a')], 1)
    await savePopularTags(['法式'])
    await clearOfflineCache()

    expect(await loadGalleryCache()).toBeNull()
    expect(await loadPopularTags()).toEqual([])
  })
})

describe('formatCacheTime', () => {
  it('输出本地月-日 时:分', () => {
    const value = formatCacheTime(new Date('2026-11-12T10:20:00').getTime())
    expect(value).toMatch(/^11-12 \d{2}:\d{2}$/)
    expect(formatCacheTime(Number.NaN)).toBe('')
  })
})
