/**
 * openInspiration 单测：按全局「素材打开模式」决定新标签页打开还是当前页跳转，
 * 并覆盖「新标签被浏览器拦截时降级当前页跳转」的兜底。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import type { Router } from 'vue-router'

vi.mock('@/utils/openInNewTab', () => ({
  openInNewTab: vi.fn(),
}))

import { openInNewTab } from '@/utils/openInNewTab'
import { useUiStore } from '@/stores/ui'
import { openInspiration } from '@/utils/openInspiration'

const mockOpen = openInNewTab as unknown as ReturnType<typeof vi.fn>

/** 最小 router 替身：只用到 resolve 与 push */
function makeRouter(): Router {
  return {
    resolve: vi.fn(() => ({ href: '/detail/abc?tag=1' })),
    push: vi.fn(() => Promise.resolve()),
  } as unknown as Router
}

describe('openInspiration', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    mockOpen.mockReset()
  })

  it('新标签页模式（默认）：走 openInNewTab，不改变当前页', () => {
    const router = makeRouter()
    mockOpen.mockReturnValue(true)

    const opened = openInspiration(router, 'abc', { tag: '1' })

    expect(opened).toBe(true)
    expect(router.resolve).toHaveBeenCalledWith({
      name: 'detail',
      params: { id: 'abc' },
      query: { tag: '1' },
    })
    expect(mockOpen).toHaveBeenCalledWith('/detail/abc?tag=1')
    expect(router.push).not.toHaveBeenCalled()
  })

  it('当前页模式：走 router.push，不打开新标签', () => {
    const store = useUiStore()
    store.materialOpenMode = 'current_tab'
    const router = makeRouter()

    const opened = openInspiration(router, 'abc', { tag: '1' })

    expect(opened).toBe(false)
    expect(mockOpen).not.toHaveBeenCalled()
    expect(router.push).toHaveBeenCalledWith({
      name: 'detail',
      params: { id: 'abc' },
      query: { tag: '1' },
    })
  })

  it('新标签页模式但被浏览器拦截：降级当前页跳转，避免点了没反应', () => {
    const router = makeRouter()
    mockOpen.mockReturnValue(false)

    const opened = openInspiration(router, 'abc')

    expect(opened).toBe(false)
    expect(router.push).toHaveBeenCalledWith({
      name: 'detail',
      params: { id: 'abc' },
      query: undefined,
    })
  })
})
