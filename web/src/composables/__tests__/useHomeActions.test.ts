/**
 * 素材库首页动作（`useHomeActions`）的测试。
 *
 * 这些处理器是首页所有「点一下就改数据」的入口（删除 / 收藏 / 评分 / 批量操作 /
 * 质量审核 / 保存智能合集），原覆盖率仅 10.6%。它们出错的后果都是**静默的数据不一致**：
 * - 移入垃圾桶后当前页空了要回退一页，否则用户停在空白页；
 * - 批量收藏只更新了部分 ID 时必须重拉校正，否则 UI 显示收藏成功、实际没收藏；
 * - 「已拒绝」筛选下标记通过要把条目从列表剔除并减总数；
 * - 保存智能合集没筛选条件时应拦截（无条件 = 匹配全库，无意义且易误解）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

const mocks = vi.hoisted(() => ({
  batchQualityCheck: vi.fn(),
  updateQualityStatus: vi.fn(),
  createCollection: vi.fn(),
  buildSmartQuery: vi.fn(() => '{"query":"smart"}'),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  notification: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}))

vi.mock('@/api/inspirations', () => ({
  batchQualityCheck: mocks.batchQualityCheck,
  updateQualityStatus: mocks.updateQualityStatus,
}))

vi.mock('@/api/collections', () => ({ createCollection: mocks.createCollection }))

vi.mock('@/utils/collectionQuery', () => ({ buildSmartQuery: mocks.buildSmartQuery }))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message, Notification: mocks.notification }
})

import { useHomeActions } from '../useHomeActions'

function item(id: string, overrides: Record<string, unknown> = {}) {
  return {
    id,
    is_favorite: false,
    quality_status: 'pending',
    quality_reason: 'x',
    ...overrides,
  } as { id: string; is_favorite: boolean; quality_status: string; quality_reason: string | null }
}

function setup(
  overrides: { filtersActive?: boolean; qualityFilter?: string; statusFilter?: string } = {},
) {
  const store = {
    items: [item('a'), item('b')],
    total: 2,
    remove: vi.fn(async () => {}),
    toggleFavorite: vi.fn(async () => {}),
    setRating: vi.fn(async () => {}),
  }
  const tagsStore = { groups: [{ tags: [{ id: 7, name: '法式' }] }] }
  const selectedIds = ref<Set<string>>(new Set())
  const batch = {
    batchFavorite: vi.fn(async () => 0),
    batchTrash: vi.fn(async () => 0),
    batchAddTags: vi.fn(async () => {}),
    batchLinkBloggers: vi.fn(async () => {}),
    batchUpdate: vi.fn(async () => 0),
    exitBatchMode: vi.fn(),
    selectedIds,
  }
  const filters = {
    filtersActive: ref(overrides.filtersActive ?? true),
    sourceFilter: ref('scraper'),
    mediaFilter: ref('image'),
    statusFilter: ref(overrides.statusFilter ?? ''),
    qualityFilter: ref(overrides.qualityFilter ?? ''),
    selectedTags: ref(['法式']),
    colorFilter: ref(''),
    ratingMin: ref(''),
  }
  const currentPage = ref(2)
  const loadPage = vi.fn()
  const api = useHomeActions({
    store: store as never,
    tagsStore: tagsStore as never,
    batch: batch as never,
    filters: filters as never,
    currentPage,
    loadPage,
  })
  return { api, store, batch, filters, currentPage, loadPage, selectedIds }
}

describe('useHomeActions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.batchQualityCheck.mockResolvedValue({ count: 3 })
    mocks.updateQualityStatus.mockResolvedValue({})
    mocks.createCollection.mockResolvedValue({ id: 1 })
  })

  describe('保存为智能合集', () => {
    it('openSaveCollection 清空名称并打开弹窗', () => {
      const { api } = setup()
      api.saveCollectionName.value = '旧名'

      api.openSaveCollection()

      expect(api.saveCollectionName.value).toBe('')
      expect(api.saveCollectionOpen.value).toBe(true)
    })

    it('名称为空 / 无筛选条件时拦截', async () => {
      const { api } = setup()
      api.saveCollectionName.value = '   '
      await api.confirmSaveCollection()
      expect(mocks.message.warning).toHaveBeenCalledWith('请输入合集名称')

      const noFilter = setup({ filtersActive: false })
      noFilter.api.saveCollectionName.value = '我的合集'
      await noFilter.api.confirmSaveCollection()
      expect(mocks.message.warning).toHaveBeenCalledWith('请先设置筛选条件，再保存为智能合集')
      expect(mocks.createCollection).not.toHaveBeenCalled()
    })

    it('成功：序列化当前筛选（标签名→ID）并关闭弹窗', async () => {
      const { api } = setup()
      api.saveCollectionName.value = ' 法式合集 '

      await api.confirmSaveCollection()

      expect(mocks.buildSmartQuery).toHaveBeenCalledWith(
        expect.objectContaining({ source: 'scraper', media: 'image', tags: ['法式'] }),
        expect.any(Function),
      )
      // nameToId：按标签名解析成 id（供后端按 id 存智能查询）
      const nameToId = (
        mocks.buildSmartQuery.mock.calls.at(-1) as unknown as [unknown, (n: string) => number]
      )[1]
      expect(nameToId('法式')).toBe(7)
      expect(nameToId('不存在')).toBeUndefined()
      expect(mocks.createCollection).toHaveBeenCalledWith({
        name: '法式合集',
        query_json: '{"query":"smart"}',
      })
      expect(mocks.message.success).toHaveBeenCalledWith(
        '已创建智能合集「法式合集」，可在「收藏合集」页查看',
      )
      expect(api.saveCollectionOpen.value).toBe(false)
    })

    it('创建失败提示且弹窗保持打开（方便重试）', async () => {
      mocks.createCollection.mockRejectedValue(null)
      const { api } = setup()
      api.saveCollectionName.value = '合集'
      api.saveCollectionOpen.value = true

      await api.confirmSaveCollection()

      expect(mocks.message.error).toHaveBeenCalledWith('保存为合集失败')
      expect(api.saveCollectionOpen.value).toBe(true)
    })
  })

  describe('删除 / 移入垃圾桶', () => {
    it('单条删除：store.remove(原因) + 提示；当前页空了回退一页', async () => {
      const { api, store, loadPage } = setup()
      store.items = [] as never // 删除后本页空

      api.handleDelete('a')
      expect(api.pendingTrashIds.value).toEqual(['a'])
      expect(api.trashModalOpen.value).toBe(true)

      await api.confirmTrash('质量差')

      expect(store.remove).toHaveBeenCalledWith('a', '质量差')
      expect(mocks.message.success).toHaveBeenCalledWith('已移入垃圾桶')
      expect(loadPage).toHaveBeenCalledWith(1) // 2 → 1
      expect(api.trashModalOpen.value).toBe(false)
      expect(api.pendingTrashIds.value).toEqual([])
    })

    it('单条删除后本页非空则不回退页码', async () => {
      const { api, store, loadPage } = setup()
      store.items = [item('b')] as never

      api.handleDelete('a')
      await api.confirmTrash('重复')

      expect(loadPage).not.toHaveBeenCalled()
    })

    it('批量删除：batchTrash 后退出多选并刷新当前页；无删除则不刷新', async () => {
      const { api, batch, loadPage, selectedIds } = setup()
      selectedIds.value = new Set(['a', 'b'])
      batch.batchTrash.mockResolvedValue(2)

      api.handleBatchTrash()
      expect(api.pendingTrashIds.value).toEqual(['a', 'b'])
      await api.confirmTrash('不喜欢')

      expect(batch.batchTrash).toHaveBeenCalledWith('不喜欢')
      expect(batch.exitBatchMode).toHaveBeenCalled()
      expect(loadPage).toHaveBeenCalledWith(2)

      batch.batchTrash.mockResolvedValue(0)
      await api.confirmTrash('其他')
      expect(loadPage).toHaveBeenCalledTimes(1) // 没删成不刷新
    })

    it('批量删除弹窗：无选中项不打开；删除异常提示「操作失败」', async () => {
      const { api, store } = setup()
      api.handleBatchTrash()
      expect(api.trashModalOpen.value).toBe(false)

      api.handleDelete('a')
      store.remove.mockRejectedValue(new Error('500'))
      await api.confirmTrash('其他')
      expect(mocks.message.error).toHaveBeenCalledWith('操作失败')
    })

    it('弹窗打开但 ID 已清空时直接返回', async () => {
      const { api, store } = setup()
      await api.confirmTrash('其他')
      expect(store.remove).not.toHaveBeenCalled()
    })
  })

  describe('收藏 / 评分', () => {
    it('toggleFavorite 失败提示，成功静默', async () => {
      const { api, store } = setup()
      await api.handleToggleFavorite('a')
      expect(store.toggleFavorite).toHaveBeenCalledWith('a')
      expect(mocks.message.error).not.toHaveBeenCalled()

      store.toggleFavorite.mockRejectedValue(new Error('500'))
      await api.handleToggleFavorite('a')
      expect(mocks.message.error).toHaveBeenCalledWith('操作失败')
    })

    it('评分成功/清除分别提示；失败提示后端原因', async () => {
      const { api, store } = setup()
      await api.handleRate('a', 4)
      expect(mocks.message.success).toHaveBeenCalledWith('已评分 4 星')

      await api.handleRate('a', 0)
      expect(mocks.message.success).toHaveBeenCalledWith('已清除评分')

      store.setRating.mockRejectedValue(null)
      await api.handleRate('a', 3)
      expect(mocks.message.error).toHaveBeenCalledWith('评分失败')
    })
  })

  describe('质量审核', () => {
    it('批量质量审核：无待审给 info；有则用通知引导去任务管理页', async () => {
      const { api } = setup()
      mocks.batchQualityCheck.mockResolvedValueOnce({ count: 0 })
      await api.handleBatchQualityCheck()
      expect(mocks.message.info).toHaveBeenCalledWith('没有待审核的素材')

      mocks.batchQualityCheck.mockResolvedValueOnce({ count: 5000 })
      await api.handleBatchQualityCheck()
      expect(mocks.batchQualityCheck).toHaveBeenCalledWith(expect.any(Number))
      expect(mocks.notification.success).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '已提交 5000 个素材进行质量审核',
          duration: 8000,
        }),
      )
      expect(api.checkingQuality.value).toBe(false)
    })

    it('批量质量审核失败提示且复位', async () => {
      mocks.batchQualityCheck.mockRejectedValue(new Error('500'))
      const { api } = setup()
      await api.handleBatchQualityCheck()
      expect(mocks.message.error).toHaveBeenCalledWith('质量审核提交失败')
      expect(api.checkingQuality.value).toBe(false)
    })

    it('标记通过：普通筛选下就地更新条目', async () => {
      const { api, store } = setup()
      await api.handleApprove('a')

      expect(mocks.updateQualityStatus).toHaveBeenCalledWith('a', 'approved')
      expect(mocks.message.success).toHaveBeenCalledWith('已标记为通过')
      const target = store.items.find((i) => i.id === 'a') as unknown as {
        quality_status: string
        quality_reason: string | null
      }
      expect(target.quality_status).toBe('approved')
      expect(target.quality_reason).toBeNull()
      expect(api.approvingIds.value.has('a')).toBe(false)
    })

    it('「已拒绝」筛选下标记通过：从列表剔除并减总数', async () => {
      const { api, store } = setup({ qualityFilter: 'rejected' })

      await api.handleApprove('a')

      expect(store.items.map((i) => i.id)).toEqual(['b'])
      expect(store.total).toBe(1)
    })

    it('标记通过失败提示且清理 loading 标记', async () => {
      mocks.updateQualityStatus.mockRejectedValue(new Error('500'))
      const { api } = setup()

      await api.handleApprove('a')

      expect(mocks.message.error).toHaveBeenCalledWith('操作失败')
      expect(api.approvingIds.value.has('a')).toBe(false)
    })
  })

  describe('批量操作', () => {
    it('批量收藏：全部更新成功时就地改状态并退出多选', async () => {
      const { api, store, batch, selectedIds } = setup()
      selectedIds.value = new Set(['a'])
      batch.batchFavorite.mockResolvedValue(1)

      await api.handleBatchFavorite(true)

      const target = store.items.find((i) => i.id === 'a')!
      expect(target.is_favorite).toBe(true)
      expect(batch.exitBatchMode).toHaveBeenCalled()
    })

    it('「收藏」筛选下批量取消：退出并重拉（避免残留）', async () => {
      const { api, batch, loadPage } = setup({ statusFilter: 'favorites' })
      batch.batchFavorite.mockResolvedValue(2)

      await api.handleBatchFavorite(false)

      expect(batch.exitBatchMode).toHaveBeenCalled()
      expect(loadPage).toHaveBeenCalledWith(2)
    })

    it('后端只更新部分 ID 时重拉校正 UI', async () => {
      const { api, batch, loadPage, selectedIds } = setup()
      selectedIds.value = new Set(['a', 'b'])
      batch.batchFavorite.mockResolvedValue(1) // 部分成功

      await api.handleBatchFavorite(true)

      expect(batch.exitBatchMode).toHaveBeenCalled()
      expect(loadPage).toHaveBeenCalled()
    })

    it('批量加标签/关联博主成功后退出多选并刷新', async () => {
      const { api, batch, loadPage } = setup()

      await api.handleBatchAddTags(['法式'])
      expect(batch.batchAddTags).toHaveBeenCalledWith(['法式'])
      expect(batch.exitBatchMode).toHaveBeenCalled()
      expect(loadPage).toHaveBeenCalledWith(2)

      await api.handleBatchAddBloggers([3])
      expect(batch.batchLinkBloggers).toHaveBeenCalledWith([3])
      expect(batch.exitBatchMode).toHaveBeenCalledTimes(2)
      expect(loadPage).toHaveBeenCalledTimes(2)
    })

    it('批量编辑：有更新才退出并刷新', async () => {
      const { api, batch, loadPage } = setup()
      batch.batchUpdate.mockResolvedValueOnce(0)
      await api.handleBatchUpdate({ quality_status: 'approved' } as never)
      expect(batch.exitBatchMode).not.toHaveBeenCalled()
      expect(loadPage).not.toHaveBeenCalled()

      batch.batchUpdate.mockResolvedValueOnce(3)
      await api.handleBatchUpdate({ quality_status: 'approved' } as never)
      expect(batch.exitBatchMode).toHaveBeenCalled()
      expect(loadPage).toHaveBeenCalledWith(2)
    })
  })
})
