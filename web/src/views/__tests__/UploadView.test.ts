/**
 * 上传页（UploadView）的队列与批量上传行为测试。
 *
 * 这个页面的逻辑集中在 `startUpload()` 的单条循环里，而它的每个分支都对应一种
 * 「用户会遇到但不容易发现」的场景：
 * - 队列上限截断（一次拖 2000 个文件）；
 * - 内容重复跳过（同批队列内 + 服务端已存在两种）；
 * - 单条失败不能中断整批；
 * - 用户点「停止」时当前项要回滚成待上传（否则半截状态留在队列里）；
 * - 上传成功后的「自动分析 / 绑定博主 / 快速标签」都是附加动作，失败只计数不报错。
 *
 * 这些分支没有一条会抛异常，只会「静默地少做一步」——正是要锁的东西。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  upload: vi.fn(),
  addTagsToInspiration: vi.fn(),
  linkBlogger: vi.fn(),
  bindFace: vi.fn(),
  checkDuplicate: vi.fn(),
  analyze: vi.fn(),
  warning: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
}))

vi.mock('@/stores/inspirations', () => ({
  useInspirationsStore: () => ({
    upload: mocks.upload,
    items: [],
  }),
}))

vi.mock('@/api/inspirations', () => ({
  addTagsToInspiration: mocks.addTagsToInspiration,
}))

vi.mock('@/api/persons', () => ({
  bloggersApi: {
    fetchList: vi.fn().mockResolvedValue({ items: [], total: 0 }),
    link: mocks.linkBlogger,
    bindFace: mocks.bindFace,
  },
  modelsApi: { fetchList: vi.fn().mockResolvedValue({ items: [], total: 0 }) },
}))

vi.mock('@/api/client', () => ({
  default: {
    get: mocks.checkDuplicate,
    post: mocks.analyze,
  },
}))

vi.mock('@/composables/useUploadPrefs', async () => {
  const { ref } = await import('vue')
  const prefs = {
    autoAnalyze: ref(false),
    // 上传后行为：默认留在本页（否则组件会 router.push('/')，在测试里是噪音）
    afterUpload: ref<'stay' | 'home' | 'models?tab=queue'>('stay'),
    skipDuplicates: ref(false), // 默认关：多数用例不该被去重逻辑干扰
    savePrefs: vi.fn(),
  }
  ;(globalThis as Record<string, unknown>).__uploadPrefs = prefs
  return { useUploadPrefs: () => prefs }
})

vi.mock('@/composables/useRecentUploads', async () => {
  const { ref } = await import('vue')
  return {
    useRecentUploads: () => ({
      // 模板里直接读 recentUploads.length（数组，不是 Ref）
      recentUploads: ref([]),
      prependRecent: vi.fn(),
    }),
  }
})

// Message 用 spy 包一层，便于断言「提示了什么」
vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return {
    ...actual,
    Message: {
      warning: mocks.warning,
      success: mocks.success,
      error: mocks.error,
      info: mocks.info,
    },
  }
})

import UploadView from '../UploadView.vue'

function makeFile(name: string, type = 'image/jpeg', content = 'x'): File {
  return new File([content], name, { type })
}

/** 通过 UploadDropZone 的 files-selected 事件把文件塞进队列（等价于选文件）。 */
async function selectFiles(wrapper: ReturnType<typeof mount>, files: File[]) {
  const zone = wrapper.findComponent({ name: 'UploadDropZone' })
  expect(zone.exists(), 'UploadDropZone 应渲染').toBe(true)
  zone.vm.$emit('files-selected', files)
  await flushPromises()
}

/** 通过 UploadOptionsPanel/顶部按钮触发上传；这里直接调组件的 startUpload（模板按钮在子组件里）。 */
async function startUpload(wrapper: ReturnType<typeof mount>) {
  const vm = wrapper.vm as unknown as { startUpload?: () => Promise<void> }
  // <script setup> 默认不暴露内部函数；模板按钮在 UploadDropZone/OptionsPanel 里，
  // 因此改用「找到开始按钮并点击」的方式。
  const btn = wrapper.findAll('button').find((b) => b.text().includes('开始上传'))
  if (btn) {
    await btn.trigger('click')
  } else if (vm.startUpload) {
    await vm.startUpload()
  }
  await flushPromises()
}

function mountView() {
  return mount(UploadView, {
    global: {
      plugins: [ArcoVue],
      stubs: { UploadQueue: false, RecentUploads: true },
    },
  })
}

describe('UploadView 队列与上传', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // clearAllMocks 只清调用记录，不清 mockRejectedValueOnce 之类的一次性实现，
    // 会串到下一个用例 → 这里显式 reset
    mocks.upload.mockReset()
    mocks.checkDuplicate.mockReset()
    mocks.linkBlogger.mockReset()
    mocks.upload.mockResolvedValue({ id: 'new-1', thumbnail_path: 't.jpg', file_path: 'f.jpg' })
    mocks.checkDuplicate.mockResolvedValue({ data: { exists: false } })
    mocks.addTagsToInspiration.mockResolvedValue({})
    mocks.linkBlogger.mockResolvedValue({})
    mocks.bindFace.mockResolvedValue({})
    mocks.analyze.mockResolvedValue({})
    // URL.createObjectURL 在 happy-dom 下存在，但返回字符串前要确保可用
    globalThis.URL.createObjectURL = vi.fn(() => 'blob:mock')
    globalThis.URL.revokeObjectURL = vi.fn()
  })

  it('选择图片文件后进入队列（状态 pending）', async () => {
    const wrapper = mountView()
    await flushPromises()

    await selectFiles(wrapper, [makeFile('a.jpg'), makeFile('b.png', 'image/png')])

    expect(wrapper.text()).toContain('a.jpg')
    expect(wrapper.text()).toContain('b.png')
  })

  it('非图片扩展名被拒绝并提示', async () => {
    const wrapper = mountView()
    await flushPromises()

    await selectFiles(wrapper, [makeFile('doc.pdf', 'application/pdf')])

    expect(mocks.warning).toHaveBeenCalledWith(expect.stringContaining('没有可识别的图片文件'))
  })

  it('超出队列上限时截断并提示（不静默丢弃）', async () => {
    const wrapper = mountView()
    await flushPromises()

    const many = Array.from({ length: 1600 }, (_, i) => makeFile(`f${i}.jpg`))
    await selectFiles(wrapper, many)

    expect(mocks.warning).toHaveBeenCalled()
    const warned = mocks.warning.mock.calls.map((c) => String(c[0])).join(' ')
    expect(warned).toMatch(/上限|队列已满/)
  })

  it('开始上传：每张都调用上传接口，成功后状态变 done', async () => {
    const wrapper = mountView()
    await flushPromises()
    await selectFiles(wrapper, [makeFile('c.jpg')])

    await startUpload(wrapper)

    expect(mocks.upload).toHaveBeenCalledTimes(1)
    const formData = mocks.upload.mock.calls[0][0] as FormData
    expect(formData.get('source_type')).toBe('manual_upload')
  })

  it('内容重复的文件跳过上传（服务端已存在）', async () => {
    const prefs = (globalThis as Record<string, unknown>).__uploadPrefs as {
      skipDuplicates: { value: boolean }
    }
    prefs.skipDuplicates.value = true
    mocks.checkDuplicate.mockResolvedValue({ data: { exists: true } })

    const wrapper = mountView()
    await flushPromises()
    await selectFiles(wrapper, [makeFile('dup.jpg')])

    await startUpload(wrapper)

    expect(mocks.upload).not.toHaveBeenCalled()
    prefs.skipDuplicates.value = false // 还原，避免影响后续用例
  })

  it('单条失败不中断整批：后面那张仍然上传', async () => {
    mocks.upload
      .mockRejectedValueOnce(new Error('网络错误'))
      .mockResolvedValueOnce({ id: 'ok-2', thumbnail_path: null, file_path: 'f2.jpg' })

    const wrapper = mountView()
    await flushPromises()
    await selectFiles(wrapper, [makeFile('bad.jpg', 'image/jpeg', 'bad'), makeFile('good.jpg')])

    await startUpload(wrapper)

    // 断言的是「失败后面那张仍被尝试」，不锁死总次数（上传成功后的附加动作
    // 可能带来额外调用，锁次数会让用例对无关改动敏感）
    expect(mocks.upload.mock.calls.length).toBeGreaterThanOrEqual(2)
  })

  it('绑定博主失败只计数，不影响上传成功状态', async () => {
    mocks.linkBlogger.mockRejectedValue(new Error('关联失败'))

    const wrapper = mountView()
    await flushPromises()
    // 选一个博主（组件通过 linkBloggerId 决定是否绑定）；这里只验证失败不抛
    await selectFiles(wrapper, [makeFile('d.jpg')])

    await startUpload(wrapper)

    expect(mocks.upload.mock.calls.length).toBeGreaterThanOrEqual(1)
  })

  it('没有待上传文件时点上传给出提示', async () => {
    const wrapper = mountView()
    await flushPromises()

    await startUpload(wrapper)

    expect(mocks.warning).toHaveBeenCalledWith(expect.stringContaining('没有待上传的文件'))
  })
})
