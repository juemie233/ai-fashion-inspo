/** 可拖拽分栏：返回容器引用、左栏宽度百分比与指针拖拽控制。
 *
 * 用 Pointer Events 而不是 mousemove：iPad/触控笔同样能拖动分栏线；
 * 分隔线在触屏上由调用方放大命中区（见 TagManageView 的 .divider 样式）。
 */

import { ref, onUnmounted } from 'vue'

export interface SplitResizeOptions {
  /** 初始左栏宽度百分比 */
  initial?: number
  /** 左栏最小宽度百分比 */
  min?: number
  /** 左栏最大宽度百分比 */
  max?: number
}

export function useSplitResize(options: SplitResizeOptions = {}) {
  const initial = options.initial ?? 50
  const min = options.min ?? 20
  const max = options.max ?? 80

  const containerRef = ref<HTMLElement | null>(null)
  const leftWidth = ref(initial)
  const isDragging = ref(false)

  function onDrag(e: PointerEvent) {
    if (!containerRef.value) return
    const rect = containerRef.value.getBoundingClientRect()
    const pct = ((e.clientX - rect.left) / rect.width) * 100
    leftWidth.value = Math.min(max, Math.max(min, pct))
  }

  function stopDrag() {
    isDragging.value = false
    document.removeEventListener('pointermove', onDrag)
    document.removeEventListener('pointerup', stopDrag)
    document.removeEventListener('pointercancel', stopDrag)
  }

  function startDrag(e: PointerEvent) {
    e.preventDefault()
    isDragging.value = true
    document.addEventListener('pointermove', onDrag)
    document.addEventListener('pointerup', stopDrag)
    document.addEventListener('pointercancel', stopDrag)
  }

  onUnmounted(stopDrag)

  return { containerRef, leftWidth, isDragging, startDrag }
}
