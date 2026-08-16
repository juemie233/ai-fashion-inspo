/** useSplitResize 拖拽分栏 composable 单测。 */

import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createApp, nextTick, type ComponentPublicInstance } from 'vue'
import { useSplitResize } from '../useSplitResize'

/** 在真实组件上下文中调用 composable（消除 onUnmounted 无实例警告） */
function withSetup<T>(composable: () => T) {
  let result!: T
  const app = createApp({
    setup() {
      result = composable()
      return () => {}
    },
  })
  const host = document.createElement('div')
  document.body.appendChild(host)
  app.mount(host)
  return {
    get result() {
      return result
    },
    app,
  }
}

function mockRect(width: number, left = 0) {
  const el = document.createElement('div')
  el.getBoundingClientRect = () =>
    ({ left, width, top: 0, height: 0, right: left + width, bottom: 0, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect
  return el
}

describe('useSplitResize', () => {
  let instances: ReturnType<typeof withSetup>[] = []

  beforeEach(() => {
    document.body.innerHTML = ''
    instances = []
  })

  function setup(options?: Parameters<typeof useSplitResize>[0]) {
    const inst = withSetup(() => useSplitResize(options))
    instances.push(inst)
    return inst
  }

  afterEach(() => {
    instances.forEach((i) => i.app.unmount())
    instances = []
  })

  it('初始宽度为配置值（默认 50）', () => {
    const { result } = setup()
    expect(result.leftWidth.value).toBe(50)
  })

  it('自定义初始值与边界', () => {
    const { result } = setup({ initial: 30, min: 10, max: 90 })
    expect(result.leftWidth.value).toBe(30)
  })

  it('拖拽按容器宽度计算百分比', async () => {
    const { result } = setup({ initial: 50 })
    result.containerRef.value = mockRect(1000)
    result.startDrag({ preventDefault: () => {} } as MouseEvent)
    await nextTick()

    document.dispatchEvent(new MouseEvent('mousemove', { clientX: 250 }))
    expect(result.leftWidth.value).toBeCloseTo(25, 5)

    document.dispatchEvent(new MouseEvent('mousemove', { clientX: 750 }))
    expect(result.leftWidth.value).toBeCloseTo(75, 5)
  })

  it('边界 clamp：超出 min/max 被限制', async () => {
    const { result } = setup({ min: 20, max: 80 })
    result.containerRef.value = mockRect(1000)
    result.startDrag({ preventDefault: () => {} } as MouseEvent)
    await nextTick()

    document.dispatchEvent(new MouseEvent('mousemove', { clientX: 10 }))
    expect(result.leftWidth.value).toBe(20)

    document.dispatchEvent(new MouseEvent('mousemove', { clientX: 990 }))
    expect(result.leftWidth.value).toBe(80)
  })

  it('mouseup 停止拖拽并移除监听', () => {
    const { result } = setup()
    result.startDrag({ preventDefault: () => {} } as MouseEvent)
    expect(result.isDragging.value).toBe(true)

    document.dispatchEvent(new MouseEvent('mouseup'))
    expect(result.isDragging.value).toBe(false)
  })

  it('容器未挂载时不计算', async () => {
    const { result } = setup({ initial: 50 })
    result.startDrag({ preventDefault: () => {} } as MouseEvent)
    await nextTick()
    document.dispatchEvent(new MouseEvent('mousemove', { clientX: 300 }))
    expect(result.leftWidth.value).toBe(50)
  })
})
