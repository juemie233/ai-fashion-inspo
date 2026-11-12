/** 移动端三个小模块的单测：剪贴板封装、出图档位展示名、图片宽高比 hook。
 *
 * 共同点：都是「原生能力不可用时的降级层」，出错的表现不是崩溃而是**静默不生效**：
 * - `utils/clipboard.ts`（原覆盖率 4.8%）：旧 APK 没有 expo-clipboard 原生模块时，
 *   设置页的「复制地址 / 粘贴地址」要给出可读提示，而不是白屏或抛异常；
 * - `utils/modelLabel.ts`：接口拉不到档位列表时，手机端不能显示英文 key；
 * - `hooks/useAspectRatio.ts`（原 46%）：首帧用缓存兜底、`onLoad` 后写回缓存，
 *   换图时必须重置（否则详情页连续切图会沿用上一张的比例，图片跳变）。
 */

import { act, renderHook } from '@testing-library/react-native'

jest.mock('expo-clipboard', () => ({
  setStringAsync: jest.fn(async () => true),
  getStringAsync: jest.fn(async () => ''),
}))

// eslint-disable-next-line @typescript-eslint/no-var-requires
const expoClipboard = require('expo-clipboard') as {
  setStringAsync: jest.Mock
  getStringAsync: jest.Mock
}

import { copyText, readClipboard } from '../utils/clipboard'
import { FALLBACK_MODEL_LABELS, modelLabel, shortModelLabel } from '../utils/modelLabel'
import { useAspectRatio } from '../hooks/useAspectRatio'
import { clearAspectRatioCache, getCachedAspectRatio } from '../utils/imageAspect'

beforeEach(() => {
  jest.clearAllMocks()
  clearAspectRatioCache()
  ;(expoClipboard.setStringAsync as jest.Mock).mockResolvedValue(true)
  ;(expoClipboard.getStringAsync as jest.Mock).mockResolvedValue('')
})

describe('clipboard.copyText', () => {
  it('空串/全空白直接拒绝，不调原生模块', async () => {
    await expect(copyText('')).resolves.toEqual({ ok: false, message: '没有可复制的内容' })
    await expect(copyText('   ')).resolves.toEqual({ ok: false, message: '没有可复制的内容' })
    expect(expoClipboard.setStringAsync).not.toHaveBeenCalled()
  })

  it('复制时去首尾空白并返回成功', async () => {
    await expect(copyText('  http://10.0.0.9:18888 \n')).resolves.toEqual({
      ok: true,
      message: '已复制到剪贴板',
    })
    expect(expoClipboard.setStringAsync).toHaveBeenCalledWith('http://10.0.0.9:18888')
  })

  it('原生写入失败时返回可读提示（不抛异常）', async () => {
    ;(expoClipboard.setStringAsync as jest.Mock).mockRejectedValue(new Error('denied'))

    await expect(copyText('x')).resolves.toEqual({
      ok: false,
      message: '复制失败，请手动选择文本复制',
    })
  })
})

describe('clipboard.readClipboard', () => {
  it('读到文本时去掉首尾空白', async () => {
    ;(expoClipboard.getStringAsync as jest.Mock).mockResolvedValue('  a\nb  ')
    await expect(readClipboard()).resolves.toBe('a\nb')
  })

  it('非字符串返回值按空处理（原生实现异常）', async () => {
    ;(expoClipboard.getStringAsync as jest.Mock).mockResolvedValue(undefined)
    await expect(readClipboard()).resolves.toBe('')
  })

  it('读取抛异常时返回空串', async () => {
    ;(expoClipboard.getStringAsync as jest.Mock).mockRejectedValue(new Error('boom'))
    await expect(readClipboard()).resolves.toBe('')
  })
})

describe('modelLabel', () => {
  it('空 key 显示「本地出图」', () => {
    expect(modelLabel(null, {})).toBe('本地出图')
    expect(modelLabel(undefined, {})).toBe('本地出图')
  })

  it('后端 labels 优先级最高', () => {
    expect(modelLabel('z_image_int8', { z_image_int8: '后端给的档位名' })).toBe('后端给的档位名')
  })

  it('接口拉不到时用内置兜底表，再不行才显示原始 key', () => {
    expect(modelLabel('z_image_int8', {})).toBe('Z-Image')
    expect(modelLabel('qwen_2_1_uc_i2i', {})).toBe('Qwen 2.1 图生图（未审查）')
    expect(modelLabel('brand_new_model', {})).toBe('brand_new_model')
    // 兜底表只覆盖已知档位
    expect(Object.keys(FALLBACK_MODEL_LABELS)).toEqual(
      expect.arrayContaining(['qwen_2_1_i2i', 'qwen_2_1_uc_i2i', 'z_image_int8']),
    )
  })

  it('shortModelLabel 去掉括号补充说明，无括号原样返回', () => {
    expect(shortModelLabel('Z-Image-Turbo int8（快，1024² 约 6 秒）')).toBe('Z-Image-Turbo int8')
    expect(shortModelLabel('Qwen 2.1 图生图')).toBe('Qwen 2.1 图生图')
  })
})

describe('useAspectRatio', () => {
  it('无缓存时返回兜底比例', () => {
    const { result } = renderHook(() => useAspectRatio('uri-a', 0.8))
    expect(result.current.ratio).toBe(0.8)
  })

  it('有缓存时首帧直接用缓存值（避免闪跳）', () => {
    act(() => {
      const { cacheAspectRatio } = require('../utils/imageAspect')
      cacheAspectRatio('uri-cached', 1.5)
    })

    const { result } = renderHook(() => useAspectRatio('uri-cached', 0.8))

    expect(result.current.ratio).toBe(1.5)
  })

  it('onLoad 后按真实尺寸修正比例并写回缓存', () => {
    const { result } = renderHook(() => useAspectRatio('uri-load', 0.8))

    act(() => {
      result.current.onLoad({ source: { width: 1600, height: 1000 } })
    })

    expect(result.current.ratio).toBeCloseTo(1.6)
    expect(getCachedAspectRatio('uri-load')).toBeCloseTo(1.6)
  })

  it('尺寸非法（0/缺失）时保持原比例，不写缓存', () => {
    const { result } = renderHook(() => useAspectRatio('uri-bad', 0.8))

    act(() => {
      result.current.onLoad({ source: { width: 0, height: 0 } })
      result.current.onLoad({})
      result.current.onLoad(null as never)
    })

    expect(result.current.ratio).toBe(0.8)
    expect(getCachedAspectRatio('uri-bad')).toBeUndefined()
  })

  it('超宽/超窄图片比例被钳制在 [0.4, 2.5]', () => {
    const { result } = renderHook(() => useAspectRatio('uri-wide', 0.8))
    act(() => {
      result.current.onLoad({ source: { width: 5000, height: 500 } })
    })
    expect(result.current.ratio).toBe(2.5)

    const narrow = renderHook(() => useAspectRatio('uri-narrow', 0.8))
    act(() => {
      narrow.result.current.onLoad({ source: { width: 300, height: 3000 } })
    })
    expect(narrow.result.current.ratio).toBe(0.4)
  })

  it('换图时重置为兜底/新图缓存，不沿用上一张比例', () => {
    const { result, rerender } = renderHook(({ uri }) => useAspectRatio(uri, 0.8), {
      initialProps: { uri: 'uri-old' },
    })
    act(() => {
      result.current.onLoad({ source: { width: 800, height: 800 } }) // 1.0
    })
    expect(result.current.ratio).toBe(1.0)

    rerender({ uri: 'uri-new' })

    expect(result.current.ratio).toBe(0.8) // 回到兜底，而不是 1.0
  })
})

// 放在最后：用「require 就抛」的工厂模拟旧 APK 缺原生模块的情况
describe('原生模块缺失（旧 APK）', () => {
  beforeAll(() => {
    // 先清模块缓存：expo-clipboard 已被顶部的 jest.mock 加载过，不清缓存
    // doMock 的「require 就抛」工厂不会生效
    jest.resetModules()
    jest.doMock('expo-clipboard', () => {
      throw new Error('Cannot find native module ExpoClipboard')
    })
  })

  afterAll(() => {
    jest.dontMock('expo-clipboard')
    jest.resetModules()
  })

  it('复制给出「请更新 App」提示', async () => {
    await expect(copyText('x')).resolves.toEqual({
      ok: false,
      message: '当前版本不支持该功能，请更新 App 后重试',
    })
  })

  it('读取返回空串（设置页粘贴按钮不崩）', async () => {
    await expect(readClipboard()).resolves.toBe('')
  })
})
