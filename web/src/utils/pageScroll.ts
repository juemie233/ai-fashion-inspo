/** 固定外壳下的页面滚动工具。
 *
 * `AppLayout` 把滚动容器从 body 改成了 `.main-content`，继续调用
 * `window.scrollTo` 会变成 no-op；所有「回顶部」和「锁背景滚动」统一走这里，
 * 避免各页面再判断滚动容器。
 */

/** 主内容滚动容器选择器（与 AppLayout.vue 的类名保持一致） */
const MAIN_CONTENT_SELECTOR = '.main-content'

/** 取主内容滚动元素；未挂载/非固定外壳时返回 null */
export function getMainContentEl(): HTMLElement | null {
  if (typeof document === 'undefined') return null
  return document.querySelector<HTMLElement>(MAIN_CONTENT_SELECTOR)
}

/** 回到页面顶部：优先滚主内容区，兼容仍由 window 滚动的旧布局 */
export function scrollPageToTop(behavior: ScrollBehavior = 'smooth'): void {
  const el = getMainContentEl()
  if (el) {
    el.scrollTo({ top: 0, behavior })
    return
  }
  window.scrollTo({ top: 0, behavior })
}

let lockCount = 0
let previousOverflow = ''

/** 锁住主内容区滚动（灯箱/全屏预览打开时用），返回解锁函数（可重复调用） */
export function lockMainScroll(): () => void {
  const el = getMainContentEl()
  if (lockCount === 0 && el) {
    previousOverflow = el.style.overflow
    el.style.overflow = 'hidden'
  }
  lockCount += 1

  let released = false
  return () => {
    if (released) return
    released = true
    lockCount = Math.max(0, lockCount - 1)
    if (lockCount === 0 && el) el.style.overflow = previousOverflow
  }
}
