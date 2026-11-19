/**
 * 收藏合集增删改（`useCollectionMutations`）的测试。
 *
 * 合集操作里有两类「不可逆或不可误解」的边界：
 * - **系统收藏根只读**：它是收藏按钮自动同步的根节点，改名/删除/固化都必须在前端拦下
 *   （后端也有硬保护，但前端不能把请求发出去再报错）；
 * - **抖音自动维护的合集不可改名**，智能合集删除/固化的文案必须讲清「素材不受影响」，
 *   否则用户不敢点。
 * 另外新建（一级/二级）、重命名、按 ID 快速加入的参数都要精确，刷新时机也要跟着动作走。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { computed, ref } from 'vue'

const mocks = vi.hoisted(() => ({
  createCollection: vi.fn(),
  updateCollection: vi.fn(),
  deleteCollection: vi.fn(),
  solidifyCollection: vi.fn(),
  addToCollection: vi.fn(),
  loadCollections: vi.fn(),
  loadContent: vi.fn(),
  modalConfirm: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))

vi.mock('@/api/collections', () => ({
  SYSTEM_MANUAL_COLLECTION_NAME: '系统入库手动收藏',
  createCollection: mocks.createCollection,
  updateCollection: mocks.updateCollection,
  deleteCollection: mocks.deleteCollection,
  solidifyCollection: mocks.solidifyCollection,
  addToCollection: mocks.addToCollection,
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message, Modal: { confirm: mocks.modalConfirm } }
})

import { useCollectionMutations } from '../useCollectionMutations'
import type { CollectionOut } from '@/api/collections'

const SYSTEM_ROOT_NAME = '系统入库手动收藏'

function makeCollection(overrides: Partial<CollectionOut> = {}): CollectionOut {
  return {
    id: 1,
    name: '合集A',
    description: null,
    kind: 'manual',
    position: 0,
    parent_id: null,
    auto_source: null,
    child_count: 0,
    cover_inspiration_id: null,
    cover_thumbnail_path: null,
    item_count: 0,
    query_json: null,
    created_at: '2026-11-01T10:00:00+00:00',
    updated_at: '2026-11-01T10:00:00+00:00',
    ...overrides,
  }
}

function setup(current: CollectionOut | null) {
  const currentRef = ref<CollectionOut | null>(current)
  const api = useCollectionMutations({
    collections: ref(current ? [current] : []),
    current: computed(() => currentRef.value),
    currentId: ref(current?.id ?? null),
    loadCollections: mocks.loadCollections,
    loadContent: mocks.loadContent,
  })
  return { api, currentRef }
}

interface ConfirmOptions {
  title: string
  content: string
  okText?: string
  okButtonProps?: { status?: string }
  onOk?: () => Promise<void>
}

function lastConfirmOptions(): ConfirmOptions {
  return mocks.modalConfirm.mock.calls.at(-1)?.[0] as ConfirmOptions
}

describe('useCollectionMutations', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.createCollection.mockResolvedValue(makeCollection({ id: 20 }))
    mocks.updateCollection.mockResolvedValue(makeCollection())
    mocks.deleteCollection.mockResolvedValue(undefined)
    mocks.solidifyCollection.mockResolvedValue(makeCollection({ kind: 'manual' }))
    mocks.addToCollection.mockResolvedValue({ added: 2 })
    mocks.loadCollections.mockResolvedValue(undefined)
    mocks.loadContent.mockResolvedValue(undefined)
    mocks.modalConfirm.mockImplementation((options: ConfirmOptions) => options)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  describe('新建与重命名弹窗', () => {
    it('新建手动合集：清空表单与父级', () => {
      const { api } = setup(null)
      api.renameTargetId.value = 3
      api.renameParentId.value = 2
      api.renameForm.name = '旧值'
      api.renameForm.description = '旧描述'

      api.openCreateManual()

      expect(api.renameModalOpen.value).toBe(true)
      expect(api.renameTargetId.value).toBeNull()
      expect(api.renameParentId.value).toBeNull()
      expect(api.renameForm.name).toBe('')
      expect(api.renameForm.description).toBe('')
    })

    it('新建二级收藏夹：记录父合集 id', () => {
      const { api } = setup(null)

      api.openCreateChild(9)

      expect(api.renameModalOpen.value).toBe(true)
      expect(api.renameTargetId.value).toBeNull()
      expect(api.renameParentId.value).toBe(9)
    })

    it('openRename：无当前合集时静默返回', () => {
      const { api } = setup(null)

      api.openRename()

      expect(api.renameModalOpen.value).toBe(false)
      expect(mocks.message.warning).not.toHaveBeenCalled()
    })

    it('openRename：系统收藏根不可编辑', () => {
      const { api } = setup(makeCollection({ id: 1, name: SYSTEM_ROOT_NAME }))

      api.openRename()

      expect(api.renameModalOpen.value).toBe(false)
      expect(mocks.message.warning).toHaveBeenCalledWith('系统收藏根由收藏按钮自动同步，不支持编辑')
    })

    it('openRename：抖音自动维护的合集名不可改', () => {
      const { api } = setup(makeCollection({ id: 2, auto_source: 'douyin' }))

      api.openRename()

      expect(api.renameModalOpen.value).toBe(false)
      expect(mocks.message.warning).toHaveBeenCalledWith('该收藏夹由抖音同步自动维护，名称不可修改')
    })

    it('openRename：普通合集回填名称/描述/父级', () => {
      const { api } = setup(
        makeCollection({ id: 5, name: '通勤', description: '上班穿搭', parent_id: 2 }),
      )

      api.openRename()

      expect(api.renameModalOpen.value).toBe(true)
      expect(api.renameTargetId.value).toBe(5)
      expect(api.renameParentId.value).toBe(2)
      expect(api.renameForm.name).toBe('通勤')
      expect(api.renameForm.description).toBe('上班穿搭')
    })
  })

  describe('确认新建 / 重命名', () => {
    it('名称为空（或纯空白）：警告且不发请求', async () => {
      const { api } = setup(null)
      api.openCreateManual()
      api.renameForm.name = '   '

      await api.confirmRename()

      expect(mocks.message.warning).toHaveBeenCalledWith('请输入合集名称')
      expect(mocks.createCollection).not.toHaveBeenCalled()
      expect(api.renameModalOpen.value).toBe(true)
    })

    it('新建一级合集：描述去空白后为 null，成功后关闭弹窗并刷新列表', async () => {
      const { api } = setup(null)
      api.openCreateManual()
      api.renameForm.name = ' 新合集 '
      api.renameForm.description = ' 描述 '

      await api.confirmRename()

      expect(mocks.createCollection).toHaveBeenCalledWith({
        name: '新合集',
        description: '描述',
        parent_id: null,
      })
      expect(mocks.message.success).toHaveBeenCalledWith('已创建合集')
      expect(api.renameModalOpen.value).toBe(false)
      expect(mocks.loadCollections).toHaveBeenCalledTimes(1)
    })

    it('新建二级收藏夹：parent_id 透传，描述空串收敛为 null', async () => {
      const { api } = setup(null)
      api.openCreateChild(3)
      api.renameForm.name = '夏季'
      api.renameForm.description = '   '

      await api.confirmRename()

      expect(mocks.createCollection).toHaveBeenCalledWith({
        name: '夏季',
        description: null,
        parent_id: 3,
      })
      expect(mocks.message.success).toHaveBeenCalledWith('已创建二级收藏夹')
    })

    it('重命名：按 id PATCH 名称与描述，成功后关闭并刷新', async () => {
      const { api } = setup(makeCollection({ id: 7, name: '旧名' }))
      api.openRename()
      api.renameForm.name = '新名'
      api.renameForm.description = '新描述'

      await api.confirmRename()

      expect(mocks.updateCollection).toHaveBeenCalledWith(7, {
        name: '新名',
        description: '新描述',
      })
      expect(mocks.message.success).toHaveBeenCalledWith('已保存')
      expect(api.renameModalOpen.value).toBe(false)
      expect(mocks.loadCollections).toHaveBeenCalledTimes(1)
    })

    it('保存失败：透出后端 detail，弹窗保持打开以便重试', async () => {
      mocks.createCollection.mockRejectedValue({
        response: { data: { detail: '同名合集已存在' } },
      })
      const { api } = setup(null)
      api.openCreateManual()
      api.renameForm.name = '重复名'

      await api.confirmRename()

      expect(mocks.message.error).toHaveBeenCalledWith('同名合集已存在')
      expect(api.renameModalOpen.value).toBe(true)
      expect(mocks.loadCollections).not.toHaveBeenCalled()
    })

    it('保存失败且无后端文案时回退「保存失败」', async () => {
      mocks.updateCollection.mockRejectedValue({})
      const { api } = setup(makeCollection({ id: 7 }))
      api.openRename()

      await api.confirmRename()

      expect(mocks.message.error).toHaveBeenCalledWith('保存失败')
    })
  })

  describe('删除合集', () => {
    it('无当前合集 / 系统收藏根：不发确认框', () => {
      const empty = setup(null)
      empty.api.confirmDelete()
      expect(mocks.modalConfirm).not.toHaveBeenCalled()

      setup(makeCollection({ name: SYSTEM_ROOT_NAME })).api.confirmDelete()
      expect(mocks.modalConfirm).not.toHaveBeenCalled()
      expect(mocks.message.warning).toHaveBeenCalledWith('系统收藏根不能删除')
    })

    it('智能合集：确认文案说明只删条件、素材不受影响', async () => {
      const { api } = setup(makeCollection({ id: 8, name: '智能A', kind: 'smart' }))

      api.confirmDelete()

      const options = lastConfirmOptions()
      expect(options.title).toBe('删除合集「智能A」？')
      expect(options.content).toBe('智能合集仅删除条件本身，素材不受任何影响。')
      expect(options.okText).toBe('删除')
      expect(options.okButtonProps).toEqual({ status: 'danger' })

      await options.onOk?.()

      expect(mocks.deleteCollection).toHaveBeenCalledWith(8)
      expect(mocks.message.success).toHaveBeenCalledWith('合集已删除')
      expect(mocks.loadCollections).toHaveBeenCalledTimes(1)
      expect(mocks.loadContent).toHaveBeenCalledTimes(1)
    })

    it('手动合集：确认文案说明只删关联、素材本体不受影响', () => {
      setup(makeCollection({ kind: 'manual' })).api.confirmDelete()

      expect(lastConfirmOptions().content).toBe(
        '仅删除合集与成员关联，素材本体与标签不受影响，此操作不可恢复。',
      )
    })

    it('删除请求失败：提示 detail，不刷新列表', async () => {
      mocks.deleteCollection.mockRejectedValue({ response: { data: { detail: '合集不存在' } } })
      const { api } = setup(makeCollection())

      api.confirmDelete()
      await lastConfirmOptions().onOk?.()

      expect(mocks.message.error).toHaveBeenCalledWith('合集不存在')
      expect(mocks.loadCollections).not.toHaveBeenCalled()
    })
  })

  describe('智能合集编辑与固化', () => {
    it('openEditSmart：非智能合集直接返回', () => {
      const { api } = setup(makeCollection({ kind: 'manual' }))

      api.openEditSmart()

      expect(api.smartEditorVisible.value).toBe(false)
      expect(api.editingSmart.value).toBeNull()
    })

    it('openEditSmart：系统收藏根（智能）条件不可编辑', () => {
      const { api } = setup(makeCollection({ name: SYSTEM_ROOT_NAME, kind: 'smart' }))

      api.openEditSmart()

      expect(api.smartEditorVisible.value).toBe(false)
      expect(mocks.message.warning).toHaveBeenCalledWith(
        '系统收藏根由收藏按钮自动同步，条件不可编辑',
      )
    })

    it('openEditSmart：智能合集带上编辑对象与 id 打开编辑器', () => {
      const smart = makeCollection({ id: 11, kind: 'smart' })
      const { api } = setup(smart)

      api.openEditSmart()

      expect(api.smartEditorVisible.value).toBe(true)
      // current 是 ref 内的响应式代理，不能和原始对象做引用相等比较
      expect(api.editingSmart.value?.id).toBe(11)
      expect(api.editingSmart.value?.kind).toBe('smart')
      expect(api.smartEditorId.value).toBe(11)
    })

    it('openCreateSmartWithId：清空编辑对象，走创建分支', () => {
      const { api } = setup(null)
      api.editingSmart.value = makeCollection({ kind: 'smart' })
      api.smartEditorId.value = 3

      api.openCreateSmartWithId()

      expect(api.smartEditorVisible.value).toBe(true)
      expect(api.editingSmart.value).toBeNull()
      expect(api.smartEditorId.value).toBeNull()
    })

    it('智能合集保存后刷新列表与内容', async () => {
      const { api } = setup(makeCollection({ kind: 'smart' }))

      await api.handleSmartSaved()

      expect(mocks.loadCollections).toHaveBeenCalledTimes(1)
      expect(mocks.loadContent).toHaveBeenCalledTimes(1)
    })

    it('confirmSolidify：非智能合集不发确认框', () => {
      setup(makeCollection({ kind: 'manual' })).api.confirmSolidify()

      expect(mocks.modalConfirm).not.toHaveBeenCalled()
    })

    it('confirmSolidify：系统收藏根不可转手动', () => {
      setup(makeCollection({ name: SYSTEM_ROOT_NAME, kind: 'smart' })).api.confirmSolidify()

      expect(mocks.modalConfirm).not.toHaveBeenCalled()
      expect(mocks.message.warning).toHaveBeenCalledWith('系统收藏根不能转手动，会失去自动同步能力')
    })

    it('confirmSolidify：确认后固化、提示并刷新', async () => {
      const { api } = setup(makeCollection({ id: 12, name: '极简', kind: 'smart' }))

      api.confirmSolidify()

      const options = lastConfirmOptions()
      expect(options.title).toBe('把「极简」转为手动合集？')
      expect(options.content).toContain('筛选条件将不再生效')

      await options.onOk?.()

      expect(mocks.solidifyCollection).toHaveBeenCalledWith(12)
      expect(mocks.message.success).toHaveBeenCalledWith('已转为手动合集')
      expect(mocks.loadCollections).toHaveBeenCalledTimes(1)
      expect(mocks.loadContent).toHaveBeenCalledTimes(1)
    })

    it('confirmSolidify 失败：提示「转换失败」且不刷新', async () => {
      mocks.solidifyCollection.mockRejectedValue({})
      const { api } = setup(makeCollection({ kind: 'smart' }))

      api.confirmSolidify()
      await lastConfirmOptions().onOk?.()

      expect(mocks.message.error).toHaveBeenCalledWith('转换失败')
      expect(mocks.loadCollections).not.toHaveBeenCalled()
    })
  })

  describe('按素材 ID 快速加入', () => {
    it('无当前合集或不是手动合集：不发请求', async () => {
      const { api } = setup(null)
      api.addIdInput.value = 'a1'
      await api.confirmAddByIds()
      expect(mocks.addToCollection).not.toHaveBeenCalled()

      const smart = setup(makeCollection({ kind: 'smart' }))
      smart.api.addIdInput.value = 'a1'
      await smart.api.confirmAddByIds()
      expect(mocks.addToCollection).not.toHaveBeenCalled()
    })

    it('输入为空：警告且不发请求', async () => {
      const { api } = setup(makeCollection())
      api.addIdInput.value = ' ,，  '

      await api.confirmAddByIds()

      expect(mocks.message.warning).toHaveBeenCalledWith('请输入至少一个素材 ID')
      expect(mocks.addToCollection).not.toHaveBeenCalled()
    })

    it('按逗号/中文逗号/空白切分 ID，成功后清空输入并刷新', async () => {
      const { api } = setup(makeCollection({ id: 3 }))
      api.addIdInput.value = 'a1, a2，a3  a4'
      api.addOpen.value = true

      await api.confirmAddByIds()

      expect(mocks.addToCollection).toHaveBeenCalledWith(3, ['a1', 'a2', 'a3', 'a4'])
      expect(mocks.message.success).toHaveBeenCalledWith('已加入')
      expect(api.addOpen.value).toBe(false)
      expect(api.addIdInput.value).toBe('')
      expect(mocks.loadContent).toHaveBeenCalledTimes(1)
      expect(mocks.loadCollections).toHaveBeenCalledTimes(1)
    })

    it('加入失败：透出 detail，输入保留以便重试', async () => {
      mocks.addToCollection.mockRejectedValue({ response: { data: { detail: '素材已删除' } } })
      const { api } = setup(makeCollection())
      api.addIdInput.value = 'a1'

      await api.confirmAddByIds()

      expect(mocks.message.error).toHaveBeenCalledWith('素材已删除')
      expect(api.addIdInput.value).toBe('a1')
      expect(mocks.loadContent).not.toHaveBeenCalled()
    })
  })
})
