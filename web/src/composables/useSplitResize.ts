/** 可拖拽分栏：返回容器引用、左栏宽度百分比与拖拽控制。 */

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

  function onDrag(e: MouseEvent) {
    if (!containerRef.value) return
    const rect = containerRef.value.getBoundingClientRect()
    const pct = ((e.clientX - rect.left) / rect.width) * 100
    leftWidth.value = Math.min(max, Math.max(min, pct))
  }

  function stopDrag() {
    isDragging.value = false
    document.removeEventListener('mousemove', onDrag)
    document.removeEventListener('mouseup', stopDrag)
  }

  function startDrag(e: MouseEvent) {
    e.preventDefault()
    isDragging.value = true
    document.addEventListener('mousemove', onDrag)
    document.addEventListener('mouseup', stopDrag)
  }

  onUnmounted(stopDrag)

  return { containerRef, leftWidth, isDragging, startDrag }
}
