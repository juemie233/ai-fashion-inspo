/**
 * 人物详情核心逻辑（`usePersonDetail`）的测试（核心 466 行此前未测）。
 *
 * 它是详情页的数据编排：路由参数 → 详情/素材/照片组；出错的表现是
 * **页面结构在但内容空**或**删除后停在空页**：
 * - 非法 id 必须回列表（不能 404 误报）；主页链接只允许 http(s)（防 javascript: 注入）；
 * - 素材/作品/筛选/排序/翻页的分页分母保持一致；作品视图首次进入才拉数据；
 * - 单张删除后「当前页最后一张」要回退一页；人脸注销跨页勾选、当前页全选判定；
 * - 照片组灯箱、删除组、跳转添加照片；返回列表要携带列表上下文（页码/搜索/平台）；
 * - 详情统计刷新失败静默（不阻塞主内容）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  router: { push: vi.fn(), replace: vi.fn(), back: vi.fn() },
  state: {
    params: { id: '7' } as Record<string, string>,
    query: { kind: 'blogger' } as Record<string, string>,
  },
  fetchDetail: vi.fn(),
  fetchInspirations: vi.fn(),
  fetchWorks: vi.fn(),
  remove: vi.fn(),
  unbindInspirations: vi.fn(),
  fetchModelPhotoSets: vi.fn(),
  fetchModelPhotoSet: vi.fn(),
  deleteModelPhotoSet: vi.fn(),
  moveToTrash: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))

vi.mock('vue-router', () => ({
  useRoute: () => mocks.state,
  useRouter: () => mocks.router,
}))

vi.mock('@/api/persons', () => {
  const personApi = {
    fetchDetail: mocks.fetchDetail,
    fetchInspirations: mocks.fetchInspirations,
    fetchWorks: mocks.fetchWorks,
    remove: mocks.remove,
    unbindInspirations: mocks.unbindInspirations,
  }
  return {
    bloggersApi: personApi,
    modelsApi: personApi,
    fetchModelPhotoSets: mocks.fetchModelPhotoSets,
    fetchModelPhotoSet: mocks.fetchModelPhotoSet,
    deleteModelPhotoSet: mocks.deleteModelPhotoSet,
  }
})

vi.mock('@/api/inspirations', () => ({ moveToTrash: mocks.moveToTrash }))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { usePersonDetail } from '../usePersonDetail'

function inspiration(id: string, overrides: Record<string, unknown> = {}) {
  return {
    inspiration_id: id,
    file_path: `images/${id}.jpg`,
    thumbnail_path: `thumbs/${id}.jpg`,
    media_type: 'image',
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

function detailPayload(overrides: Record<string, unknown> = {}) {
  return {
    id: 7,
    name: '测试博主',
    profile_url: 'https://www.xiaohongshu.com/user/profile/abc',
    ...overrides,
  }
}

function setup() {
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
  const api = usePersonDetail()
  warn.mockRestore()
  return api
}

describe('usePersonDetail（核心）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.state.params = { id: '7' }
    mocks.state.query = { kind: 'blogger' }
    mocks.fetchDetail.mockResolvedValue(detailPayload())
    mocks.fetchInspirations.mockResolvedValue({ items: [inspiration('a')], total: 1 })
    mocks.fetchWorks.mockResolvedValue({ works: [], total: 0, total_items: 0 })
    mocks.fetchModelPhotoSets.mockResolvedValue({ items: [] })
    mocks.fetchModelPhotoSet.mockResolvedValue({ photos: [] })
    mocks.remove.mockResolvedValue({})
    mocks.unbindInspirations.mockResolvedValue({
      inspirations_unlinked: 2,
      face_detections_cleared: 3,
    })
  })

  describe('路由参数与安全', () => {
    it('kind/kindLabel/api 按 query 选择；默认博主', () => {
      const blogger = setup()
      expect(blogger.kind.value).toBe('blogger')
      expect(blogger.kindLabel.value).toBe('穿搭博主')

      mocks.state.query = { kind: 'model' }
      const model = setup()
      expect(model.kind.value).toBe('model')
      expect(model.kindLabel.value).toBe('职业模特')
    })

    it('主页链接只允许 http/https，其它协议与非法 URL 不可点', () => {
      const api = setup()

      api.detail.value = detailPayload() as never
      expect(api.isProfileUrlSafe.value).toBe(true)

      api.detail.value = detailPayload({ profile_url: 'javascript:alert(1)' }) as never
      expect(api.isProfileUrlSafe.value).toBe(false)

      api.detail.value = detailPayload({ profile_url: '不是URL' }) as never
      expect(api.isProfileUrlSafe.value).toBe(false)

      api.detail.value = detailPayload({ profile_url: null }) as never
      expect(api.isProfileUrlSafe.value).toBe(false)
    })
  })

  describe('加载与分页', () => {
    it('loadInspirations 带分页/排序/筛选并写入列表', async () => {
      const api = setup()
      api.filters.value = { quality_status: 'approved' } as never
      api.sort.value = 'oldest'

      await api.loadInspirations()

      expect(mocks.fetchInspirations).toHaveBeenCalledWith(7, 1, 30, 'oldest', {
        quality_status: 'approved',
      })
      expect(api.items.value).toHaveLength(1)
      expect(api.total.value).toBe(1)
      expect(api.itemsLoading.value).toBe(false)
    })

    it('素材加载失败提示且复位 loading', async () => {
      mocks.fetchInspirations.mockRejectedValue(new Error('500'))
      const api = setup()

      await api.loadInspirations()

      expect(mocks.message.error).toHaveBeenCalledWith('加载人物素材失败')
      expect(api.itemsLoading.value).toBe(false)
    })

    it('作品视图首次进入才拉数据；再次切换不重复请求', async () => {
      mocks.fetchWorks.mockResolvedValue({
        works: [{ work_id: 'w1' }],
        total: 1,
        total_items: 4,
      })
      const api = setup()

      await api.setViewMode('works')
      expect(mocks.fetchWorks).toHaveBeenCalledTimes(1)
      expect(api.works.value).toHaveLength(1)
      expect(api.worksTotalItems.value).toBe(4)

      await api.setViewMode('list')
      await api.setViewMode('works')
      expect(mocks.fetchWorks).toHaveBeenCalledTimes(1) // 已有数据不重拉
    })

    it('作品加载失败提示；翻页重新拉取', async () => {
      mocks.fetchWorks.mockRejectedValue(new Error('500'))
      const api = setup()
      await api.setViewMode('works')
      expect(mocks.message.error).toHaveBeenCalledWith('加载作品失败')

      mocks.fetchWorks.mockResolvedValue({ works: [], total: 0, total_items: 0 })
      await api.setWorksPage(2)
      expect(api.worksPage.value).toBe(2)
      expect(mocks.fetchWorks).toHaveBeenLastCalledWith(7, 2, 12)
    })

    it('改筛选/排序都回第 1 页并重新加载；翻页只改页码', async () => {
      const api = setup()
      await api.setPage(3)
      expect(api.page.value).toBe(3)

      await api.setFilters({ quality_status: 'pending' } as never)
      expect(api.page.value).toBe(1)
      expect(mocks.fetchInspirations).toHaveBeenLastCalledWith(7, 1, 30, 'newest', {
        quality_status: 'pending',
      })

      await api.setPage(2)
      await api.setSort('confidence')
      expect(api.page.value).toBe(1)
      // 筛选条件在排序切换时保留（页面语义：只换排序，不重置筛选）
      expect(mocks.fetchInspirations).toHaveBeenLastCalledWith(7, 1, 30, 'confidence', {
        quality_status: 'pending',
      })
    })
  })

  describe('详情加载', () => {
    it('非法 id：提示并回列表，不发请求', async () => {
      mocks.state.params = { id: 'abc' }
      const api = setup()

      await api.loadDetail()

      expect(mocks.message.error).toHaveBeenCalledWith('人物参数无效')
      expect(mocks.router.replace).toHaveBeenCalledWith('/persons')
      expect(mocks.fetchDetail).not.toHaveBeenCalled()
    })

    it('成功：拉详情后并发拉素材与照片组', async () => {
      const api = setup()

      await api.loadDetail()

      expect(api.detail.value?.name).toBe('测试博主')
      expect(mocks.fetchInspirations).toHaveBeenCalled()
      expect(mocks.fetchModelPhotoSets).toHaveBeenCalled()
      expect(api.loading.value).toBe(false)
    })

    it('详情失败提示且 loading 复位，不再拉列表', async () => {
      mocks.fetchDetail.mockRejectedValue(new Error('404'))
      const api = setup()

      await api.loadDetail()

      expect(mocks.message.error).toHaveBeenCalledWith('加载人物详情失败')
      expect(api.loading.value).toBe(false)
      expect(mocks.fetchInspirations).not.toHaveBeenCalled()
    })
  })

  describe('单张删除', () => {
    it('无目标时直接返回', async () => {
      const api = setup()
      await api.confirmTrash('其他')
      expect(mocks.moveToTrash).not.toHaveBeenCalled()
    })

    it('成功：删除、刷新素材与统计；当前页最后一张时回退一页', async () => {
      const api = setup()
      await api.loadInspirations()
      api.page.value = 2
      api.items.value = [inspiration('only') as never]
      api.openTrashModal('only')

      await api.confirmTrash('质量差')

      expect(mocks.moveToTrash).toHaveBeenCalledWith('only', '质量差')
      expect(mocks.message.success).toHaveBeenCalledWith('已移入垃圾桶')
      expect(api.page.value).toBe(1) // 删完回退
      expect(mocks.fetchInspirations).toHaveBeenCalledTimes(2)
      expect(mocks.fetchDetail).toHaveBeenCalledTimes(1)
      expect(api.trashModalOpen.value).toBe(false)
      expect(api.trashTargetId.value).toBeNull()
    })

    it('失败提示后端原因', async () => {
      mocks.moveToTrash.mockRejectedValue({ response: { data: { detail: '素材已删除' } } })
      const api = setup()
      api.openTrashModal('x')

      await api.confirmTrash('其他')

      expect(mocks.message.error).toHaveBeenCalledWith('素材已删除')
    })
  })

  describe('人脸注销多选', () => {
    it('进入/退出清空勾选，toggle 增删', () => {
      const api = setup()
      api.unbindSelectedIds.value = new Set(['old'])

      api.enterUnbindMode()
      expect(api.unbindMode.value).toBe(true)
      expect(api.unbindSelectedIds.value.size).toBe(0)

      api.toggleUnbindSelect('a')
      api.toggleUnbindSelect('b')
      expect([...api.unbindSelectedIds.value].sort()).toEqual(['a', 'b'])
      api.toggleUnbindSelect('a')
      expect([...api.unbindSelectedIds.value]).toEqual(['b'])

      api.exitUnbindMode()
      expect(api.unbindMode.value).toBe(false)
      expect(api.unbindSelectedIds.value.size).toBe(0)
    })

    it('当前页全选判定与全选/取消', async () => {
      const api = setup()
      mocks.fetchInspirations.mockResolvedValue({
        items: [inspiration('a'), inspiration('b')],
        total: 2,
      })
      await api.loadInspirations()

      expect(api.isCurrentPageAllSelected.value).toBe(false)
      api.toggleSelectCurrentPage()
      expect([...api.unbindSelectedIds.value].sort()).toEqual(['a', 'b'])
      expect(api.isCurrentPageAllSelected.value).toBe(true)

      api.toggleSelectCurrentPage()
      expect(api.unbindSelectedIds.value.size).toBe(0)
    })

    it('确定注销：非博主直接返回；未勾选警告；成功刷新并回退页码', async () => {
      mocks.state.query = { kind: 'model' }
      const modelApi = setup()
      await modelApi.confirmUnbindSelected()
      expect(mocks.unbindInspirations).not.toHaveBeenCalled()

      mocks.state.query = { kind: 'blogger' }
      const api = setup()
      await api.confirmUnbindSelected()
      expect(mocks.message.warning).toHaveBeenCalledWith('请先勾选要注销的素材')

      await api.loadInspirations()
      api.items.value = [inspiration('a') as never]
      api.page.value = 2
      api.unbindSelectedIds.value = new Set(['a'])
      await api.confirmUnbindSelected()

      expect(mocks.unbindInspirations).toHaveBeenCalledWith(7, ['a'])
      expect(mocks.message.success).toHaveBeenCalledWith(
        '已注销 1 个素材的绑定：解除归属 2 条、回退人脸记录 3 条',
      )
      expect(api.page.value).toBe(1)
      expect(api.unbindMode.value).toBe(false)
    })

    it('确定注销失败提示', async () => {
      mocks.unbindInspirations.mockRejectedValue({
        response: { data: { detail: '人脸服务不可用' } },
      })
      const api = setup()
      api.unbindSelectedIds.value = new Set(['a'])

      await api.confirmUnbindSelected()

      expect(mocks.message.error).toHaveBeenCalledWith('人脸服务不可用')
      expect(api.unbindSubmitting.value).toBe(false)
    })

    it('清空全部绑定：非博主返回；成功回第 1 页；失败提示', async () => {
      mocks.state.query = { kind: 'model' }
      const modelApi = setup()
      await modelApi.unbindAll()
      expect(mocks.unbindInspirations).not.toHaveBeenCalled()

      mocks.state.query = { kind: 'blogger' }
      const api = setup()
      await api.loadInspirations()
      api.page.value = 3
      await api.unbindAll()

      expect(api.page.value).toBe(1)
      expect(mocks.message.success).toHaveBeenCalledWith(
        '已清空全部素材绑定：解除 2 条关联、回退人脸记录 3 条',
      )
      expect(api.clearingAll.value).toBe(false)

      mocks.unbindInspirations.mockRejectedValue(null)
      await api.unbindAll()
      expect(mocks.message.error).toHaveBeenCalledWith('清空绑定失败')
    })
  })

  describe('照片组', () => {
    it('加载失败静默；成功写入列表', async () => {
      mocks.fetchModelPhotoSets.mockRejectedValueOnce(new Error('500'))
      const api = setup()
      await api.loadPhotoSets() // 静默
      expect(mocks.message.error).not.toHaveBeenCalled()

      mocks.fetchModelPhotoSets.mockResolvedValue({ items: [{ id: 1, name: '写真' }] })
      await api.loadPhotoSets()
      expect(api.photoSets.value).toHaveLength(1)
      expect(api.photoSetsLoading.value).toBe(false)
    })

    it('点组打开灯箱；加载失败提示', async () => {
      mocks.fetchModelPhotoSet.mockResolvedValue({
        photos: [{ file_path: 'person_photos/a.jpg' }],
      })
      const api = setup()
      await api.openPhotoSet({ id: 1, name: '写真' } as never)

      expect(api.photoLightboxOpen.value).toBe(true)
      expect(api.photoLightboxPaths.value).toEqual(['person_photos/a.jpg'])
      expect(api.photoLightboxName.value).toBe('写真')

      mocks.fetchModelPhotoSet.mockRejectedValue(new Error('500'))
      await api.openPhotoSet({ id: 1, name: '写真' } as never)
      expect(mocks.message.error).toHaveBeenCalledWith('加载照片组失败')
    })

    it('删除照片组：成功提示并刷新；失败提示', async () => {
      const api = setup()
      await api.handleDeletePhotoSet({ id: 2, name: '夏日' } as never)
      expect(mocks.deleteModelPhotoSet).toHaveBeenCalledWith(7, 2)
      expect(mocks.message.success).toHaveBeenCalledWith('已删除照片组「夏日」')

      mocks.deleteModelPhotoSet.mockRejectedValue({
        response: { data: { detail: '有照片，不能删' } },
      })
      await api.handleDeletePhotoSet({ id: 3, name: 'x' } as never)
      expect(mocks.message.error).toHaveBeenCalledWith('有照片，不能删')
    })
  })

  describe('导航与编辑', () => {
    it('跳转添加照片带 person_id；返回列表只带上下文键', () => {
      mocks.state.query = {
        kind: 'model',
        page: '2',
        q: '法式',
        platform: '小红书',
        sort: 'count',
        ignored: 'x',
      }
      const api = setup()

      api.goAddPhotos()
      expect(mocks.router.push).toHaveBeenCalledWith({
        path: '/model-photos',
        query: { person_id: 7 },
      })

      api.backToList()
      expect(mocks.router.push).toHaveBeenCalledWith({
        path: '/persons',
        query: { kind: 'model', page: '2', q: '法式', platform: '小红书', sort: 'count' },
      })

      api.goSearchByTag('法式')
      expect(mocks.router.push).toHaveBeenCalledWith({ path: '/search', query: { q: '法式' } })
    })

    it('删除人物：成功提示并回列表；失败提示', async () => {
      const api = setup()
      api.detail.value = detailPayload() as never

      await api.handleDelete()
      expect(mocks.remove).toHaveBeenCalledWith(7)
      expect(mocks.message.success).toHaveBeenCalledWith('已删除人物「测试博主」')
      expect(mocks.router.push).toHaveBeenCalledWith({
        path: '/persons',
        query: { kind: 'blogger' },
      })

      mocks.remove.mockRejectedValue({ response: { data: { detail: '有关联素材' } } })
      await api.handleDelete()
      expect(mocks.message.error).toHaveBeenCalledWith('有关联素材')
    })

    it('无详情时不发删除请求', async () => {
      const api = setup()
      await api.handleDelete()
      expect(mocks.remove).not.toHaveBeenCalled()
    })
  })

  describe('素材转换', () => {
    it('toInspirationOut 映射字段；灯箱只含图片且排除无路径项', async () => {
      const api = setup()

      expect(api.toInspirationOut(inspiration('a'))).toEqual({
        id: 'a',
        file_path: 'images/a.jpg',
        thumbnail_path: 'thumbs/a.jpg',
        media_type: 'image',
        is_favorite: false,
        created_at: '2026-01-01T00:00:00Z',
        tags: [],
        analysis_status: 'none',
      })

      mocks.fetchInspirations.mockResolvedValue({
        items: [
          inspiration('i'),
          inspiration('v', { media_type: 'video' }),
          inspiration('n', { file_path: '' }),
        ],
        total: 3,
      })
      await api.loadInspirations()
      expect(api.lightboxPaths.value).toEqual(['images/i.jpg'])
    })
  })
})
