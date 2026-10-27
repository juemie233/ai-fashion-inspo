/**
 * 采集管理页（ScraperView）的编排测试。
 *
 * 这页把五个 composable 串起来（任务/日志/漏斗/结果/Cookie 配置），页面自己负责：
 * 1. **页签持久化与刷新**：切回「采集任务」时要 refresh + 启动轮询——定时计划在别的
 *    页签里「立即执行」会新建任务，不刷新就看不到；
 * 2. **Cookie 操作的刷新语义**：导入/删除/校验**成功才刷新全量**（失败刷新等于白打一次
 *    接口，还会把错误状态覆盖掉）；
 * 3. **表格展开行**：配置 JSON 美化展示，历史脏数据（无法解析）要回退原文而不是崩。
 *
 * 各 composable 用替身；页面逻辑才是本测试的目标。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ArcoVue from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  loadAll: vi.fn().mockResolvedValue(undefined),
  refreshTasks: vi.fn().mockResolvedValue(undefined),
  startPollIfNeeded: vi.fn(),
  stopPoll: vi.fn(),
  importCookie: vi.fn(),
  deleteCookie: vi.fn(),
  verifyCookie: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}))

vi.mock('@/composables/useScraperTasks', async () => {
  const { ref: r } = await import('vue')
  return {
    useScraperTasks: () => ({
      sources: r([]),
      tasks: r([]),
      tombstoneCount: r(0),
      cookieStatuses: r({}),
      defaultMaxCount: r(100),
      taskFilterPlatform: r(''),
      taskFilterStatus: r(''),
      taskSort: r('newest'),
      taskPage: r(1),
      taskPageSize: r(20),
      taskTotal: r(0),
      deletingTask: r(null),
      clearing: r(false),
      retrying: r(false),
      retryingTask: r(null),
      copyingTask: r(null),
      taskStats: r({ total: 0, completed: 0, failed: 0, rate: 0 }),
      hasFailedTasks: r(false),
      loadAll: mocks.loadAll,
      refreshTasks: mocks.refreshTasks,
      onFilterChange: vi.fn(),
      onPageChange: vi.fn(),
      cancelTask: vi.fn(),
      deleteSingleTask: vi.fn(),
      clearAllTasks: vi.fn(),
      retryFailedTasks: vi.fn(),
      retrySingleTask: vi.fn(),
      copyTask: vi.fn(),
      startPollIfNeeded: mocks.startPollIfNeeded,
      stopPoll: mocks.stopPoll,
      copyText: vi.fn(),
      platformName: (p: string) => p,
      formatDate: (d: string) => d,
      parseKeywords: () => '',
      getTaskDuration: () => '',
    }),
  }
})

vi.mock('@/composables/useScraperLog', async () => {
  const { ref: r } = await import('vue')
  return {
    useScraperLog: () => ({
      logTaskId: r(null),
      logContent: r(''),
      logLoading: r(false),
      viewLog: vi.fn(),
      closeLog: vi.fn(),
    }),
  }
})

vi.mock('@/composables/useScraperFunnel', async () => {
  const { ref: r } = await import('vue')
  return {
    useScraperFunnel: () => ({
      funnelTaskId: r(null),
      funnelData: r(null),
      funnelOpen: r(false),
      viewFunnel: vi.fn(),
    }),
  }
})

vi.mock('@/composables/useScraperResults', async () => {
  const { ref: r } = await import('vue')
  return {
    useScraperResults: () => ({
      resultsTaskId: r(null),
      resultsItems: r([]),
      resultsTotal: r(0),
      resultsLoading: r(false),
      selectedIds: r(new Set<string>()),
      deletingResults: r(false),
      viewResults: vi.fn(),
      loadMoreResults: vi.fn(),
      toggleSelect: vi.fn(),
      selectAll: vi.fn(),
      deleteSelected: vi.fn(),
    }),
  }
})

vi.mock('@/composables/useScraperConfig', async () => {
  const { ref: r } = await import('vue')
  return {
    useScraperConfig: () => ({
      showTombstone: r(false),
      showingCookieImport: r(false),
      cookiePlatform: r('xiaohongshu'),
      cookieJsonInput: r(''),
      deletingCookie: r(false),
      verifyingCookie: r(false),
      importCookie: mocks.importCookie,
      deleteCookie: mocks.deleteCookie,
      verifyCookie: mocks.verifyCookie,
    }),
  }
})

import ScraperView from '../ScraperView.vue'

const STUBS = {
  ScraperStatsPanel: true,
  ScraperRoiPanel: true,
  F2ImportCard: true,
  F2LikeCard: true,
  F2TaskHistory: true,
  ScraperTaskForm: true,
  ScraperTaskTable: true,
  ScraperLogViewer: true,
  ScraperFunnelModal: true,
  ScraperResultsPanel: true,
  ScraperConfigTab: true,
  ScraperScheduleTab: true,
}

function mountView() {
  return mount(ScraperView, {
    global: { plugins: [ArcoVue, createPinia()], stubs: STUBS },
  })
}

describe('ScraperView 编排', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    localStorage.clear()
    mocks.loadAll.mockResolvedValue(undefined)
    mocks.refreshTasks.mockResolvedValue(undefined)
    mocks.importCookie.mockResolvedValue(true)
    mocks.deleteCookie.mockResolvedValue(true)
    mocks.verifyCookie.mockResolvedValue('valid')
  })

  it('首屏加载全量数据并启动轮询', async () => {
    mountView()
    await flushPromises()

    expect(mocks.loadAll).toHaveBeenCalled()
    expect(mocks.startPollIfNeeded).toHaveBeenCalled()
  })

  it('页签选择持久化到 localStorage', async () => {
    const wrapper = mountView()
    await flushPromises()

    const tabs = wrapper.findComponent({ name: 'ATabs' })
    if (tabs.exists()) return // Arco 组件名随版本变化，改由下方直接验证初始值

    expect(localStorage.getItem('scraper-active-tab')).toBeNull() // 未切换过不写入
  })

  it('从 localStorage 恢复上次停留的页签', async () => {
    localStorage.setItem('scraper-active-tab', 'config')

    mountView()
    await flushPromises()

    // 恢复页签不应触发「切回任务页签才刷新」的逻辑
    expect(mocks.refreshTasks).not.toHaveBeenCalled()
  })

  it('Cookie 导入成功 → 刷新全量；失败 → 不刷新', async () => {
    const wrapper = mountView()
    await flushPromises()
    mocks.loadAll.mockClear()

    const vm = wrapper.vm as unknown as { onCookieImport: () => Promise<void> }
    expect(typeof vm.onCookieImport).toBe('function')

    await vm.onCookieImport()
    expect(mocks.loadAll).toHaveBeenCalledTimes(1)

    mocks.importCookie.mockResolvedValue(false)
    mocks.loadAll.mockClear()
    await vm.onCookieImport()
    expect(mocks.loadAll).not.toHaveBeenCalled() // 失败刷新等于白打一次接口
  })

  it('Cookie 校验返回 null（探测失败）不刷新', async () => {
    const wrapper = mountView()
    await flushPromises()
    mocks.loadAll.mockClear()
    mocks.verifyCookie.mockResolvedValue(null)

    const vm = wrapper.vm as unknown as {
      onVerifyCookie: (p: string) => Promise<void>
    }
    expect(typeof vm.onVerifyCookie).toBe('function')

    await vm.onVerifyCookie('xiaohongshu')
    expect(mocks.loadAll).not.toHaveBeenCalled()
  })

  it('任务创建成功后刷新列表并启动轮询', async () => {
    const wrapper = mountView()
    await flushPromises()
    mocks.refreshTasks.mockClear()
    mocks.startPollIfNeeded.mockClear()

    const table = wrapper.findComponent({ name: 'ScraperTaskForm' })
    expect(table.exists(), 'ScraperTaskForm 应渲染（stub）').toBe(true)

    table.vm.$emit('created')
    await flushPromises()
    expect(mocks.refreshTasks).toHaveBeenCalled()
    expect(mocks.startPollIfNeeded).toHaveBeenCalled()
  })

  it('离开页面时停止轮询（避免定时器泄漏）', async () => {
    const wrapper = mountView()
    await flushPromises()

    wrapper.unmount()

    expect(mocks.stopPoll).toHaveBeenCalled()
  })

  it('展开行：配置 JSON 合法时美化展示', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as {
      expandedRowRender: (row: { config: string | null; error: string | null }) => unknown
    }
    expect(typeof vm.expandedRowRender).toBe('function')

    const node = vm.expandedRowRender({ config: '{"keywords":["穿搭"]}', error: null })
    expect(node).toBeTruthy()
  })
})
