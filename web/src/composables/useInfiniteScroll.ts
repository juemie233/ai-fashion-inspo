/** 无限滚动 composable：监听容器滚动，触底时回调。 */

import { watch, onUnmounted, type Ref } from 'vue'

export function useInfiniteScroll(
  containerRef: Ref<HTMLElement | null>,
  onLoadMore: () => void,
  options: { threshold?: number; enabled?: Ref<boolean> } = {}
) {
  const { threshold = 200, enabled } = options
  let cleanup: (() => void) | null = null

  function attach() {
    const el = containerRef.value
    if (!el) return

    function handler() {
      if (enabled && !enabled.value) return
      const { scrollTop, scrollHeight, clientHeight } = el
      if (scrollHeight - scrollTop - clientHeight < threshold) {
        onLoadMore()
      }
    }

    el.addEventListener('scroll', handler, { passive: true })
    cleanup = () => el.removeEventListener('scroll', handler)
  }

  // 当容器引用变化时重新绑定
  const stopWatch = watch(containerRef, attach, { immediate: true })

  onUnmounted(() => {
    cleanup?.()
    stopWatch()
  })
}
