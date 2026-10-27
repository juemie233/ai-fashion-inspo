/**
 * 数据治理页（GovernanceView）的编排测试。
 *
 * 这页上的操作**都是破坏性的**（批量删除、去重删文件、清孤立文件），所以页面逻辑的
 * 重点不是「能不能点」，而是三件事：
 *
 * 1. **后台任务完成后的文案必须诚实**：提交的 ID 已不在库里（列表陈旧导致的重复提交）时，
 *    要说「这批素材已不在库中」，而不是显示「已删除 0 个素材」——后者让人以为删除没生效，
 *    于是反复点。去重同理：0 个可删就说「未找到可删除的重复文件」。
 * 2. **完成即刷新**：删完要刷新统计与对应列表，否则界面还显示删掉的东西占的空间。
 * 3. **子页面走 URL**：刷新/分享后停留在原来的小页面；cleanup 是默认页且不写进 URL
 *    （保持地址干净），但要保留其它 query 参数。
 *
 * 子组件（各 Tab 面板、进度条）用替身，只验编排。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ArcoVue from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  routerReplace: vi.fn(),
  routeQuery: {} as Record<string, string>,
  adminTaskRef: { value: null as unknown },
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
  const adminTask = r(null)
  mocks.adminTaskRef = adminTask as unknown as { value: unknown }
  return {
    useAdminTask: () => ({
      adminTask,
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

const STUBS = {
  AdminTaskProgress: true,
  GovernanceCleanupTab: true,
  GovernanceIntegrityTab: true,
  GovernanceDuplicatesTab: true,
  GovernanceNearDupTab: true,
}

function mountView() {
  return mount(GovernanceView, {
    global: { plugins: [ArcoVue, createPinia()], stubs: STUBS },
  })
}

function statsPayload() {
  return {
    data: {
      total_inspirations: 100,
      total_size_bytes: 1024,
      storage_size_bytes: 2048,
      by_source: {},
    },
  }
}

describe('GovernanceView 编排', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    mocks.routeQuery = {}
    mocks.get.mockImplementation((url: string) => {
      if (url.startsWith('/admin/stats')) return Promise.resolve(statsPayload())
      if (url.startsWith('/admin/largest-files')) return Promise.resolve({ data: [] })
      if (url.startsWith('/admin/integrity-check')) {
        return Promise.resolve({
          data: { missing_files: [], orphan_files: [], orphan_total_size_bytes: 0 },
        })
      }
      if (url.startsWith('/admin/duplicates')) {
        return Promise.resolve({
          data: { duplicate_groups: [], duplicate_count: 0, wasted_bytes: 0 },
        })
      }
      return Promise.resolve({ data: {} })
    })
    mocks.post.mockResolvedValue({ data: { message: '任务已创建', task_id: 9 } })
  })

  it('首屏加载统计与最大文件，并接管上一次未完成的后台任务', async () => {
    mountView()
    await flushPromises()

    expect(mocks.get).toHaveBeenCalledWith('/admin/stats')
    expect(mocks.get).toHaveBeenCalledWith('/admin/largest-files?limit=20')
    expect(mocks.resumeAdminTask).toHaveBeenCalled() // 刷新页面后继续看进度
  })

  it('加载统计失败给出提示且不崩', async () => {
    mocks.get.mockRejectedValue(new Error('500'))

    const wrapper = mountView()
    await flushPromises()

    expect(mocks.message.error).toHaveBeenCalledWith('加载统计数据失败')
    expect(wrapper.exists()).toBe(true)
  })

  it('批量删除：提交任务后开始轮询进度', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as {
      batchDeleteByCondition: (c: string) => Promise<void>
    }
    await vm.batchDeleteByCondition('untagged')

    expect(mocks.post).toHaveBeenCalledWith('/admin/batch-delete', { condition: 'untagged' })
    expect(mocks.startAdminPolling).toHaveBeenCalledWith(9, expect.any(Function))
    expect(mocks.message.success).toHaveBeenCalledWith('任务已创建')
  })

  it('批量删除失败：提示且不留 loading', async () => {
    mocks.post.mockRejectedValue(new Error('boom'))
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as {
      batchDeleteByCondition: (c: string) => Promise<void>
      clearingUntagged: boolean
    }
    await vm.batchDeleteByCondition('untagged')

    expect(mocks.message.error).toHaveBeenCalledWith('批量删除失败')
    expect(vm.clearingUntagged).toBe(false)
  })

  it('删除完成：如实提示「已删除 N 个无标签素材」并刷新', async () => {
    const wrapper = mountView()
    await flushPromises()
    mocks.get.mockClear()

    const vm = wrapper.vm as unknown as { handleAdminTaskDone: () => void }
    mocks.adminTaskRef.value = {
      id: 1,
      type: 'batch_delete',
      status: 'success',
      result: { label: 'untagged', deleted_count: 12, freed_bytes: 2048 },
    }
    vm.handleAdminTaskDone()
    await flushPromises()

    expect(mocks.message.success).toHaveBeenCalledWith(
      expect.stringContaining('已删除 12 个无标签素材'),
    )
    expect(mocks.get).toHaveBeenCalledWith('/admin/stats') // 统计已刷新
  })

  it('删除完成但一条都没删：提示「已不在库中」而不是「已删除 0 个」', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as { handleAdminTaskDone: () => void }
    mocks.adminTaskRef.value = {
      id: 1,
      type: 'batch_delete',
      status: 'success',
      total: 5,
      result: { label: 'untagged', deleted_count: 0, freed_bytes: 0 },
    }
    vm.handleAdminTaskDone()

    expect(mocks.message.warning).toHaveBeenCalledWith(expect.stringContaining('已不在库中'))
    expect(mocks.message.success).not.toHaveBeenCalled()
  })

  it('去重完成：有删除时报告组数/文件数/释放空间，并刷新重复列表', async () => {
    const wrapper = mountView()
    await flushPromises()
    mocks.get.mockClear()

    const vm = wrapper.vm as unknown as { handleAdminTaskDone: () => void }
    mocks.adminTaskRef.value = {
      id: 2,
      type: 'deduplicate',
      status: 'success',
      result: { groups_processed: 3, files_deleted: 4, freed_bytes: 1024 * 1024 },
    }
    vm.handleAdminTaskDone()
    await flushPromises()

    expect(mocks.message.success).toHaveBeenCalledWith(expect.stringContaining('处理 3 组'))
    expect(mocks.get).toHaveBeenCalledWith('/admin/duplicates')
  })

  it('去重完成但无可删：提示「未找到可删除的重复文件」（info 而非 success）', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as { handleAdminTaskDone: () => void }
    mocks.adminTaskRef.value = {
      id: 2,
      type: 'deduplicate',
      status: 'success',
      result: { groups_processed: 0, files_deleted: 0, freed_bytes: 0 },
    }
    vm.handleAdminTaskDone()

    expect(mocks.message.info).toHaveBeenCalledWith('未找到可删除的重复文件')
    expect(mocks.message.success).not.toHaveBeenCalled()
  })

  it('去重删除：提交任务并开始轮询', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as { deduplicate: () => Promise<void> }
    await vm.deduplicate()

    expect(mocks.post).toHaveBeenCalledWith('/admin/deduplicate')
    expect(mocks.startAdminPolling).toHaveBeenCalledWith(9, expect.any(Function))
  })

  it('清理孤立文件：报告删除数与释放空间，并刷新完整性与统计', async () => {
    mocks.post.mockResolvedValue({ data: { deleted_count: 7, freed_bytes: 4096 } })
    const wrapper = mountView()
    await flushPromises()
    mocks.get.mockClear()

    const vm = wrapper.vm as unknown as { cleanOrphans: () => Promise<void> }
    await vm.cleanOrphans()
    await flushPromises()

    expect(mocks.message.success).toHaveBeenCalledWith(
      expect.stringContaining('已删除 7 个孤立文件'),
    )
    expect(mocks.get).toHaveBeenCalledWith('/admin/integrity-check')
    expect(mocks.get).toHaveBeenCalledWith('/admin/stats')
  })

  it('近似重复页删除：复用批量删除任务并轮询', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as {
      handleNearDuplicateDelete: (ids: string[]) => Promise<void>
    }
    await vm.handleNearDuplicateDelete(['a', 'b'])

    expect(mocks.post).toHaveBeenCalledWith('/admin/batch-delete', { ids: ['a', 'b'] })
    expect(mocks.startAdminPolling).toHaveBeenCalled()
  })

  it('子组件提交任务后由页面接管轮询', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as {
      handleTaskStarted: (id: number, msg: string) => void
    }
    vm.handleTaskStarted(77, '已提交删除任务')

    expect(mocks.message.success).toHaveBeenCalledWith('已提交删除任务')
    expect(mocks.startAdminPolling).toHaveBeenCalledWith(77, expect.any(Function))
  })

  it('子页面走 URL：cleanup 是默认页不写 tab，其它页写入并保留其它参数', async () => {
    mocks.routeQuery = { focus: 'x' }
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as { activeTab: string }
    vm.activeTab = 'integrity'
    await flushPromises()

    expect(mocks.routerReplace).toHaveBeenCalledWith({
      query: expect.objectContaining({ tab: 'integrity', focus: 'x' }),
    })

    mocks.routerReplace.mockClear()
    vm.activeTab = 'cleanup'
    await flushPromises()
    const query = mocks.routerReplace.mock.calls.at(-1)?.[0]?.query ?? {}
    expect(query.tab).toBeUndefined()
    expect(query.focus).toBe('x')
  })

  it('非法 tab 回退到 cleanup', async () => {
    mocks.routeQuery = { tab: '乱写的' }
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as { activeTab: string }
    expect(vm.activeTab).toBe('cleanup')
  })

  it('离开页面停止轮询（避免定时器泄漏）', async () => {
    const wrapper = mountView()
    await flushPromises()

    wrapper.unmount()

    expect(mocks.stopAdminPolling).toHaveBeenCalled()
  })
})
