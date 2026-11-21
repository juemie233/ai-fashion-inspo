/**
 * 数据治理页：完整性检查扫描器来源回填测试。
 *
 * 分支 feat/rust-media-worker 的 `GET /admin/integrity-check` 响应新增
 * `scanner: "rust" | "python"`，前端需要把它回填给完整性卡片展示：
 *
 * 1. 有 scanner 字段 → 原样透传（rust / python）；
 * 2. 没有 scanner 字段（旧后端、或 mock 不完整）→ 兜底 'python'；
 * 3. 空串（后端异常返回）→ 兜底 'python'（空串不能当「无标签」传下去）；
 * 4. 请求失败 → 提示「完整性检查失败」、不崩、checking 复位。
 *
 * 另外验证 loadIntegrity 的触发方式：当前实现由「重新检查」按钮和「清理孤立文件」
 * 后重新加载触发，onMounted 只加载统计与大文件列表（见下方已知落差用例）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ArcoVue from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  routerReplace: vi.fn(),
  routeQuery: {} as Record<string, string>,
  startAdminPolling: vi.fn(),
  stopAdminPolling: vi.fn(),
  resumeAdminTask: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: mocks.routeQuery }),
  useRouter: () => ({ replace: mocks.routerReplace, push: vi.fn() }),
}))

vi.mock('@/api/client', () => ({
  default: { get: mocks.get, post: mocks.post },
}))

vi.mock('@/composables/useAdminTask', async () => {
  const { ref: r } = await import('vue')
  return {
    useAdminTask: () => ({
      adminTask: r(null),
      startAdminPolling: mocks.startAdminPolling,
      stopAdminPolling: mocks.stopAdminPolling,
      resumeAdminTask: mocks.resumeAdminTask,
    }),
  }
})

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import GovernanceView from '../GovernanceView.vue'

/** 完整性卡片替身：只记录父组件传入的 prop，并允许触发 recheck。 */
const IntegrityStub = defineComponent({
  name: 'AdminIntegrityCheck',
  props: {
    missingFiles: { type: Array, default: () => [] },
    orphanFiles: { type: Array, default: () => [] },
    orphanBytes: { type: Number, default: 0 },
    checking: { type: Boolean, default: false },
    scanner: { type: String, default: '' },
  },
  emits: ['recheck', 'cleanOrphans'],
  template: '<div data-test="integrity-stub" />',
})

const STUBS = {
  AdminTaskProgress: true,
  AdminProblemCards: true,
  AdminIntegrityCheck: IntegrityStub,
  AdminDuplicates: true,
  AdminNearDuplicates: true,
}

function statsPayload() {
  return {
    data: {
      total_count: 100,
      total_size_bytes: 1024,
      thumbnail_size_bytes: 0,
      images_size_bytes: 1024,
      untagged_count: 0,
      analysis_failed_count: 0,
      favorite_count: 0,
      total_tags: 0,
      tombstone_count: 0,
      by_source_type: [],
      by_media_type: [],
      by_analysis_status: [],
      by_month: [],
    },
  }
}

/** 完整性检查响应；extra 用来模拟「缺 scanner 字段 / 空串 / 未知值」等后端形态。 */
function integrityPayload(extra: Record<string, unknown> = {}) {
  return {
    data: {
      missing_files: [],
      orphan_files: [],
      orphan_total_size_bytes: 0,
      ...extra,
    },
  }
}

function mountView() {
  return mount(GovernanceView, {
    global: { plugins: [ArcoVue, createPinia()], stubs: STUBS },
  })
}

type ViewWrapper = ReturnType<typeof mountView>

function integrityChild(wrapper: ViewWrapper) {
  return wrapper.findComponent(IntegrityStub)
}

/** 模拟子组件点「重新检查」→ 父组件 loadIntegrity。 */
async function clickRecheck(wrapper: ViewWrapper) {
  const child = integrityChild(wrapper)
  expect(child.exists()).toBe(true)
  child.vm.$emit('recheck')
  await flushPromises()
}

describe('GovernanceView 完整性检查扫描器来源回填', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    // 进入页面就停在完整性 Tab，保证子组件替身已挂载
    mocks.routeQuery = { tab: 'integrity' }
    mocks.get.mockImplementation((url: string) => {
      if (url.startsWith('/admin/stats')) return Promise.resolve(statsPayload())
      if (url.startsWith('/admin/largest-files')) return Promise.resolve({ data: [] })
      if (url.startsWith('/admin/integrity-check')) {
        return Promise.resolve(integrityPayload({ scanner: 'rust' }))
      }
      if (url.startsWith('/admin/duplicates')) {
        return Promise.resolve({
          data: { duplicate_groups: [], duplicate_count: 0, wasted_bytes: 0 },
        })
      }
      return Promise.resolve({ data: {} })
    })
    mocks.post.mockResolvedValue({ data: { deleted_count: 2, freed_bytes: 1024 } })
  })

  it('挂载后只自动加载统计与大文件，完整性巡检需手动触发（当前实现）', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(mocks.get).toHaveBeenCalledWith('/admin/stats')
    expect(mocks.get).toHaveBeenCalledWith('/admin/largest-files?limit=20')
    expect(mocks.get).not.toHaveBeenCalledWith('/admin/integrity-check')
    // 尚未巡检时不能凭空显示来源标签：父组件把空串传给子组件，由子组件隐藏标签
    expect(integrityChild(wrapper).props('scanner')).toBe('')
  })

  // 任务书期望「mounted 时调用 loadIntegrity」，但当前 onMounted 只调 loadAll +
  // resumeAdminTask，完整性巡检由「重新检查」/「清理孤立文件」触发（mounted 不调用）。
  // 这里用 it.fails 记录该落差：若哪天 onMounted 补上巡检，本用例会转为失败并提醒删除标记。
  it.fails('（已知落差）mounted 时自动调用 loadIntegrity', async () => {
    mountView()
    await flushPromises()

    expect(mocks.get).toHaveBeenCalledWith('/admin/integrity-check')
  })

  it('重新检查返回 scanner=rust：回填并透传给完整性卡片', async () => {
    mocks.get.mockImplementation((url: string) => {
      if (url.startsWith('/admin/integrity-check')) {
        return Promise.resolve(
          integrityPayload({
            scanner: 'rust',
            missing_files: [{ file_path: 'storage/a.jpg', inspiration_ids: ['i-1'] }],
            orphan_files: [{ file_path: 'storage/b.jpg', size_bytes: 1024 }],
            orphan_total_size_bytes: 4096,
          }),
        )
      }
      return Promise.resolve({ data: [] })
    })

    const wrapper = mountView()
    await flushPromises()
    await clickRecheck(wrapper)

    expect(mocks.get).toHaveBeenCalledWith('/admin/integrity-check')
    const child = integrityChild(wrapper)
    expect(child.props('scanner')).toBe('rust')
    // 巡检结果同样要回填给卡片，且 loading 已复位
    expect(child.props('missingFiles')).toHaveLength(1)
    expect(child.props('orphanFiles')).toHaveLength(1)
    expect(child.props('orphanBytes')).toBe(4096)
    expect(child.props('checking')).toBe(false)
  })

  it('重新检查返回 scanner=python：原样透传', async () => {
    mocks.get.mockImplementation((url: string) => {
      if (url.startsWith('/admin/integrity-check')) {
        return Promise.resolve(integrityPayload({ scanner: 'python' }))
      }
      return Promise.resolve({ data: [] })
    })

    const wrapper = mountView()
    await flushPromises()
    await clickRecheck(wrapper)

    expect(integrityChild(wrapper).props('scanner')).toBe('python')
  })

  it('响应缺 scanner 字段：兜底传 python（兼容旧后端）', async () => {
    mocks.get.mockImplementation((url: string) => {
      if (url.startsWith('/admin/integrity-check')) {
        return Promise.resolve(integrityPayload())
      }
      return Promise.resolve({ data: [] })
    })

    const wrapper = mountView()
    await flushPromises()
    await clickRecheck(wrapper)

    expect(integrityChild(wrapper).props('scanner')).toBe('python')
  })

  it('响应 scanner 为空串：兜底传 python（空串不能当无标签）', async () => {
    mocks.get.mockImplementation((url: string) => {
      if (url.startsWith('/admin/integrity-check')) {
        return Promise.resolve(integrityPayload({ scanner: '' }))
      }
      return Promise.resolve({ data: [] })
    })

    const wrapper = mountView()
    await flushPromises()
    await clickRecheck(wrapper)

    expect(integrityChild(wrapper).props('scanner')).toBe('python')
  })

  it('重新检查请求失败：提示「完整性检查失败」、不崩、checking 复位', async () => {
    mocks.get.mockImplementation((url: string) => {
      if (url.startsWith('/admin/integrity-check')) {
        return Promise.reject(new Error('500'))
      }
      return Promise.resolve({ data: [] })
    })

    const wrapper = mountView()
    await flushPromises()
    await clickRecheck(wrapper)

    expect(mocks.message.error).toHaveBeenCalledWith('完整性检查失败')
    expect(wrapper.exists()).toBe(true)
    expect(integrityChild(wrapper).props('checking')).toBe(false)
    // 失败时不回填扫描器来源，标签保持隐藏
    expect(integrityChild(wrapper).props('scanner')).toBe('')
  })

  it('清理孤立文件后会重新巡检并回填 scanner', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as { cleanOrphans: () => Promise<void> }
    await vm.cleanOrphans()
    await flushPromises()

    expect(mocks.post).toHaveBeenCalledWith('/admin/cleanup-orphans')
    expect(mocks.get).toHaveBeenCalledWith('/admin/integrity-check')
    expect(integrityChild(wrapper).props('scanner')).toBe('rust')
  })
})
