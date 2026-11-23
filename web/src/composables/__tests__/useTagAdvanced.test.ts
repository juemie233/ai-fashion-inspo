/**
 * 高级标签管理页状态（`useTagAdvanced`）的测试。
 *
 * 这份状态是**模块级单例**（管理视图与各子面板共用同一份 batchEditVisible），
 * 所以测试要特别钉住：
 * - 多次调用 useTagAdvanced() 拿到的是同一个 ref（否则「打开抽屉」事件跨组件失效）；
 * - 打开批量编辑时可选携带预选标签/类别，再次打开会覆盖而不是累积；
 * - Tab 从 URL 恢复、切换时写回 URL；默认 health 不写 tab，保留其它 query。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'

const routeState = vi.hoisted(() => ({
  query: {} as Record<string, string>,
  replace: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: routeState.query }),
  useRouter: () => ({ replace: routeState.replace }),
}))

import { ADVANCED_TABS, useTagAdvanced } from '../useTagAdvanced'

describe('useTagAdvanced', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    routeState.query = {}
    // 模块级单例状态：每个用例开始前复位
    const state = useTagAdvanced()
    state.batchEditVisible.value = false
    state.batchEditInitialTagIds.value = []
    state.batchEditInitialCategory.value = undefined
    state.activeTab.value = 'health'
  })

  describe('Tab 状态与 URL 持久化', () => {
    it('URL 无 tab：默认 health；非法 tab 也回退 health', () => {
      expect(useTagAdvanced().activeTab.value).toBe('health')

      routeState.query = { tab: 'bogus' }
      expect(useTagAdvanced().activeTab.value).toBe('health')
    })

    it('从 URL 恢复合法 tab', () => {
      routeState.query = { tab: 'cluster' }
      expect(useTagAdvanced().activeTab.value).toBe('cluster')

      routeState.query = { tab: 'governance' }
      expect(useTagAdvanced().activeTab.value).toBe('governance')
    })

    it('ADVANCED_TABS 覆盖全部面板（健康度/聚类/网络/效果/层级/历史/治理）', () => {
      expect(ADVANCED_TABS).toEqual([
        'health',
        'cluster',
        'network',
        'effect',
        'tree',
        'history',
        'governance',
      ])
    })

    it('切到非 health 时写入 tab，切回 health 时删除 tab 并保留其它参数', async () => {
      routeState.query = { focus: 'x' }
      const state = useTagAdvanced()

      state.activeTab.value = 'tree'
      await nextTick()
      expect(routeState.replace).toHaveBeenLastCalledWith({ query: { focus: 'x', tab: 'tree' } })

      state.activeTab.value = 'health'
      await nextTick()
      expect(routeState.replace).toHaveBeenLastCalledWith({ query: { focus: 'x' } })
    })

    it('初始 tab 来自 URL，但挂载时不主动改写 URL（仅变化时 replace）', () => {
      routeState.query = { tab: 'effect' }
      useTagAdvanced()

      expect(routeState.replace).not.toHaveBeenCalled()
    })
  })

  describe('批量编辑抽屉（模块级单例）', () => {
    it('默认打开：无预选标签、无预选类别', () => {
      const state = useTagAdvanced()

      state.openBatchEdit()

      expect(state.batchEditVisible.value).toBe(true)
      expect(state.batchEditInitialTagIds.value).toEqual([])
      expect(state.batchEditInitialCategory.value).toBeUndefined()
    })

    it('可携带预选标签与类别（从健康度面板跳转时用）', () => {
      const state = useTagAdvanced()

      state.openBatchEdit({ tag_ids: [1, 2], category: 'style' })

      expect(state.batchEditInitialTagIds.value).toEqual([1, 2])
      expect(state.batchEditInitialCategory.value).toBe('style')
    })

    it('再次打开会覆盖上一次的预选，不累积', () => {
      const state = useTagAdvanced()
      state.openBatchEdit({ tag_ids: [1, 2], category: 'style' })

      state.openBatchEdit({ tag_ids: [3] })

      expect(state.batchEditInitialTagIds.value).toEqual([3])
      expect(state.batchEditInitialCategory.value).toBeUndefined()
    })

    it('多次调用拿到同一份 ref（跨组件共享入口）', () => {
      const a = useTagAdvanced()
      const b = useTagAdvanced()

      expect(a.batchEditVisible).toBe(b.batchEditVisible)
      expect(a.batchEditInitialTagIds).toBe(b.batchEditInitialTagIds)
      expect(a.batchEditInitialCategory).toBe(b.batchEditInitialCategory)
    })

    it('两个实例的 openBatchEdit 互相可见', () => {
      const a = useTagAdvanced()
      const b = useTagAdvanced()

      a.openBatchEdit({ tag_ids: [7] })

      expect(b.batchEditVisible.value).toBe(true)
      expect(b.batchEditInitialTagIds.value).toEqual([7])
    })
  })
})
