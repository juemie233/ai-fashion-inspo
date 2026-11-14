/** 固定外壳下的页面滚动工具：主内容区回顶部、锁背景滚动。 */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { getMainContentEl, lockMainScroll, scrollPageToTop } from '../pageScroll'

function mountMainContent(): HTMLElement {
  const main = document.createElement('main')
  main.className = 'main-content'
  main.style.overflow = 'auto'
  document.body.appendChild(main)
  return main
}

afterEach(() => {
  document.body.innerHTML = ''
})

describe('pageScroll', () => {
  it('优先滚动主内容区而不是 window', () => {
    const main = mountMainContent()
    const scrollTo = vi.fn()
    main.scrollTo = scrollTo

    scrollPageToTop('instant')

    expect(scrollTo).toHaveBeenCalledWith({ top: 0, behavior: 'instant' })
  })

  it('主内容区不存在时回退到 window.scrollTo', () => {
    const spy = vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
    scrollPageToTop()
    expect(spy).toHaveBeenCalledWith({ top: 0, behavior: 'smooth' })
    spy.mockRestore()
  })

  it('锁主滚动支持嵌套与幂等解锁', () => {
    const main = mountMainContent()
    const unlock1 = lockMainScroll()
    const unlock2 = lockMainScroll()
    expect(getMainContentEl()).toBe(main)
    expect(main.style.overflow).toBe('hidden')

    unlock1()
    expect(main.style.overflow).toBe('hidden')
    unlock2()
    expect(main.style.overflow).toBe('auto')

    // 重复调用同一个解锁函数不会把计数减成负数、误恢复
    unlock2()
    expect(main.style.overflow).toBe('auto')
  })
})
