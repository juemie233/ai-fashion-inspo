/** 收藏合集的增删改动作（从 CollectionsView.vue 抽出，C-2）。
 *
 * 覆盖：新建手动合集 / 重命名 / 删除（含智能合集提示）/ 新建与编辑智能合集 / 固化为手动 /
 * 按 ID 快速添加素材。只做 API 调用 + 局部 UI 状态；依赖由调用方以 opts 传入。
 */

import { reactive, ref, type ComputedRef, type Ref } from 'vue'
import { Message, Modal } from '@arco-design/web-vue'
import type { CollectionOut } from '@/api/collections'
import { addToCollection, deleteCollection, solidifyCollection } from '@/api/collections'
import { getApiErrorMessage } from '@/utils/apiError'

interface CollectionMutationDeps {
  collections: Ref<CollectionOut[]>
  current: ComputedRef<CollectionOut | null>
  currentId: Ref<number | null>
  loadCollections: () => Promise<void>
  loadContent: () => Promise<void>
}

export function useCollectionMutations(opts: CollectionMutationDeps) {
  const { current, loadCollections, loadContent } = opts

  const renameModalOpen = ref(false)
  const renameTargetId = ref<number | null>(null) // null = 新建手动合集
  // 新建时的父收藏夹：非空表示建二级（挂在选中的一级之下）
  const renameParentId = ref<number | null>(null)
  const renameForm = reactive({ name: '', description: '' })

  function openCreateManual() {
    renameTargetId.value = null
    renameParentId.value = null
    renameForm.name = ''
    renameForm.description = ''
    renameModalOpen.value = true
  }

  /** 在某个一级合集下新建二级收藏夹 */
  function openCreateChild(parentId: number) {
    renameTargetId.value = null
    renameParentId.value = parentId
    renameForm.name = ''
    renameForm.description = ''
    renameModalOpen.value = true
  }

  function openRename() {
    const c = current.value
    if (!c) return
    if (c.auto_source) {
      Message.warning('该收藏夹由抖音同步自动维护，名称不可修改')
      return
    }
    renameTargetId.value = c.id
    renameParentId.value = c.parent_id
    renameForm.name = c.name
    renameForm.description = c.description ?? ''
    renameModalOpen.value = true
  }

  async function confirmRename() {
    const name = renameForm.name.trim()
    if (!name) {
      Message.warning('请输入合集名称')
      return
    }
    try {
      const { updateCollection: update, createCollection: create } =
        await import('@/api/collections')
      if (renameTargetId.value === null) {
        await create({
          name,
          description: renameForm.description.trim() || null,
          parent_id: renameParentId.value,
        })
        Message.success(renameParentId.value === null ? '已创建合集' : '已创建二级收藏夹')
      } else {
        await update(renameTargetId.value, {
          name,
          description: renameForm.description.trim() || null,
        })
        Message.success('已保存')
      }
      renameModalOpen.value = false
      loadCollections()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '保存失败'))
    }
  }

  function confirmDelete() {
    const c = current.value
    if (!c) return
    const isSmart = c.kind === 'smart'
    Modal.confirm({
      title: `删除合集「${c.name}」？`,
      content: isSmart
        ? '智能合集仅删除条件本身，素材不受任何影响。'
        : '仅删除合集与成员关联，素材本体与标签不受影响，此操作不可恢复。',
      okText: '删除',
      okButtonProps: { status: 'danger' },
      onOk: async () => {
        try {
          await deleteCollection(c.id)
          Message.success('合集已删除')
          await loadCollections()
          loadContent()
        } catch (e) {
          Message.error(getApiErrorMessage(e, '删除合集失败'))
        }
      },
    })
  }

  // ── 智能合集：编辑条件 / 固化 ──

  const editingSmart = ref<CollectionOut | null>(null)
  const smartEditorVisible = ref(false)
  const smartEditorId = ref<number | null>(null)

  function openEditSmart() {
    const c = current.value
    if (!c || c.kind !== 'smart') return
    editingSmart.value = c
    smartEditorId.value = c.id
    smartEditorVisible.value = true
  }

  /** 从空条件创建智能合集（编辑器 collectionId=null 走创建分支） */
  function openCreateSmartWithId() {
    editingSmart.value = null
    smartEditorId.value = null
    smartEditorVisible.value = true
  }

  async function handleSmartSaved() {
    await loadCollections()
    loadContent()
  }

  function confirmSolidify() {
    const c = current.value
    if (!c || c.kind !== 'smart') return
    Modal.confirm({
      title: `把「${c.name}」转为手动合集？`,
      content: '将当前匹配到的素材固化为合集成员，之后可手动增删与排序；筛选条件将不再生效。',
      onOk: async () => {
        try {
          await solidifyCollection(c.id)
          Message.success('已转为手动合集')
          await loadCollections()
          loadContent()
        } catch (e) {
          Message.error(getApiErrorMessage(e, '转换失败'))
        }
      },
    })
  }

  // ── 手动合集：加入素材（快速添加：输入素材 ID） ──

  const addOpen = ref(false)
  const addIdInput = ref('')

  async function confirmAddByIds() {
    const c = current.value
    if (!c || c.kind !== 'manual') return
    const ids = addIdInput.value
      .split(/[,，\s]+/)
      .map((x) => x.trim())
      .filter(Boolean)
    if (ids.length === 0) {
      Message.warning('请输入至少一个素材 ID')
      return
    }
    try {
      await addToCollection(c.id, ids)
      Message.success('已加入')
      addOpen.value = false
      addIdInput.value = ''
      loadContent()
      loadCollections()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '加入失败'))
    }
  }

  // ── 手动合集：批量移出 ──

  return {
    renameModalOpen,
    renameTargetId,
    renameParentId,
    renameForm,
    openCreateManual,
    openCreateChild,
    openRename,
    confirmRename,
    confirmDelete,
    editingSmart,
    smartEditorVisible,
    smartEditorId,
    openEditSmart,
    openCreateSmartWithId,
    handleSmartSaved,
    confirmSolidify,
    addOpen,
    addIdInput,
    confirmAddByIds,
  }
}
