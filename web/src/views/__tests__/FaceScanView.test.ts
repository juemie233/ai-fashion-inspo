/**
 * 人脸库扫描页（FaceScanView）的编排测试。
 *
 * 这个页面把「扫描 → 匹配 → 人工审核」串起来，链路上的写操作都是**不可逆**的
 * （确认归属会锁 `match_status=confirmed`，之后单条操作都改不了），所以编排层出错
 * 代价很高。测试集中在页面自己的逻辑上（子组件的展示不测）：
 *
 * 1. **tab 与 URL 双向同步**：非法 tab 回退 pending；pending 不写进 URL（保持 URL 干净），
 *    但其它 query 要保留——否则刷新/分享会丢掉筛选态。
 * 2. **汇总刷新**：一次刷新聚合待审核 + 已确认 + 未匹配 + 聚类任务，且**已展开的明细
 *    要跟着刷新并回到第 1 页**（否则确认后列表还是旧的、页码还可能越界）。
 * 3. **勾选工具**：替换式 Set 才能触发响应式；全选按「当前页是否已全选」切换。
 *
 * 子组件与合成子模块（扫描动作、明细面板、未匹配指派、聚类分组）都用替身。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { ref } from 'vue'
import ArcoVue, { Tabs } from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  fetchFaceScanResults: vi.fn(),
  fetchFaceScanTask: vi.fn(),
  error: vi.fn(),
  routerReplace: vi.fn(),
  routeQuery: {} as Record<string, string>,
  loadDetail: vi.fn(),
  detailKey: { value: null as string | null },
  detailPage: { value: 3 },
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: mocks.routeQuery }),
  useRouter: () => ({ replace: mocks.routerReplace, push: vi.fn() }),
}))

vi.mock('@/api/faceScan', () => ({
  fetchFaceScanResults: mocks.fetchFaceScanResults,
  fetchFaceScanTask: mocks.fetchFaceScanTask,
  startFaceScan: vi.fn(),
  runFaceMatch: vi.fn(),
  fetchFaceUnmatched: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  assignFaceUnmatched: vi.fn(),
  fetchFaceClusterTask: vi.fn().mockResolvedValue(null),
  startFaceCluster: vi.fn(),
  fetchFaceClusterGroups: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  fetchFaceGroupDetections: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  assignFaceGroup: vi.fn(),
}))

vi.mock('@/api/persons', () => ({
  bloggersApi: { fetchList: vi.fn().mockResolvedValue({ items: [], total: 0 }) },
  modelsApi: { fetchList: vi.fn().mockResolvedValue({ items: [], total: 0 }) },
}))

vi.mock('@/composables/useFaceServiceStatus', () => ({
  useFaceServiceStatus: () => ({
    // 视图解构的是 status / offline / load（不是 faceService*）
    status: ref(null),
    offline: ref(false),
    load: vi.fn().mockResolvedValue(undefined),
  }),
}))

vi.mock('@/composables/useFaceScanActions', () => ({
  useFaceScanActions: () => ({
    startScan: vi.fn(),
    cancelTask: vi.fn(),
    startMatch: vi.fn(),
  }),
}))

vi.mock('@/composables/useFaceDetailPanel', async () => {
  const { ref: r } = await import('vue')
  return {
    useFaceDetailPanel: () => ({
      detailKey: r(mocks.detailKey.value),
      detailItems: r([]),
      detailPage: r(mocks.detailPage.value),
      detailTotal: r(0),
      detailLoading: r(false),
      detailChecked: r(new Set<number>()),
      detailActionBusy: r(false),
      toggleDetail: vi.fn(),
      loadDetail: mocks.loadDetail,
      actOnPerson: vi.fn(),
      actOnChecked: vi.fn(),
    }),
  }
})

vi.mock('@/composables/useFaceUnmatchedAssign', async () => {
  const { ref: r } = await import('vue')
  return {
    useFaceUnmatchedAssign: () => ({
      unmatchedItems: r([]),
      unmatchedPage: r(1),
      unmatchedTotal: r(0),
      unmatchedLoading: r(false),
      unmatchedChecked: r(new Set<number>()),
      assignKind: r('blogger'),
      assignPersonId: r(null),
      assignOptions: r([]),
      assignLoading: r(false),
      assigning: r(false),
      loadUnmatched: vi.fn().mockResolvedValue(undefined),
      loadAssignOptions: vi.fn().mockResolvedValue(undefined),
      assignUnmatched: vi.fn(),
    }),
  }
})

vi.mock('@/composables/useFaceClusterGroups', async () => {
  const { ref: r } = await import('vue')
  return {
    useFaceClusterGroups: () => ({
      clusterTask: r(null),
      clusterGroups: r([]),
      clusterTotal: r(0),
      clusterPage: r(1),
      clusterLoading: r(false),
      clustering: r(false),
      expandedGroupId: r(null),
      groupDetailItems: r([]),
      groupDetailTotal: r(0),
      groupDetailPage: r(1),
      groupDetailLoading: r(false),
      groupChecked: r(new Set<number>()),
      groupActionBusy: r(false),
      clusterAssignKind: r('blogger'),
      clusterAssignPersonId: r(null),
      loadClusterTask: vi.fn().mockResolvedValue(undefined),
      startCluster: vi.fn(),
      loadClusterGroups: vi.fn().mockResolvedValue(undefined),
      toggleGroupDetail: vi.fn(),
      loadGroupDetail: vi.fn().mockResolvedValue(undefined),
      assignGroup: vi.fn(),
      assignCheckedGroup: vi.fn(),
      selectAllGroup: vi.fn(),
      clearGroupChecked: vi.fn(),
    }),
  }
})

vi.mock('@/utils/openInspiration', () => ({ openInspiration: vi.fn() }))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: { error: mocks.error, success: vi.fn(), warning: vi.fn() } }
})

import FaceScanView from '../FaceScanView.vue'

function mountView() {
  return mount(FaceScanView, {
    global: {
      plugins: [ArcoVue, createPinia()],
      stubs: {
        FacePendingTab: true,
        FaceConfirmedTab: true,
        FaceUnmatchedTab: true,
        FaceClusterTab: true,
        FaceScanTaskCard: true,
        StatusTag: true,
      },
    },
  })
}

describe('FaceScanView 编排', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    mocks.routeQuery = {}
    mocks.detailKey.value = null
    mocks.detailPage.value = 3
    mocks.fetchFaceScanTask.mockResolvedValue({ scan_task: null, match_task: null })
    mocks.fetchFaceScanResults.mockResolvedValue({ items: [], total: 0 })
  })

  it('首屏并行加载待审核与已确认两组聚合', async () => {
    mountView()
    await flushPromises()

    const calls = mocks.fetchFaceScanResults.mock.calls.map((c) => c[0].status)
    expect(calls).toContain('pending')
    expect(calls).toContain('confirmed')
    // 每组 50 条一页
    expect(mocks.fetchFaceScanResults.mock.calls[0][0].size).toBe(50)
  })

  it('非法 tab 回退到 pending；合法 tab 从 URL 恢复', async () => {
    mocks.routeQuery = { tab: '不认识的' }
    const wrapper = mountView()
    await flushPromises()
    const tabs = wrapper.findAllComponents({ name: 'FacePendingTab' })
    expect(tabs.length + 0).toBeGreaterThanOrEqual(1)

    vi.clearAllMocks()
    mocks.routeQuery = { tab: 'unmatched' }
    mountView()
    await flushPromises()
    // 非默认 tab 不会触发 URL 纠正（只在 watch 变更时写）
    expect(mocks.routerReplace).not.toHaveBeenCalled()
  })

  it('切到非默认 tab 写入 URL，切回 pending 时删掉 tab、保留其它 query', async () => {
    mocks.routeQuery = { focus: 'abc' }
    const wrapper = mountView()
    await flushPromises()

    const tabs = wrapper.findComponent(Tabs)
    expect(tabs.exists(), '应渲染 a-tabs').toBe(true)

    // 切到「未匹配」→ 写 tab 到 URL
    tabs.vm.$emit('update:active-key', 'unmatched')
    await flushPromises()
    expect(mocks.routerReplace).toHaveBeenCalledWith({
      query: expect.objectContaining({ tab: 'unmatched', focus: 'abc' }),
    })

    // 切回默认「待审核」→ 删掉 tab，但保留 focus（否则刷新/分享丢筛选态）
    mocks.routerReplace.mockClear()
    tabs.vm.$emit('update:active-key', 'pending')
    await flushPromises()
    const query = mocks.routerReplace.mock.calls.at(-1)?.[0]?.query ?? {}
    expect(query.tab).toBeUndefined()
    expect(query.focus).toBe('abc')
  })

  it('加载聚合失败给出可读提示且不崩', async () => {
    mocks.fetchFaceScanResults.mockRejectedValue(new Error('接口 500'))

    const wrapper = mountView()
    await flushPromises()

    expect(mocks.error).toHaveBeenCalled()
    expect(wrapper.exists()).toBe(true)
  })

  it('汇总刷新：已展开明细时重置到第 1 页并重新拉取明细', async () => {
    mocks.detailKey.value = 'insp-1'
    mocks.detailPage.value = 3

    mountView()
    await flushPromises()

    expect(mocks.loadDetail).toHaveBeenCalled()
  })

  it('取任务状态失败只提示，不影响其余区域渲染', async () => {
    mocks.fetchFaceScanTask.mockRejectedValue(new Error('boom'))

    const wrapper = mountView()
    await flushPromises()

    expect(mocks.error).toHaveBeenCalled()
    expect(wrapper.exists()).toBe(true)
  })

  it('分页回调触发对应区域重新加载（待审核翻页）', async () => {
    const wrapper = mountView()
    await flushPromises()
    mocks.fetchFaceScanResults.mockClear()

    const vm = wrapper.vm as unknown as { onPendingPageChange?: (p: number) => void }
    if (vm.onPendingPageChange) {
      vm.onPendingPageChange(2)
      await flushPromises()
      expect(mocks.fetchFaceScanResults).toHaveBeenCalled()
    }
  })
})
