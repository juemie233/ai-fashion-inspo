/**
 * 三个「Tab 容器页」的编排测试：人物管理 / AI 模型管理 / 任务管理。
 *
 * 前两个都是纯容器（62 行）：Tab + URL 持久化 + 挂载时拉数据。看起来没什么可测，
 * 但它们各有一处**会让用户困惑**的语义：
 * - Tab 写进 URL 时，默认 Tab 不写（保持地址干净），但**其它 query 必须保留**——
 *   列表页的页码/搜索条件就挂在同一个 URL 上，丢了就等于「切个 tab 筛选全没了」；
 * - 非法 tab 值要回退到默认页，而不是渲染空白。
 *
 * 任务管理页额外负责「拉数据 + 轮询 + 停止轮询」，漏了停止就是定时器泄漏
 * （页面离开后还在打接口）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ArcoVue from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  routeQuery: {} as Record<string, string>,
  routerReplace: vi.fn(),
  routerPush: vi.fn(),
  refreshModels: vi.fn(),
  loadTasks: vi.fn().mockResolvedValue(undefined),
  startPoll: vi.fn(),
  stopPoll: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: mocks.routeQuery }),
  useRouter: () => ({ replace: mocks.routerReplace, push: mocks.routerPush }),
}))

vi.mock('@/stores/aiModels', async () => {
  const { ref: r } = await import('vue')
  return {
    useAiModelsStore: () => ({
      models: r([]),
      activeModel: r(''),
      loading: r(false),
      refreshModels: mocks.refreshModels,
    }),
  }
})

vi.mock('@/composables/useTaskCenter', async () => {
  const { ref: r } = await import('vue')
  return {
    useTaskCenter: () => ({
      loading: r(false),
      statusFilter: r(''),
      typeFilter: r(''),
      page: r(1),
      retrying: r(false),
      total: r(0),
      pageItems: r([]),
      hasActive: r(false),
      hasFailedScraper: r(false),
      loadTasks: mocks.loadTasks,
      onFilterChange: vi.fn(),
      cancelTask: vi.fn(),
      deleteTask: vi.fn(),
      pauseTask: vi.fn(),
      resumeTask: vi.fn(),
      retryFailedScraper: vi.fn(),
      startPoll: mocks.startPoll,
      stopPoll: mocks.stopPoll,
    }),
  }
})

import PersonView from '../PersonView.vue'
import ModelManageView from '../ModelManageView.vue'
import TaskManageView from '../TaskManageView.vue'

const PERSON_STUBS = { PersonListSection: true }
const MODEL_STUBS = {
  ModelListPanel: true,
  AnalysisPanel: true,
  SettingsPanel: true,
  QualityPanel: true,
  ReviewPanel: true,
}
const TASK_STUBS = { TaskList: true, BackupStatusCard: true }

function mountPerson() {
  return mount(PersonView, {
    global: { plugins: [ArcoVue, createPinia()], stubs: PERSON_STUBS },
  })
}

function mountModels() {
  return mount(ModelManageView, {
    global: { plugins: [ArcoVue, createPinia()], stubs: MODEL_STUBS },
  })
}

function mountTasks() {
  return mount(TaskManageView, {
    global: { plugins: [ArcoVue, createPinia()], stubs: TASK_STUBS },
  })
}

describe('PersonView：博主 / 模特双 Tab', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    mocks.routeQuery = {}
  })

  it('默认展示穿搭博主', async () => {
    const wrapper = mountPerson()
    await flushPromises()

    const vm = wrapper.vm as unknown as { activeKind: string }
    expect(vm.activeKind).toBe('blogger')
    expect(wrapper.findComponent({ name: 'PersonListSection' }).props('kind')).toBe('blogger')
  })

  it('从 URL 恢复模特 Tab（刷新/从详情返回后保持）', async () => {
    mocks.routeQuery = { kind: 'model' }
    const wrapper = mountPerson()
    await flushPromises()

    const vm = wrapper.vm as unknown as { activeKind: string }
    expect(vm.activeKind).toBe('model')
    expect(wrapper.findComponent({ name: 'PersonListSection' }).props('kind')).toBe('model')
  })

  it('非法 kind 回退到博主（不渲染空白）', async () => {
    mocks.routeQuery = { kind: '乱写的' }
    const wrapper = mountPerson()
    await flushPromises()

    const vm = wrapper.vm as unknown as { activeKind: string }
    expect(vm.activeKind).toBe('blogger')
  })

  it('切 Tab 时把 kind 写进 URL，并保留其它 query', async () => {
    mocks.routeQuery = { q: '张三', page: '2' }
    const wrapper = mountPerson()
    await flushPromises()

    const vm = wrapper.vm as unknown as { activeKind: string }
    vm.activeKind = 'model'
    await flushPromises()

    expect(mocks.routerReplace).toHaveBeenCalledWith({
      path: '/persons',
      query: expect.objectContaining({ kind: 'model', q: '张三', page: '2' }),
    })
  })

  it('「人脸库扫描」按钮跳到扫描页', async () => {
    const wrapper = mountPerson()
    await flushPromises()

    const btn = wrapper.findAll('button').find((b) => b.text().includes('人脸库扫描'))
    expect(btn, '应有扫描入口按钮').toBeTruthy()
    await btn!.trigger('click')

    expect(mocks.routerPush).toHaveBeenCalledWith('/face-scan')
  })
})

describe('ModelManageView：五个面板 Tab', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    mocks.routeQuery = {}
  })

  it('挂载时拉取模型列表', async () => {
    mountModels()
    await flushPromises()
    expect(mocks.refreshModels).toHaveBeenCalled()
  })

  it('默认展示「模型列表」页，且不把 tab 写进 URL', async () => {
    const wrapper = mountModels()
    await flushPromises()

    const vm = wrapper.vm as unknown as { activeTab: string }
    expect(vm.activeTab).toBe('models')
    expect(mocks.routerReplace).not.toHaveBeenCalled() // 没切换就不写 URL
  })

  it('从 URL 恢复页签；非法值回退 models', async () => {
    mocks.routeQuery = { tab: 'quality' }
    let wrapper = mountModels()
    await flushPromises()
    expect((wrapper.vm as unknown as { activeTab: string }).activeTab).toBe('quality')
    wrapper.unmount()

    mocks.routeQuery = { tab: '不存在' }
    wrapper = mountModels()
    await flushPromises()
    expect((wrapper.vm as unknown as { activeTab: string }).activeTab).toBe('models')
  })

  it('切到非默认页写 tab，切回默认页删 tab 但保留其它 query', async () => {
    mocks.routeQuery = { model: 'qwen3-vl:8b' }
    const wrapper = mountModels()
    await flushPromises()

    const vm = wrapper.vm as unknown as { activeTab: string }
    vm.activeTab = 'queue'
    await flushPromises()
    expect(mocks.routerReplace).toHaveBeenCalledWith({
      query: expect.objectContaining({ tab: 'queue', model: 'qwen3-vl:8b' }),
    })

    mocks.routerReplace.mockClear()
    vm.activeTab = 'models'
    await flushPromises()
    const query = mocks.routerReplace.mock.calls.at(-1)?.[0]?.query ?? {}
    expect(query.tab).toBeUndefined()
    expect(query.model).toBe('qwen3-vl:8b')
  })
})

describe('TaskManageView：拉取 + 轮询生命周期', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    mocks.routeQuery = {}
  })

  it('挂载时加载任务并启动轮询', async () => {
    mountTasks()
    await flushPromises()

    expect(mocks.loadTasks).toHaveBeenCalled()
    expect(mocks.startPoll).toHaveBeenCalled() // 有活动任务时要能实时更新
  })

  it('离开页面停止轮询（否则页面关了还在打接口）', async () => {
    const wrapper = mountTasks()
    await flushPromises()

    wrapper.unmount()

    expect(mocks.stopPoll).toHaveBeenCalled()
  })

  it('类型筛选选项与任务类型映射表同源（新增类型不会漏配）', async () => {
    const wrapper = mountTasks()
    await flushPromises()

    const vm = wrapper.vm as unknown as {
      typeOptions: { label: string; value: string }[]
    }
    expect(vm.typeOptions[0]).toEqual({ label: '全部类型', value: '' })
    // 至少覆盖到常见类型，且都是非空中文标签
    const values = vm.typeOptions.slice(1).map((o) => o.value)
    expect(values).toContain('batch_analyze')
    expect(values).toContain('image_gen')
    for (const option of vm.typeOptions.slice(1)) {
      expect(option.label).toBeTruthy()
    }
  })

  it('状态筛选选项包含排队/运行/成功/失败', async () => {
    const wrapper = mountTasks()
    await flushPromises()

    const vm = wrapper.vm as unknown as {
      statusOptions: { label: string; value: string }[]
    }
    const values = vm.statusOptions.map((o) => o.value)
    expect(values).toEqual(expect.arrayContaining(['', 'pending', 'running', 'success', 'failed']))
  })
})
