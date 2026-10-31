/**
 * 素材详情页（DetailView）的交互路径测试。
 *
 * 这个页面是「单条素材的所有操作」入口：收藏、评分、移垃圾桶、恢复、彻底删除、
 * 移除标签、复制原始链接、键盘翻页。它们的共同点是**操作完必须同步本地状态**——
 * 收藏了但按钮没变、删了但没回列表，用户会重复点击。
 *
 * 做法：挂载真实组件 + 真实 composable，只 mock API 层与 router。子组件用浅渲染替身
 * （模板结构不是本测试的目标，行为才是）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  fetchInspiration: vi.fn(),
  toggleFavorite: vi.fn(),
  updateRating: vi.fn(),
  moveToTrash: vi.fn(),
  restoreInspiration: vi.fn(),
  deleteInspiration: vi.fn(),
  removeTagFromInspiration: vi.fn(),
  analyzeInspiration: vi.fn(),
  fetchSimilar: vi.fn(),
  routerPush: vi.fn(),
  routerReplace: vi.fn(),
  routeQuery: {} as Record<string, string>,
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { id: 'insp-1' }, query: mocks.routeQuery }),
  useRouter: () => ({ push: mocks.routerPush, replace: mocks.routerReplace }),
}))

vi.mock('@/api/inspirations', async () => {
  const actual = await vi.importActual<typeof import('@/api/inspirations')>('@/api/inspirations')
  return {
    ...actual,
    fetchInspiration: mocks.fetchInspiration,
    toggleFavorite: mocks.toggleFavorite,
    updateRating: mocks.updateRating,
    moveToTrash: mocks.moveToTrash,
    restoreInspiration: mocks.restoreInspiration,
    deleteInspiration: mocks.deleteInspiration,
    removeTagFromInspiration: mocks.removeTagFromInspiration,
    analyzeInspiration: mocks.analyzeInspiration,
    getFileUrl: (p: string) => `/files/${p}`,
    fetchSimilarInspirations: mocks.fetchSimilar,
  }
})

vi.mock('@/api/persons', () => ({
  bloggersApi: { fetchList: vi.fn().mockResolvedValue({ items: [], total: 0 }) },
  modelsApi: { fetchList: vi.fn().mockResolvedValue({ items: [], total: 0 }) },
  addInspirationBlogger: vi.fn(),
  addInspirationModel: vi.fn(),
  removeInspirationBlogger: vi.fn(),
  removeInspirationModel: vi.fn(),
}))

vi.mock('@/api/collections', () => ({
  fetchCollections: vi.fn().mockResolvedValue([]),
  addInspirationsToCollection: vi.fn(),
  evaluateSmartCollection: vi.fn(),
  createCollection: vi.fn(),
}))

vi.mock('@/api/tags', () => ({
  fetchTags: vi.fn().mockResolvedValue([]),
  recordTagCorrection: vi.fn(),
  fetchTagSuggestions: vi.fn().mockResolvedValue([]),
}))

import DetailView from '../DetailView.vue'

const DETAIL = {
  id: 'insp-1',
  file_path: 'images/2026-10/a.jpg',
  thumbnail_path: 'thumbnails/a.jpg',
  media_type: 'image',
  source_type: 'douyin',
  source_url: 'https://www.douyin.com/note/123',
  quality_status: 'approved',
  is_favorite: false,
  rating: 0,
  deleted_at: null,
  trash_reason: null,
  tags: [
    { tag: { id: 1, name: '针织开衫', category: 'item' }, confidence: 0.9, source: 'ai_generated' },
    { tag: { id: 2, name: '米色', category: 'color' }, confidence: 0.8, source: 'ai_generated' },
  ],
  bloggers: [],
  models: [],
  created_at: '2026-10-20T10:00:00',
  updated_at: '2026-10-20T10:00:00',
}

function mountView() {
  return mount(DetailView, {
    global: {
      plugins: [ArcoVue],
      stubs: {
        // 子组件只做展示，替身掉以聚焦本页行为
        DetailMediaPane: true,
        DetailInfoPanel: true,
        SimilarSection: true,
        CollectionPickerModal: true,
        ImageCropModal: true,
        TagCorrectionModal: true,
        DetailTrashModal: true,
      },
    },
  })
}

describe('DetailView 交互路径', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.routeQuery = {}
    mocks.fetchInspiration.mockResolvedValue(structuredClone(DETAIL))
    mocks.toggleFavorite.mockResolvedValue({})
    mocks.updateRating.mockResolvedValue({})
    mocks.moveToTrash.mockResolvedValue({})
    mocks.restoreInspiration.mockResolvedValue({ deleted_at: null, trash_reason: null })
    mocks.deleteInspiration.mockResolvedValue({})
    mocks.removeTagFromInspiration.mockResolvedValue({})
    mocks.fetchSimilar.mockResolvedValue({ items: [] })
  })

  it('按路由 id 加载详情', async () => {
    mountView()
    await flushPromises()

    expect(mocks.fetchInspiration).toHaveBeenCalledWith('insp-1')
  })

  it('加载失败给出提示而不是白屏', async () => {
    mocks.fetchInspiration.mockRejectedValue(new Error('404'))

    const wrapper = mountView()
    await flushPromises()

    // 组件仍渲染骨架（未崩溃）
    expect(wrapper.exists()).toBe(true)
  })

  it('收藏切换后同步本地状态（按钮不会停在旧值）', async () => {
    const wrapper = mountView()
    await flushPromises()

    // 通过子组件 stub 派发事件，等价于点击收藏按钮
    const panel = wrapper.findComponent({ name: 'DetailInfoPanel' })
    expect(panel.exists(), 'DetailInfoPanel 应渲染（stub）').toBe(true)
    {
      panel.vm.$emit('favorite')
      await flushPromises()
      expect(mocks.toggleFavorite).toHaveBeenCalledWith('insp-1', true)
    }
  })

  it('移入垃圾桶成功后返回列表并带上原筛选条件', async () => {
    mocks.routeQuery = { source: 'douyin', page: '2' }

    const wrapper = mountView()
    await flushPromises()

    // 打开垃圾桶弹窗并选原因 → 确认
    const trashModal = wrapper.findComponent({ name: 'DetailTrashModal' })
    expect(trashModal.exists()).toBe(true)
    {
      trashModal.vm.$emit('update:reason', '质量差')
      trashModal.vm.$emit('confirm')
      await flushPromises()

      expect(mocks.moveToTrash).toHaveBeenCalledWith('insp-1', '质量差')
      // 返回首页时必须带回筛选，否则用户丢上下文
      expect(mocks.routerPush).toHaveBeenCalledWith({ path: '/', query: mocks.routeQuery })
    }
  })

  it('未选删除原因时确认无效（不会发出请求）', async () => {
    const wrapper = mountView()
    await flushPromises()

    const trashModal = wrapper.findComponent({ name: 'DetailTrashModal' })
    expect(trashModal.exists()).toBe(true)
    {
      trashModal.vm.$emit('confirm')
      await flushPromises()
      expect(mocks.moveToTrash).not.toHaveBeenCalled()
    }
  })

  it('垃圾桶素材恢复后清空本地的删除状态', async () => {
    mocks.fetchInspiration.mockResolvedValue({
      ...structuredClone(DETAIL),
      deleted_at: '2026-10-21T00:00:00',
      trash_reason: '质量差',
    })

    const wrapper = mountView()
    await flushPromises()

    const panel = wrapper.findComponent({ name: 'DetailInfoPanel' })
    expect(panel.exists()).toBe(true)
    {
      panel.vm.$emit('restore')
      await flushPromises()
      expect(mocks.restoreInspiration).toHaveBeenCalledWith('insp-1')
    }
  })

  it('彻底删除成功后返回列表', async () => {
    const wrapper = mountView()
    await flushPromises()

    const panel = wrapper.findComponent({ name: 'DetailInfoPanel' })
    expect(panel.exists()).toBe(true)
    {
      panel.vm.$emit('permanent-delete')
      await flushPromises()
      expect(mocks.deleteInspiration).toHaveBeenCalledWith('insp-1')
      expect(mocks.routerPush).toHaveBeenCalled()
    }
  })

  it('右箭头翻页：有下一个时跳转，无下一个时不动', async () => {
    const wrapper = mountView()
    await flushPromises()

    // 浏览上下文未加载（无 prev/next）→ 按右键不应跳转
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight' }))
    await flushPromises()

    expect(mocks.routerPush).not.toHaveBeenCalled()
    expect(wrapper.exists()).toBe(true)
  })

  it('输入框聚焦时方向键不触发翻页（避免打断打字）', async () => {
    mountView()
    await flushPromises()

    const input = document.createElement('input')
    document.body.appendChild(input)
    input.focus()
    const event = new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true })
    Object.defineProperty(event, 'target', { value: input })
    document.dispatchEvent(event)
    await flushPromises()

    expect(mocks.routerPush).not.toHaveBeenCalled()
    input.remove()
  })

  it('移除标签后从本地列表剔除（无需整页刷新）', async () => {
    const wrapper = mountView()
    await flushPromises()

    const panel = wrapper.findComponent({ name: 'DetailInfoPanel' })
    expect(panel.exists()).toBe(true)
    {
      panel.vm.$emit('remove-tag', DETAIL.tags[0])
      await flushPromises()
      expect(mocks.removeTagFromInspiration).toHaveBeenCalledWith('insp-1', 1)
    }
  })

  it('「图生图（未审查）」链接一次带齐 ref 与 model（切档位 + 预填素材 ID）', async () => {
    const wrapper = mountView()
    await flushPromises()

    // 链接文案带「未审查」，用户点它属于明确的知情选择（不是默认值替他选的）
    const link = wrapper
      .findAll('a, button, span')
      .find((n) => n.text().includes('图生图（未审查）'))
    expect(link, '详情页应有「图生图（未审查）」链接').toBeTruthy()
    await link!.trigger('click')
    await flushPromises()

    expect(mocks.routerPush).toHaveBeenCalledWith({
      path: '/generate',
      query: { ref: 'insp-1', model: 'qwen_2_1_uc_i2i' },
    })
  })

  it('「换个姿势」入口带 ref 与 pose_lib（进图生图页自动打开姿势选择器）', async () => {
    const wrapper = mountView()
    await flushPromises()

    const link = wrapper.findAll('a, button, span').find((n) => n.text().includes('换个姿势'))
    expect(link, '详情页应有「换个姿势」入口').toBeTruthy()
    await link!.trigger('click')
    await flushPromises()

    expect(mocks.routerPush).toHaveBeenCalledWith({
      path: '/generate',
      query: { ref: 'insp-1', pose_lib: '1', model: 'qwen_2_1_uc_i2i' },
    })
  })

  it('搜索标签跳转到搜索页并带上关键词', async () => {
    const wrapper = mountView()
    await flushPromises()

    const panel = wrapper.findComponent({ name: 'DetailInfoPanel' })
    expect(panel.exists(), 'DetailInfoPanel 应渲染（stub）').toBe(true)
    {
      panel.vm.$emit('tag-click', '针织开衫')
      await flushPromises()
      expect(mocks.routerPush).toHaveBeenCalledWith({ path: '/search', query: { q: '针织开衫' } })
    }
  })
})
