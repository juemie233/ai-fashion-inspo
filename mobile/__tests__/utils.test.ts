/** 工具层单测：图片比例缓存、主色解析、最近搜索、触觉反馈封装。
 *
 * 这几个工具都直接决定「看起来对不对」，且都容易在边界（脏数据/无权限/无马达）上翻车，
 * 所以各给一组用例锁住行为。
 */

import AsyncStorage from '@react-native-async-storage/async-storage'
import * as Haptics from 'expo-haptics'
import {
  cacheAspectRatio,
  clampRatio,
  clearAspectRatioCache,
  detailImageHeight,
  getCachedAspectRatio,
  MAX_RATIO,
  MIN_RATIO,
  ratioFromSize,
} from '../utils/imageAspect'
import { dominantColor } from '../utils/dominantColor'
import {
  clearSearchHistory,
  loadSearchHistory,
  MAX_HISTORY,
  pushSearchHistory,
} from '../utils/searchHistory'
import { selectionFeedback, successFeedback, warningFeedback } from '../utils/haptics'

beforeEach(async () => {
  jest.clearAllMocks()
  clearAspectRatioCache()
  await clearSearchHistory()
})

describe('imageAspect', () => {
  it('比例钳制在 [0.4, 2.5]', () => {
    expect(clampRatio(0.1)).toBe(MIN_RATIO)
    expect(clampRatio(9)).toBe(MAX_RATIO)
    expect(clampRatio(0)).toBe(0.75)
    expect(clampRatio(Number.NaN)).toBe(0.75)
    expect(clampRatio(0.8)).toBeCloseTo(0.8)
  })

  it('详情主图高度：宽图整行高、竖图不超上限且仍占整行宽', () => {
    // 横图：宽 360 / 比例 1.5 = 240，未触上限
    expect(detailImageHeight(1.5, 360, 520)).toBeCloseTo(240)
    // 竖图（9:16）：自然高度 640 > 上限 520 → 取 520（宽度仍由页面给整行，故不会靠左）
    expect(detailImageHeight(0.5625, 360, 520)).toBe(520)
    // 比例被钳制：极窄竖图按 MIN_RATIO=0.4 算
    expect(detailImageHeight(0.1, 360, 520)).toBe(520)
    // 非法输入不炸，回退上限
    expect(detailImageHeight(0.75, 0, 520)).toBe(520)
    // 上限非法视为不限（回落到自然高度），别把图画成 1px
    expect(detailImageHeight(Number.NaN, 360, Number.NaN)).toBeCloseTo(360 / 0.75)
  })

  it('由宽高算比例，非法尺寸返回 null', () => {
    expect(ratioFromSize(1200, 1600)).toBeCloseTo(0.75)
    expect(ratioFromSize(0, 100)).toBeNull()
    expect(ratioFromSize(100, Number.NaN)).toBeNull()
  })

  it('缓存读写（onLoad 与首帧复用同一份）', () => {
    expect(getCachedAspectRatio('http://h/a.jpg')).toBeUndefined()
    cacheAspectRatio('http://h/a.jpg', 1.5)
    expect(getCachedAspectRatio('http://h/a.jpg')).toBeCloseTo(1.5)
    // 脏值不落缓存
    cacheAspectRatio('http://h/b.jpg', 0)
    expect(getCachedAspectRatio('http://h/b.jpg')).toBeUndefined()
  })
})

describe('dominantColor', () => {
  it('取 JSON 数组里的第一个十六进制色', () => {
    expect(dominantColor('["#2B3A67 藏青", "#1A1A1A 黑"]')).toBe('#2B3A67')
  })

  it('兼容裸字符串与三位缩写', () => {
    expect(dominantColor('"#abc"')).toBe('#AABBCC')
    expect(dominantColor('#123456')).toBe('#123456')
  })

  it('空值/脏数据返回 null（调用方走主题兜底色）', () => {
    expect(dominantColor(null)).toBeNull()
    expect(dominantColor('')).toBeNull()
    expect(dominantColor('[]')).toBeNull()
    expect(dominantColor('["藏青"]')).toBeNull()
    expect(dominantColor(undefined)).toBeNull()
  })
})

describe('searchHistory', () => {
  it('去重 + 最新在前', async () => {
    await pushSearchHistory('法式')
    await pushSearchHistory('通勤')
    await pushSearchHistory('法式')

    expect(await loadSearchHistory()).toEqual(['法式', '通勤'])
  })

  it('最多保留 MAX_HISTORY 条', async () => {
    for (let i = 0; i < MAX_HISTORY + 5; i += 1) {
      await pushSearchHistory(`关键词${i}`)
    }
    const history = await loadSearchHistory()
    expect(history).toHaveLength(MAX_HISTORY)
    expect(history[0]).toBe(`关键词${MAX_HISTORY + 4}`)
  })

  it('空字符串不入库；存储损坏时返回空数组', async () => {
    await pushSearchHistory('   ')
    expect(await loadSearchHistory()).toEqual([])

    await AsyncStorage.setItem('search:history', '{not-json')
    expect(await loadSearchHistory()).toEqual([])
  })
})

describe('haptics', () => {
  it('三个反馈各自调用对应 API', () => {
    selectionFeedback()
    successFeedback()
    warningFeedback()

    expect(Haptics.selectionAsync).toHaveBeenCalledTimes(1)
    expect(Haptics.notificationAsync).toHaveBeenCalledWith(
      Haptics.NotificationFeedbackType.Success,
    )
    expect(Haptics.notificationAsync).toHaveBeenCalledWith(
      Haptics.NotificationFeedbackType.Warning,
    )
  })

  it('原生模块抛错/拒绝时不影响主流程', () => {
    ;(Haptics.selectionAsync as unknown as jest.Mock).mockImplementationOnce(() => {
      throw new Error('no vibrator')
    })
    expect(() => selectionFeedback()).not.toThrow()

    ;(Haptics.notificationAsync as unknown as jest.Mock).mockRejectedValueOnce(
      new Error('no vibrator'),
    )
    expect(() => successFeedback()).not.toThrow()
  })
})
