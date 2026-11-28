/**
 * 标签变更事件总线（`useTagEvents`）的测试（模块此前无测试，75% 覆盖）。
 *
 * 各标签页面靠这条总线在「改名/合并/删除/批量编辑」后刷新数据，语义要点：
 * - 模块级单例：任意组件调用 useTagEvents() 都是同一个总线；
 * - `onTagChanged(handler, types?)` 支持按事件类型过滤，返回的 off 可手动注销；
 * - 广播时遍历快照：回调里注销自己不会打断同一次广播里的其它订阅者；
 * - 组件 setup 内订阅会在卸载时自动注销（避免已销毁组件继续刷新）。
 */

import { describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { mount } from '@vue/test-utils'

import { useTagEvents, type TagEventPayload } from '../useTagEvents'

/** 每次用例独立注销，避免模块级单例在用例间串数据 */
function subscribe(...args: Parameters<ReturnType<typeof useTagEvents>['onTagChanged']>) {
  const off = useTagEvents().onTagChanged(...args)
  cleanups.push(off)
  return off
}
const cleanups: Array<() => void> = []

describe('useTagEvents', () => {
  // 注意：不加 afterEach 清空对「组件卸载」用例无影响，手动 off 幂等
  it('notifyTagChanged 把载荷广播给所有订阅者', () => {
    const a = vi.fn()
    const b = vi.fn()
    subscribe(a)
    subscribe(b)

    const payload: TagEventPayload = { type: 'merged', tagIds: [1], targetId: 2 }
    useTagEvents().notifyTagChanged(payload)

    expect(a).toHaveBeenCalledWith(payload)
    expect(b).toHaveBeenCalledWith(payload)
  })

  it('模块级单例：两个 useTagEvents() 实例共享同一总线', () => {
    const handler = vi.fn()
    const off = useTagEvents().onTagChanged(handler)
    cleanups.push(off)

    useTagEvents().notifyTagChanged({ type: 'created' })

    expect(handler).toHaveBeenCalledTimes(1)
  })

  it('types 过滤：只接收关心的事件类型', () => {
    const handler = vi.fn()
    subscribe(handler, ['merged'])

    useTagEvents().notifyTagChanged({ type: 'created' })
    useTagEvents().notifyTagChanged({ type: 'merged', tagIds: [3] })
    useTagEvents().notifyTagChanged({ type: 'deleted', tagIds: [3] })

    expect(handler).toHaveBeenCalledTimes(1)
    expect(handler).toHaveBeenCalledWith({ type: 'merged', tagIds: [3] })
  })

  it('不传 types 时接收全部类型', () => {
    const handler = vi.fn()
    subscribe(handler)

    useTagEvents().notifyTagChanged({ type: 'created' })
    useTagEvents().notifyTagChanged({ type: 'reorder' })

    expect(handler).toHaveBeenCalledTimes(2)
  })

  it('返回的 off 可注销订阅；重复注销幂等', () => {
    const handler = vi.fn()
    const off = subscribe(handler)

    off()
    off()
    useTagEvents().notifyTagChanged({ type: 'updated', tagIds: [1] })

    expect(handler).not.toHaveBeenCalled()
  })

  it('广播时遍历副本：回调中注销自己不影响同批其它订阅者', () => {
    const later = vi.fn()
    const first = vi.fn(() => off())
    const off = subscribe(first)
    subscribe(later)

    useTagEvents().notifyTagChanged({ type: 'deleted', tagIds: [1] })

    expect(first).toHaveBeenCalledTimes(1)
    expect(later).toHaveBeenCalledTimes(1)

    // 下一次广播时已注销的 first 不再收到
    useTagEvents().notifyTagChanged({ type: 'deleted', tagIds: [2] })
    expect(first).toHaveBeenCalledTimes(1)
    expect(later).toHaveBeenCalledTimes(2)
  })

  it('同一 handler 重复订阅是两条独立订阅（各自收到一次）', () => {
    const handler = vi.fn()
    subscribe(handler)
    subscribe(handler)

    useTagEvents().notifyTagChanged({ type: 'created' })

    expect(handler).toHaveBeenCalledTimes(2)
  })

  it('组件 setup 内订阅：卸载时自动注销（不依赖手动 off）', async () => {
    const handler = vi.fn()
    const Comp = defineComponent({
      setup() {
        useTagEvents().onTagChanged(handler)
        return () => h('div')
      },
    })
    const wrapper = mount(Comp)

    useTagEvents().notifyTagChanged({ type: 'updated', tagIds: [1] })
    expect(handler).toHaveBeenCalledTimes(1)

    wrapper.unmount()
    useTagEvents().notifyTagChanged({ type: 'updated', tagIds: [2] })
    expect(handler).toHaveBeenCalledTimes(1)
  })
})
