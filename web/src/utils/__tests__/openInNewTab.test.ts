/**
 * 站内导航辅助 openInNewTab：新标签页打开，返回值可靠表示成功与否。
 *
 * 这段代码的注释记录了一个踩过的坑：`window.open(href, '_blank', 'noopener,noreferrer')`
 * 在 features 含 noopener 时**返回 null**，调用方无法区分「已打开新标签」与「被拦截」；
 * 若据此降级为当前页跳转，会出现新标签和当前页双跳。这里用单测把这个约定钉住：
 * 只传两个参数、自己切断 opener、返回布尔值。
 */

import { afterEach, describe, expect, it, vi } from 'vitest'

import { openInNewTab } from '../openInNewTab'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('openInNewTab', () => {
  it('成功打开：window.open(href, "_blank") 且切断新窗口的 opener', () => {
    const fakeWindow = { opener: 'https://current-page.example' } as unknown as Window
    const open = vi.spyOn(window, 'open').mockReturnValue(fakeWindow)

    expect(openInNewTab('/inspirations/abc?tab=detail#top')).toBe(true)

    expect(open).toHaveBeenCalledWith('/inspirations/abc?tab=detail#top', '_blank')
    // opener 必须被手动置空（等价 noopener 的安全效果，但不影响返回值）
    expect(fakeWindow.opener).toBeNull()
  })

  it('只传两个参数：feature 串里带 noopener 会让 window.open 返回 null，无法判定成功', () => {
    const fakeWindow = {} as Window
    const open = vi.spyOn(window, 'open').mockReturnValue(fakeWindow)

    openInNewTab('/collections/5')

    expect(open.mock.calls[0]).toHaveLength(2)
  })

  it('被浏览器弹窗拦截（返回 null）时返回 false，调用方据此提示', () => {
    vi.spyOn(window, 'open').mockReturnValue(null)

    expect(openInNewTab('/collections/5')).toBe(false)
  })

  it('空字符串等任意 href 原样透传（由调用方保证合法 URL）', () => {
    const fakeWindow = {} as Window
    const open = vi.spyOn(window, 'open').mockReturnValue(fakeWindow)

    expect(openInNewTab('')).toBe(true)
    expect(open).toHaveBeenCalledWith('', '_blank')
  })
})
